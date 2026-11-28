"""担当者に束ねた固定版を渡し、同じ版だけに判断を反映する。"""

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.models.design_review_request import (
    DesignReviewRequest,
    DesignReviewRequestTarget,
)
from app.models.specification import Specification
from app.models.user import User
from app.repositories.fixed_approval import FixedApprovalRepository
from app.schemas.design_review_request import (
    DesignReviewCreate,
    DesignReviewDecision,
    DesignReviewRead,
    DesignReviewReference,
    DesignReviewTargetRead,
)
from app.services.artifact_release_snapshot import digest
from app.services.audit_log import AuditLogService
from app.services.design_workspace import lock_project
from app.services.specification import SpecificationService

REVIEW_PERMISSIONS = ("document:read", "document:update", "requirement:review")


class DesignReviewService:
    def __init__(self):
        self.specifications = SpecificationService()
        self.reviewers = FixedApprovalRepository()

    def candidates(self, db: Session, project_id: int, actor: User):
        query = self.reviewers.candidates(db, project_id, actor.id, REVIEW_PERMISSIONS)
        return list(db.scalars(query.order_by(User.name, User.id)).all())

    @staticmethod
    def _present(
        db: Session, project_id: int, actor: User, requests: list[DesignReviewRequest]
    ) -> list[DesignReviewRead]:
        identifiers = [row.id for row in requests]
        if not identifiers:
            return []
        item = DesignReviewRequestTarget
        targets: dict[UUID, list[DesignReviewTargetRead]] = defaultdict(list)
        for row in db.scalars(
            select(item)
            .where(item.project_id == project_id, item.request_id.in_(identifiers))
            .order_by(item.specification_id)
        ):
            targets[row.request_id].append(DesignReviewTargetRead.model_validate(row))
        user_ids = {
            user_id
            for row in requests
            for user_id in (row.requester_id, row.reviewer_id)
        }
        names = {
            row.id: row.name
            for row in db.execute(
                select(User.id, User.name).where(User.id.in_(user_ids))
            )
        }
        return [
            DesignReviewRead.model_validate(
                dict(
                    id=row.id,
                    kind=row.kind,
                    status=row.status,
                    requester_id=row.requester_id,
                    reviewer_id=row.reviewer_id,
                    requester_name=names.get(row.requester_id, "メンバー"),
                    reviewer_name=names.get(row.reviewer_id, "メンバー"),
                    created_at=row.created_at,
                    decided_at=row.decided_at,
                    note=row.note,
                    decision_note=row.decision_note,
                    targets=targets[row.id],
                    assigned_to_me=actor.id == row.reviewer_id,
                    can_cancel=actor.id == row.requester_id and row.status == "pending",
                )
            )
            for row in requests
        ]

    def listing(self, db: Session, project_id: int, actor: User, pending_only: bool):
        rows = DesignReviewRequest
        query = select(rows).where(
            rows.project_id == project_id,
            or_(rows.requester_id == actor.id, rows.reviewer_id == actor.id),
        )
        if pending_only:
            query = query.where(rows.status == "pending")
        found = list(
            db.scalars(query.order_by(rows.created_at.desc(), rows.id).limit(50)).all()
        )
        return self._present(db, project_id, actor, found)

    @staticmethod
    def _audit(db: Session, row: DesignReviewRequest, actor: User, action: str):
        AuditLogService().record(
            db,
            event_type=f"design_review_request.{action}",
            actor_user_id=actor.id,
            project_id=row.project_id,
            resource_type="design_review_request",
            metadata={
                "request_id": str(row.id),
                "kind": row.kind,
                "status": row.status,
            },
        )

    def _targets(
        self,
        db: Session,
        project_id: int,
        refs: Sequence[DesignReviewReference | DesignReviewRequestTarget],
    ):
        locked: list[Specification] = []
        for target in sorted(refs, key=lambda item: item.specification_id):
            row = self.specifications.get(
                db, project_id, target.specification_id, lock=True
            )
            self.specifications.check_version(row, target.version)
            locked.append(row)
        return locked

    def create(
        self, db: Session, project_id: int, actor: User, data: DesignReviewCreate
    ):
        signature = digest(data.model_dump(mode="json"))
        lock_project(db, project_id)
        existing = db.get(DesignReviewRequest, (project_id, data.request_id))
        if existing is not None:
            if (
                existing.requester_id != actor.id
                or existing.request_digest != signature
            ):
                raise ConflictError()
            return self._present(db, project_id, actor, [existing])[0]
        eligible = self.reviewers.candidates(
            db, project_id, actor.id, REVIEW_PERMISSIONS
        )
        if db.scalar(eligible.where(User.id == data.reviewer_id)) is None:
            raise BadRequestError(
                "依頼先はこの案件でレビューできるメンバーを指定してください"
            )
        rows = self._targets(db, project_id, data.targets)
        item = DesignReviewRequestTarget
        request = DesignReviewRequest
        occupied = db.scalar(
            select(item.specification_id)
            .join(
                request,
                (request.project_id == item.project_id)
                & (request.id == item.request_id),
            )
            .where(
                item.project_id == project_id,
                item.specification_id.in_([row.id for row in rows]),
                request.status == "pending",
            )
            .limit(1)
        )
        if occupied is not None:
            raise ConflictError(
                "対応中の依頼がある設計は、その依頼を閉じてから再指定してください"
            )
        batch = request(
            id=data.request_id,
            project_id=project_id,
            kind=data.kind,
            status="pending",
            requester_id=actor.id,
            reviewer_id=data.reviewer_id,
            request_digest=signature,
            note=data.note,
        )
        db.add(batch)
        db.flush()
        for row in rows:
            if data.kind == "review" and row.status in {"draft", "changes_requested"}:
                self.specifications.stage_status(db, row, "in_review", actor)
            elif row.status != "in_review":
                raise BadRequestError(
                    "承認依頼の対象はレビュー中の設計を選択してください"
                    if data.kind == "approval"
                    else "下書きかレビュー中の設計を選択してください"
                )
            elif (
                self.specifications.repository.revision_number(db, row.id, row.version)
                is None
            ):
                raise ConflictError("選択した設計の固定版を読み込めません")
            db.add(
                item(
                    project_id=project_id,
                    request_id=batch.id,
                    specification_id=row.id,
                    version=row.version,
                    title=row.title,
                    kind=row.kind,
                )
            )
        db.commit()
        self._audit(db, batch, actor, "created")
        return self._present(db, project_id, actor, [batch])[0]

    def decide(
        self,
        db: Session,
        project_id: int,
        request_id: UUID,
        actor: User,
        data: DesignReviewDecision,
    ):
        request = DesignReviewRequest
        batch = db.scalar(
            select(request)
            .where(
                request.project_id == project_id,
                request.id == request_id,
                or_(request.requester_id == actor.id, request.reviewer_id == actor.id),
            )
            .with_for_update()
        )
        if batch is None:
            raise NotFoundError()
        if data.status == "cancelled":
            if batch.requester_id != actor.id:
                raise ForbiddenError()
        elif batch.reviewer_id != actor.id or not all(
            self.specifications.authorization.has_project_permission(
                db, user=actor, project_id=project_id, permission_code=permission
            )
            for permission in REVIEW_PERMISSIONS
        ):
            raise ForbiddenError()
        if batch.status != "pending":
            if (
                batch.status == data.status
                and batch.decided_by == actor.id
                and batch.decision_note == data.note
            ):
                return self._present(db, project_id, actor, [batch])[0]
            raise ConflictError("この依頼はすでに対応済みです")
        if (data.status == "approved" and batch.kind != "approval") or (
            data.status == "reviewed" and batch.kind != "review"
        ):
            raise BadRequestError("依頼の種類に合う判断を選んでください")
        if data.status != "cancelled":
            item = DesignReviewRequestTarget
            refs = list(
                db.scalars(
                    select(item).where(
                        item.project_id == project_id, item.request_id == request_id
                    )
                ).all()
            )
            rows = self._targets(db, project_id, refs)
            if data.status != "reviewed":
                for row in rows:
                    self.specifications.stage_status(db, row, data.status, actor)
        batch.status = data.status
        batch.decision_note = data.note
        batch.decided_at = datetime.now(UTC)
        batch.decided_by = actor.id
        db.commit()
        self._audit(db, batch, actor, data.status)
        return self._present(db, project_id, actor, [batch])[0]
