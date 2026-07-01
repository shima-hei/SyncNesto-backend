"""下書き保存サービスを定義するモジュール。"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import ForbiddenError, NotFoundError, VersionConflictError
from app.models.draft import Draft
from app.models.user import User
from app.repositories.draft import DraftRepository
from app.schemas.draft import DraftUpsert
from app.services.authorization import AuthorizationService

DRAFT_TTL_DAYS = 30


class DraftService:
    """下書き保存に関するビジネスロジックを提供する。"""

    def __init__(
        self,
        repository: DraftRepository | None = None,
        authorization_service: AuthorizationService | None = None,
    ) -> None:
        """DraftServiceを初期化する。"""
        self.repository = repository or DraftRepository()
        self.authorization_service = authorization_service or AuthorizationService()

    def list_drafts(
        self,
        db: Session,
        *,
        current_user: User,
        resource_type: str | None = None,
        project_id: int | None = None,
    ) -> list[Draft]:
        """所有者の下書き一覧を取得する。"""
        self._ensure_project_readable(
            db,
            current_user=current_user,
            project_id=project_id,
        )
        drafts = self.repository.list_by_owner(
            db,
            owner_user_id=current_user.id,
            resource_type=resource_type,
            project_id=project_id,
        )
        if project_id is not None:
            return drafts
        return [
            draft
            for draft in drafts
            if draft.project_id is None
            or self._has_project_read_permission(
                db,
                current_user=current_user,
                project_id=draft.project_id,
            )
        ]

    def get_draft(self, db: Session, *, current_user: User, draft_id: int) -> Draft:
        """所有者本人の下書きを取得する。"""
        draft = self.repository.get_by_id(db, draft_id)
        if draft is None or draft.owner_user_id != current_user.id:
            raise NotFoundError(error_messages.NOT_FOUND)
        self._ensure_project_readable(
            db,
            current_user=current_user,
            project_id=draft.project_id,
        )
        return draft

    def upsert_draft(
        self,
        db: Session,
        *,
        current_user: User,
        scope_key: str,
        draft_in: DraftUpsert,
    ) -> Draft:
        """scope_key単位で下書きを作成または更新する。"""
        self._ensure_project_readable(
            db,
            current_user=current_user,
            project_id=draft_in.project_id,
        )
        draft = self.repository.get_by_owner_scope(
            db,
            owner_user_id=current_user.id,
            scope_key=scope_key,
        )
        expires_at = datetime.now(UTC) + timedelta(days=DRAFT_TTL_DAYS)
        if draft is None:
            return self.repository.create(
                db,
                draft=Draft(
                    owner_user_id=current_user.id,
                    scope_key=scope_key,
                    resource_type=draft_in.resource_type,
                    resource_id=draft_in.resource_id,
                    project_id=draft_in.project_id,
                    schema_version=draft_in.schema_version,
                    content=draft_in.content,
                    expires_at=expires_at,
                ),
            )
        if draft_in.version is not None and draft.version != draft_in.version:
            raise VersionConflictError(current=self._build_current(draft))

        draft.resource_type = draft_in.resource_type
        draft.resource_id = draft_in.resource_id
        draft.project_id = draft_in.project_id
        draft.schema_version = draft_in.schema_version
        draft.content = draft_in.content
        draft.expires_at = expires_at
        draft.version += 1
        db.commit()
        db.refresh(draft)
        return draft

    def delete_draft(self, db: Session, *, current_user: User, draft_id: int) -> None:
        """所有者本人の下書きを削除する。"""
        draft = self.get_draft(db, current_user=current_user, draft_id=draft_id)
        self.repository.delete(db, draft=draft)

    def delete_expired_drafts(self, db: Session, *, now: datetime | None = None) -> int:
        """期限切れ下書きを削除する。"""
        return self.repository.delete_expired(db, now=now or datetime.now(UTC))

    def _ensure_project_readable(
        self,
        db: Session,
        *,
        current_user: User,
        project_id: int | None,
    ) -> None:
        """project_id付き下書きの閲覧権限を確認する。"""
        if project_id is None:
            return
        if not self._has_project_read_permission(
            db,
            current_user=current_user,
            project_id=project_id,
        ):
            raise ForbiddenError()

    def _has_project_read_permission(
        self,
        db: Session,
        *,
        current_user: User,
        project_id: int,
    ) -> bool:
        """プロジェクト閲覧権限があるか判定する。"""
        return self.authorization_service.has_project_permission(
            db,
            user=current_user,
            project_id=project_id,
            permission_code="project:read",
        )

    def _build_current(self, draft: Draft) -> dict[str, object]:
        """VERSION_CONFLICT current payloadを作成する。"""
        return {
            "id": draft.id,
            "scope_key": draft.scope_key,
            "resource_type": draft.resource_type,
            "resource_id": draft.resource_id,
            "project_id": draft.project_id,
            "schema_version": draft.schema_version,
            "content": draft.content,
            "version": draft.version,
            "updated_at": draft.updated_at,
            "expires_at": draft.expires_at,
        }
