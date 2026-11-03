"""既存キーを変えず、独自種別の名前と非表示を管理する。"""

from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import BadRequestError, DuplicateResourceError, NotFoundError
from app.core.task_types import STANDARD_TASK_TYPES
from app.models.task_type import ProjectTaskType
from app.repositories.task_type import TaskTypeRepository
from app.schemas.task_type import (
    TaskTypeCreate,
    TaskTypeList,
    TaskTypeRead,
    TaskTypeUpdate,
)
from app.services.audit_log import AuditLogService
from app.services.conflict import (
    raise_duplicate_after_rollback,
    raise_if_version_conflict,
)


class TaskTypeService:
    """認可済みProject内でのみ種別を操作する。"""

    def __init__(self):
        self.repository = TaskTypeRepository()

    def read(self, row: ProjectTaskType) -> TaskTypeRead:
        """モデルを公開契約へ変換する。"""
        return TaskTypeRead(
            key=row.key,
            name=row.name,
            source="custom",
            is_active=row.is_active,
            version=row.version,
        )

    def list(self, db: Session, project_id: int) -> TaskTypeList:
        """非表示と旧値も返し、利用可否は明示する。"""
        items = [
            TaskTypeRead(key=k, name=v, source="standard")
            for k, v in STANDARD_TASK_TYPES.items()
        ]
        items.extend(self.read(row) for row in self.repository.list(db, [project_id]))
        keys = {item.key for item in items}
        items.extend(
            TaskTypeRead(key=key, name=key or "未設定", source="legacy")
            for key in self.repository.legacy_keys(db, project_id)
            if key not in keys
        )
        return TaskTypeList(items=items)

    def validate(self, db: Session, project_id: int, key: str):
        """標準・管理済み・既存の旧値に限定し、Task入力から管理を迂回させない。"""
        if key in STANDARD_TASK_TYPES:
            return
        row = self.repository.get(db, project_id, key, lock=True)
        if row is None and key in self.repository.legacy_keys(db, project_id):
            return
        if row is None or not row.is_active:
            raise BadRequestError(error_messages.TASK_TYPE_UNAVAILABLE)

    def save(
        self,
        db: Session,
        project_id: int,
        data: TaskTypeCreate | TaskTypeUpdate,
        actor_id: int,
        key: str | None = None,
    ) -> TaskTypeRead:
        """安定キーを変更せず、名前と表示状態を版付きで保存する。"""
        row = self.repository.get(db, project_id, key, lock=True) if key else None
        if key and row is None:
            raise NotFoundError(error_messages.TASK_TYPE_NOT_FOUND)
        if row is not None and isinstance(data, TaskTypeUpdate):
            raise_if_version_conflict(
                current_version=row.version,
                requested_version=data.version,
                current=self.read(row).model_dump(),
            )
        normalized = data.name.casefold()
        if normalized in {name.casefold() for name in STANDARD_TASK_TYPES.values()}:
            raise DuplicateResourceError(error_messages.TASK_TYPE_NAME_EXISTS)
        if row is None:
            row = ProjectTaskType(
                project_id=project_id,
                key="custom_" + uuid4().hex,
                name=data.name,
                name_key=normalized,
            )
            db.add(row)
        else:
            if not isinstance(data, TaskTypeUpdate):
                raise BadRequestError(error_messages.TASK_TYPE_UNAVAILABLE)
            row.name, row.name_key = data.name, normalized
            row.is_active = data.is_active
            row.version += 1
        try:
            db.flush()
        except IntegrityError as exc:
            raise_duplicate_after_rollback(
                db, error_messages.TASK_TYPE_NAME_EXISTS, exc
            )
        AuditLogService().record(
            db,
            event_type="task_type.saved",
            actor_user_id=actor_id,
            project_id=project_id,
            resource_id=row.id,
            metadata={
                "key": row.key,
                "version": row.version,
                "is_active": row.is_active,
            },
            resource_type="project_task_type",
            commit=False,
        )
        result = self.read(row)
        db.commit()
        return result
