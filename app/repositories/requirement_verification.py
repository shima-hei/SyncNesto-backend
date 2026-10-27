"""確認表の限定取得と追記。commitと認可はServiceが担当する。"""

from sqlalchemy import Text, cast, func, insert, or_, select

from app.core.exceptions import ArtifactReleaseTooLargeError, NotFoundError
from app.models.artifact_release import ArtifactRelease, ArtifactReleaseNode
from app.models.requirement_verification import RequirementVerificationPlan as Plan
from app.models.requirement_verification import (
    RequirementVerificationRevision as Revision,
)
from app.models.user import User
from app.services.fixed_approval_scope import requirement_subject


class RequirementVerificationRepository:
    def insert_header(self, db, row):
        # 遅延FKの初期参照先は同transactionの次のINSERTで作る。
        # 所属/固定対象はServiceとDB guardで検証し、既存ORM認可は緩めない。
        values = {
            column.name: getattr(row, column.name)
            for column in Plan.__table__.c
            if getattr(row, column.name) is not None
        }
        db.connection().execute(insert(Plan).values(**values))
        saved = db.get(Plan, row.id)
        assert saved is not None
        return saved

    def plan(self, db, project_id, plan_id, *, lock=False):
        query = select(Plan).where(Plan.id == plan_id, Plan.project_id == project_id)
        row = db.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise NotFoundError()
        return row

    def fixed(self, db, project_id, release_id, target_node_id):
        node = ArtifactReleaseNode
        condition = (
            node.release_id == release_id,
            or_(
                node.id == target_node_id,
                (node.parent_id == target_node_id)
                & (node.node_kind == "requirement_detail"),
            ),
        )
        count, size = db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(func.octet_length(cast(node.payload, Text))), 0),
            ).where(*condition)
        ).one()
        if count > 501 or size + count * 128 + 256 > 1024 * 1024:
            raise ArtifactReleaseTooLargeError()
        rows = list(
            db.scalars(
                select(node)
                .join(ArtifactRelease, ArtifactRelease.id == node.release_id)
                .where(
                    *condition,
                    ArtifactRelease.project_id == project_id,
                    ArtifactRelease.sealed.is_(True),
                )
                .order_by(node.position, node.id)
            )
        )
        target = next(
            (
                r
                for r in rows
                if r.id == target_node_id and r.node_kind == "requirement"
            ),
            None,
        )
        if target is None:
            raise NotFoundError()
        return requirement_subject(target, [r for r in rows if r.id != target_node_id])

    def revision(self, db, plan_id, version):
        row, name = db.execute(
            select(Revision, User.name)
            .join(User, User.id == Revision.actor_id)
            .where(Revision.plan_id == plan_id, Revision.version == version)
        ).one()
        return dict(
            version=row.version,
            checklist=row.checklist,
            checklist_digest=row.checklist_digest,
            actor_name=name,
            created_at=row.created_at,
        )

    def summaries(self, db, project_id, requirement_id, offset, limit):
        query = (
            select(Plan, ArtifactReleaseNode.payload["title"].astext.label("title"))
            .join(ArtifactReleaseNode, ArtifactReleaseNode.id == Plan.target_node_id)
            .where(Plan.project_id == project_id)
        )
        if requirement_id is not None:
            query = query.where(Plan.source_requirement_id == requirement_id)
        return db.execute(
            query.order_by(Plan.created_at, Plan.id).offset(offset).limit(limit + 1)
        ).all()

    def history(self, db, plan_id, after, limit):
        return db.execute(
            select(Revision, User.name)
            .join(User, User.id == Revision.actor_id)
            .where(Revision.plan_id == plan_id, Revision.version > after)
            .order_by(Revision.version)
            .limit(limit + 1)
        ).all()
