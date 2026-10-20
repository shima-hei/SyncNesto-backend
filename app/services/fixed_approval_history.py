"""旧履歴と新依頼を、権限・時点・IDを混同せず一覧する。"""

from sqlalchemy import DateTime, exists, false, func, or_, select

from app.core.exceptions import FixedApprovalTooLargeError
from app.models.fixed_approval import FixedApprovalRequest, LegacyApprovalImport
from app.models.guest_fixed_approval import FixedApprovalGuestBinding
from app.models.requirement import RequirementApproval, RequirementDocument
from app.models.user import User
from app.schemas.fixed_approval import (
    FixedApprovalHistoryItem,
    FixedApprovalHistoryPage,
)
from app.services.fixed_approval import FixedApprovalService
from app.services.fixed_approval_migration import record_json
from app.services.fixed_approval_scope import legacy_evidence


class FixedApprovalHistoryService:
    """一覧も単体取得と同じ認可集合を使う。"""

    def __init__(self):
        """現在の固定版認可とRepositoryを利用する。"""
        self.approvals = FixedApprovalService()
        self.repository = self.approvals.repository

    def legacy_item(self, db, actor, project_id, source, imported, native_query):
        """元行と取込時点を区別し、移行先を権限外へ漏らさない。"""
        current = self.repository.legacy(db, project_id, source["id"])
        if current is not None:
            logs = [
                record_json(item) for item in self.repository.legacy_logs(db, current)
            ]
            try:
                evidence = legacy_evidence(source, logs)
            except FixedApprovalTooLargeError:
                evidence = {
                    "classification": "history_only",
                    "subject_verified": False,
                    "actual_actor_id": None,
                    "issues": ["EVIDENCE_TOO_LARGE"],
                }
            if imported is not None:
                evidence = {**evidence, "archive_is_older": imported.source != source}
        else:
            evidence = imported.evidence
        migrated = db.scalar(
            native_query.where(
                FixedApprovalRequest.legacy_import_id.in_(
                    select(LegacyApprovalImport.id).where(
                        LegacyApprovalImport.project_id == project_id,
                        LegacyApprovalImport.legacy_approval_id == source["id"],
                        LegacyApprovalImport.mode == "switch",
                    ),
                )
            )
        )
        return FixedApprovalHistoryItem(
            scheme="legacy",
            id=str(source["id"]),
            title=f"従来承認 #{source['id']}",
            target_kind=source["target_type"],
            source_id=source["target_id"],
            status=source["status"],
            requester_id=source["requested_by"],
            approver_id=source["approver_id"],
            created_at=source["requested_at"],
            finalized_at=source.get("approved_at") or source.get("rejected_at"),
            evidence=evidence,
            import_id=imported.id if imported else None,
            migrated_to=migrated.id if migrated else None,
        )

    def list(
        self,
        original,
        user,
        project_id,
        target_kind,
        source_id,
        status,
        mine,
        scheme,
        page,
        page_size,
        audience=None,
        publication_id=None,
    ):
        """必要な件数だけ各集合から取得して、申請時刻でマージする。"""
        with self.approvals.access(original, user, project_id) as (db, actor):
            kinds, document_read = self.approvals.allowed(db, actor, project_id)
            native_visible = self.repository.visible(
                project_id, kinds, document_read=document_read
            )
            native = native_visible
            legacy = self.repository.legacy_visible(project_id)
            legacy_kind = {
                "requirement": "requirement_item",
                "requirement_document": "document",
            }.get(target_kind, target_kind)
            old_exists = exists(
                select(RequirementApproval.id)
                .join(RequirementDocument)
                .where(
                    RequirementApproval.id == LegacyApprovalImport.legacy_approval_id,
                    RequirementDocument.project_id == project_id,
                )
            )
            archive_latest = (
                select(LegacyApprovalImport)
                .where(
                    LegacyApprovalImport.project_id == project_id,
                    ~old_exists,
                )
                .distinct(LegacyApprovalImport.legacy_approval_id)
                .order_by(
                    LegacyApprovalImport.legacy_approval_id,
                    LegacyApprovalImport.created_at.desc(),
                    LegacyApprovalImport.id.desc(),
                )
                .subquery()
            )
            from sqlalchemy.orm import aliased

            archive_model = aliased(LegacyApprovalImport, archive_latest)
            archive = select(archive_model)
            external = select(FixedApprovalGuestBinding.approval_id).where(
                FixedApprovalGuestBinding.approval_id == FixedApprovalRequest.id
            )
            if publication_id:
                external = external.where(
                    FixedApprovalGuestBinding.publication_id == publication_id
                )
            if audience == "guest" or publication_id:
                native = native.where(exists(external))
                legacy, archive = legacy.where(false()), archive.where(false())
            elif audience == "internal":
                native = native.where(~exists(external))
            if target_kind:
                native = native.where(FixedApprovalRequest.target_kind == target_kind)
                legacy = legacy.where(RequirementApproval.target_type == legacy_kind)
                archive = archive.where(
                    archive_model.source["target_type"].as_string() == legacy_kind
                )
            if source_id:
                native = native.where(FixedApprovalRequest.source_id == source_id)
                legacy = legacy.where(RequirementApproval.target_id == source_id)
                archive = archive.where(
                    archive_model.source["target_id"].as_integer() == source_id
                )
            if status:
                native = native.where(FixedApprovalRequest.status == status)
                legacy = legacy.where(RequirementApproval.status == status)
                archive = archive.where(
                    archive_model.source["status"].as_string() == status
                )
                if status == "requested":
                    legacy = legacy.where(
                        ~exists(
                            select(LegacyApprovalImport.id).where(
                                LegacyApprovalImport.project_id == project_id,
                                LegacyApprovalImport.legacy_approval_id
                                == RequirementApproval.id,
                                LegacyApprovalImport.mode == "switch",
                            )
                        )
                    )
                    archive = archive.where(archive_model.mode != "switch")
            if mine:
                native = native.where(
                    or_(
                        FixedApprovalRequest.requester_id == actor.id,
                        FixedApprovalRequest.approver_id == actor.id,
                    )
                )
                legacy = legacy.where(
                    or_(
                        RequirementApproval.requested_by == actor.id,
                        RequirementApproval.approver_id == actor.id,
                    )
                )
                archive = archive.where(
                    or_(
                        archive_model.source["requested_by"].as_integer() == actor.id,
                        archive_model.source["approver_id"].as_integer() == actor.id,
                    )
                )
            total, items = 0, []
            limit = page * page_size
            if scheme != "legacy":
                total += (
                    db.scalar(select(func.count()).select_from(native.subquery())) or 0
                )
                for row in db.scalars(
                    native.order_by(
                        FixedApprovalRequest.created_at.desc(),
                        FixedApprovalRequest.id.desc(),
                    ).limit(limit)
                ):
                    items.append(
                        FixedApprovalHistoryItem(
                            scheme="fixed",
                            id=str(row.id),
                            title=row.scope_manifest["title"],
                            target_kind=row.target_kind,
                            source_id=row.source_id,
                            status=row.status,
                            requester_id=row.requester_id,
                            approver_id=row.approver_id,
                            created_at=row.created_at,
                            finalized_at=row.finalized_at,
                            audience="guest"
                            if row.policy_version == "fixed-approval-guest-v1"
                            else "internal",
                        )
                    )
            if scheme != "fixed":
                total += (
                    db.scalar(select(func.count()).select_from(legacy.subquery())) or 0
                )
                total += (
                    db.scalar(select(func.count()).select_from(archive.subquery())) or 0
                )
                for row in db.scalars(
                    legacy.order_by(
                        RequirementApproval.requested_at.desc(),
                        RequirementApproval.id.desc(),
                    ).limit(limit)
                ):
                    imported = db.scalar(
                        select(LegacyApprovalImport)
                        .where(
                            LegacyApprovalImport.project_id == project_id,
                            LegacyApprovalImport.legacy_approval_id == row.id,
                        )
                        .order_by(
                            LegacyApprovalImport.created_at.desc(),
                            LegacyApprovalImport.id.desc(),
                        )
                        .limit(1)
                    )
                    items.append(
                        self.legacy_item(
                            db,
                            actor,
                            project_id,
                            record_json(row),
                            imported,
                            native_visible,
                        )
                    )
                for row in db.scalars(
                    archive.order_by(
                        archive_model.source["requested_at"]
                        .as_string()
                        .cast(DateTime(timezone=True))
                        .desc()
                    ).limit(limit)
                ):
                    items.append(
                        self.legacy_item(
                            db, actor, project_id, row.source, row, native_visible
                        )
                    )
            items.sort(
                key=lambda item: (item.created_at, item.scheme, item.id), reverse=True
            )
            visible_items = items[(page - 1) * page_size : page * page_size]
            user_ids = {
                identity
                for item in visible_items
                for identity in (item.requester_id, item.approver_id)
            }
            names = (
                {
                    identity: name
                    for identity, name in db.execute(
                        select(User.id, User.name).where(
                            User.id.in_(user_ids), User.deleted_at.is_(None)
                        )
                    )
                }
                if user_ids
                else {}
            )
            return FixedApprovalHistoryPage(
                items=[
                    item.model_copy(
                        update={
                            "requester_name": names.get(item.requester_id),
                            "approver_name": names.get(item.approver_id),
                        }
                    )
                    for item in visible_items
                ],
                total=total,
                page=page,
                page_size=page_size,
            )
