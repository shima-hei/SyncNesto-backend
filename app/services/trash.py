"""既存RBACで保護するごみ箱と保持期限後の回収。"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.config import settings
from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    VersionConflictError,
)
from app.db.demo_scope import lock_active_demo
from app.models.document import ProjectDocument
from app.models.user import User
from app.repositories.document import DocumentRepository
from app.repositories.trash import RESOURCES, TrashRepository
from app.schemas.trash import TrashItem, TrashKind, TrashRead, TrashRestore
from app.services.audit_log import AuditLogService
from app.services.authorization import AuthorizationService
from app.services.conflict import raise_if_version_conflict
from app.services.storage import StorageService


class TrashService:
    """削除の世代と期限を検証し、親と子の個別削除を区別する。"""

    def __init__(self, storage: StorageService | None = None) -> None:
        """既存の認可・監査・ストレージを再利用する。"""
        self.repository = TrashRepository()
        self.authorization = AuthorizationService()
        self.audit = AuditLogService()
        self.storage = storage

    def permitted(
        self,
        db: Session,
        user: User,
        project_id: int,
        kind: str,
        *,
        restore: bool = False,
        permission_cache: dict[str, bool] | None = None,
    ) -> bool:
        """削除した本文を読める権限と、元の操作権限の両方を要求する。"""
        spec = RESOURCES[kind]
        operations = {"read", spec.operation}
        if restore:
            operations.add("update")
        cache = permission_cache if permission_cache is not None else {}
        for op in sorted(operations):
            code = f"{spec.permission}:{op}"
            if code not in cache:
                cache[code] = self.authorization.has_project_permission(
                    db,
                    user=user,
                    project_id=project_id,
                    permission_code=code,
                )
            if not cache[code]:
                return False
        return True

    def summary(self, data: dict, now: datetime) -> TrashItem:
        """期限を絶対日時で表示し、期限切れ・削除親を復元不可にする。"""
        days = settings.deleted_data_retention_days
        expires_at = data["deleted_at"] + timedelta(days=days) if days else None
        reason = None
        if expires_at is not None and expires_at <= now:
            reason = error_messages.TRASH_EXPIRED
        elif data.get("parent_deleted_at") is not None:
            reason = error_messages.TRASH_PARENT_DELETED
        return TrashItem(
            **{
                **data,
                "expires_at": expires_at,
                "can_restore": reason is None,
                "blocked_reason": reason,
            }
        )

    def list(
        self,
        db: Session,
        user: User,
        project_id: int,
        kind: TrashKind | None,
        q: str,
        page: int,
        page_size: int,
    ) -> TrashRead:
        """一覧の件数・名前にも復元と同じ権限境界を適用する。"""
        permission_cache: dict[str, bool] = {}
        kinds = [
            k
            for k in RESOURCES
            if (kind is None or k == kind)
            and self.permitted(
                db, user, project_id, k, permission_cache=permission_cache
            )
        ]
        rows, total = self.repository.list(db, project_id, kinds, q, page, page_size)
        items = [self.summary(row, datetime.now(UTC)) for row in rows]
        for item in items:
            if item.can_restore and not self.permitted(
                db,
                user,
                project_id,
                item.kind,
                restore=True,
                permission_cache=permission_cache,
            ):
                item.can_restore = False
                item.blocked_reason = error_messages.FORBIDDEN
        return TrashRead(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            retention_days=settings.deleted_data_retention_days,
        )

    def restore(
        self,
        db: Session,
        user: User,
        project_id: int,
        kind: TrashKind,
        resource_id: str,
        data: TrashRestore,
    ) -> None:
        """ロック取得後に世代・期限を再判定し、対象自身だけ復元する。"""
        if not self.permitted(db, user, project_id, kind, restore=True):
            raise ForbiddenError()
        if db.info.get("demo_id"):
            lock_active_demo(db)
        row, parent = self.repository.get(db, project_id, kind, resource_id)
        if row is None or row.deleted_at is None:
            raise NotFoundError(error_messages.TRASH_NOT_FOUND)
        spec = RESOURCES[kind]
        version = getattr(row, "version", None)
        current = self.summary(
            dict(
                kind=kind,
                id=str(row.id),
                title=getattr(row, spec.title),
                deleted_at=row.deleted_at,
                version=version,
                container_id=getattr(row, spec.parent_key or "", None),
                parent_deleted_at=parent.deleted_at if parent else None,
            ),
            datetime.now(UTC),
        )
        if row.deleted_at != data.deleted_at:
            raise VersionConflictError(current=current.model_dump(mode="json"))
        raise_if_version_conflict(
            current_version=version or 0,
            requested_version=data.version or 0,
            current=current.model_dump(mode="json"),
        )
        if not current.can_restore:
            raise ConflictError(current.blocked_reason)
        row.deleted_at = None
        if version is not None:
            row.version += 1
        if hasattr(row, "updated_by"):
            row.updated_by = user.id
        if isinstance(row, ProjectDocument):
            DocumentRepository().append_revision(db, row, user.id)
        db.commit()
        self.audit.record(
            db,
            event_type=f"{kind}.restored",
            actor_user_id=user.id,
            project_id=project_id,
            resource_type=kind,
            resource_id=row.id if isinstance(row.id, int) else None,
            metadata={"id": str(row.id), "version": getattr(row, "version", None)},
        )

    def due(self, db: Session, tenant_id: int, limit: int, now: datetime) -> list[dict]:
        """専用デモと削除済みProjectを除き、期限後の候補を件数制限する。"""
        days = settings.deleted_data_retention_days
        if days == 0:
            return []
        return self.repository.due(db, tenant_id, now - timedelta(days=days), limit)

    def purge_one(self, db: Session, target: dict, now: datetime) -> bool:
        """期限・状態を再確認し、ファイル削除成功後だけDB行を回収する。"""
        if db.info.get("demo_id") or settings.deleted_data_retention_days == 0:
            raise ForbiddenError()
        row, _ = self.repository.get(
            db, target["project_id"], target["kind"], target["id"]
        )
        if (
            row is None
            or row.deleted_at is None
            or row.deleted_at
            > (now - timedelta(days=settings.deleted_data_retention_days))
        ):
            db.rollback()
            return False
        targets = self.repository.purge_plan(db, row.__tablename__, row.id)
        detachments = self.repository.detachments(db, targets)
        # 期限後は復元できないため、途中のS3失敗・DB rollbackは次回安全に再試行できる。
        keys = self.repository.storage_keys(db, targets)
        if keys:
            storage = self.storage or StorageService()
            for key in keys:
                storage.delete_object(key)
        self.repository.purge(db, targets, detachments)
        db.commit()
        self.audit.record(
            db,
            event_type=f"{target['kind']}.purged",
            project_id=target["project_id"],
            resource_type=target["kind"],
            metadata={
                "id": target["id"],
                "retention_days": settings.deleted_data_retention_days,
            },
        )
        return True
