"""現在の権限・元対象を確認し、修正と再依頼の既存入口を返す。"""

from sqlalchemy import func, select

from app.core.config import settings
from app.core.exceptions import NotFoundError
from app.db.base import Base
from app.models.user import User
from app.repositories.guest_fixed_approval import GuestFixedApprovalRepository
from app.schemas.fixed_approval_notification import (
    ApprovalNextAction,
    ApprovalNextActions,
    ApprovalNextActor,
)
from app.services.artifact_release import SOURCE_PERMISSIONS
from app.services.fixed_approval import FixedApprovalService


class ApprovalNextActionsService:
    """判断やタスクの状態を複製せず、次操作だけを導出する。"""

    def read(self, original, user, project_id, approval_id):
        """元が削除済みでも固定した履歴を残し、編集リンクは返さない。"""
        if not settings.fixed_approval_notifications_enabled:
            raise NotFoundError()
        approvals = FixedApprovalService()
        with approvals.access(original, user, project_id) as (db, actor):
            row = approvals.get_row(db, actor, project_id, approval_id)
            binding = GuestFixedApprovalRepository().binding(db, row.id, project_id)
            waiting = row.status == "requested" and (
                not binding
                or db.scalar(func.guest_approval_has_access(row.id, row.approver_id))
            )
            next_id = row.approver_id if waiting else row.requester_id
            next_user = db.get(User, next_id)
            next_actor = ApprovalNextActor(
                kind="approver" if waiting else "requester",
                name=next_user.name
                if next_user and next_user.deleted_at is None
                else None,
                is_you=next_id == actor.id,
            )
            auth = approvals.releases.authorization

            def allowed(code):
                return auth.has_project_permission(
                    db, user=actor, project_id=project_id, permission_code=code
                )

            kind = row.target_kind
            table = Base.metadata.tables[
                {
                    "requirement": "requirements",
                    "requirement_document": "requirement_documents",
                    "specification": "specifications",
                    "test_design": "test_designs",
                }[kind]
            ]
            query = select(table).where(
                table.c.id == row.source_id, table.c.deleted_at.is_(None)
            )
            if kind == "requirement":
                docs = Base.metadata.tables["requirement_documents"]
                query = query.join(docs, docs.c.id == table.c.document_id).where(
                    docs.c.project_id == project_id, docs.c.deleted_at.is_(None)
                )
            else:
                query = query.where(table.c.project_id == project_id)
            source = db.execute(query).mappings().first()
            actions = []
            prefix = SOURCE_PERMISSIONS[kind]
            if source is not None and allowed(f"{prefix}:read"):
                values = dict(
                    source_kind=kind,
                    source_id=row.source_id,
                    document_id=source["document_id"]
                    if kind == "requirement"
                    else None,
                )
                actions.append(
                    ApprovalNextAction.model_validate(
                        dict(kind="open_source", **values)
                    )
                )
                if allowed(f"{prefix}:update"):
                    actions.append(
                        ApprovalNextAction.model_validate(
                            dict(kind="edit_source", **values)
                        )
                    )
            if (
                source is not None
                and allowed("artifact_release:create")
                and allowed(f"{prefix}:update")
            ):
                actions.append(ApprovalNextAction(kind="open_fixed_versions"))
            if binding and allowed("artifact_publication:read"):
                actions.append(
                    ApprovalNextAction(
                        kind="open_publication",
                        publication_id=binding["publication_id"],
                    )
                )
            return ApprovalNextActions(
                approval_id=row.id,
                status=row.status,
                next_actor=next_actor,
                actions=actions,
            )
