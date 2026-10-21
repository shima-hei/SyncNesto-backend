"""同案件の出典と選択履歴へのデータアクセス。"""

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.project import Project
from app.models.project_goal_source_event import ProjectGoalSourceEvent
from app.models.requirement import RequirementDocument
from app.models.user import User


class ProjectObjectiveRepository:
    """本文をProjectへ複写せず、参照専用版で更新する。"""

    def project(self, db: Session, project_id: int) -> Project | None:
        """現在のtenant内の有効案件だけを取得する。"""
        return db.scalar(
            select(Project)
            .where(
                Project.id == project_id,
                Project.tenant_id == db.info.get("tenant_id"),
                Project.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )

    def document(
        self, db: Session, project_id: int, document_id: int, *, lock: bool = False
    ) -> RequirementDocument | None:
        """別案件と削除済み文書を除き、必要なら版をロックする。"""
        query = (
            select(RequirementDocument)
            .where(
                RequirementDocument.id == document_id,
                RequirementDocument.project_id == project_id,
                RequirementDocument.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        return db.scalar(query.with_for_update() if lock else query)

    def select_source(
        self, db: Session, project: Project, document_id: int | None
    ) -> bool:
        """旧Projectメタデータへ触れず、参照版をCAS更新する。"""
        result = db.scalar(
            update(Project)
            .where(
                Project.id == project.id,
                Project.tenant_id == db.info.get("tenant_id"),
                Project.deleted_at.is_(None),
                Project.goal_source_version == project.goal_source_version,
            )
            .values(
                goal_document_id=document_id,
                goal_source_version=project.goal_source_version + 1,
                updated_at=Project.updated_at,
            )
            .returning(Project.id)
            .execution_options(synchronize_session=False)
        )
        return result is not None

    def events(self, db: Session, project_id: int, page: int, page_size: int):
        """案件内の履歴と表示可能な操作者名をまとめて読む。"""
        query = select(ProjectGoalSourceEvent).where(
            ProjectGoalSourceEvent.project_id == project_id
        )
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = list(
            db.scalars(
                query.order_by(ProjectGoalSourceEvent.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        names = (
            {
                identity: name
                for identity, name in db.execute(
                    select(User.id, User.name).where(
                        User.id.in_({r.changed_by for r in rows}),
                        User.deleted_at.is_(None),
                    )
                )
            }
            if rows
            else {}
        )
        document_ids = {
            identity
            for row in rows
            for identity in (row.previous_document_id, row.document_id)
            if identity is not None
        }
        titles = (
            {
                identity: title
                for identity, title in db.execute(
                    select(RequirementDocument.id, RequirementDocument.title).where(
                        RequirementDocument.id.in_(document_ids),
                        RequirementDocument.project_id == project_id,
                        RequirementDocument.deleted_at.is_(None),
                    )
                )
            }
            if document_ids
            else {}
        )
        return rows, total, names, titles
