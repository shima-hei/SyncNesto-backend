"""参加資格と公開用の案件情報を扱う。"""

from sqlalchemy import select, text

from app.core.exceptions import NotFoundError
from app.models.artifact_publication_access import GuestProjectProfile
from app.repositories.artifact_publication_access import PublicationAccessRepository
from app.schemas.guest_workspace import (
    GuestProjectList,
    GuestProjectOverview,
    GuestProjectProfileSettings,
    GuestProjectRead,
)
from app.services.artifact_publication_access import ArtifactPublicationAccessService
from app.services.audit_log import AuditLogService
from app.services.publication_access_context import (
    PublicationAccessContext,
    retry_publication_operation,
)


class GuestWorkspaceService:
    """固定版公開と同じ本人再確認・参加資格・Project境界を使う。"""

    def __init__(self):
        self.context = PublicationAccessContext()
        self.repository = PublicationAccessRepository()

    @retry_publication_operation
    def projects(self, original, user, page, page_size):
        """本人の参加済み集合をページ取得する。"""
        with self.context.transaction(original, user) as (db, actor):
            query = self.repository.shared_projects_query(actor.id)
            rows, total = self.repository.page(
                db, query, [query.selected_columns.participation_id], page, page_size
            )
            return GuestProjectList(
                items=[self._read(row) for row in rows],
                total=total,
                page=page,
                page_size=page_size,
            )

    @staticmethod
    def _read(row):
        return GuestProjectRead(
            **{key: row[key] for key in GuestProjectRead.model_fields}
        )

    @retry_publication_operation
    def project(self, original, user, participation_id):
        """本人の有効な参加世代から公開用概要を解決する。"""
        with self.context.transaction(original, user) as (db, actor):
            query = self.repository.shared_projects_query(actor.id)
            query = query.where(
                query.selected_columns.participation_id == participation_id
            )
            candidate = db.execute(query).mappings().first()
            if candidate is None:
                raise NotFoundError()
            db.execute(
                text("SELECT publication_lock(:project,true)"),
                {"project": candidate["project_id"]},
            )
            self.context.verify(db, original, actor, internal=False)
            row = db.execute(query).mappings().first()
            if row is None:
                raise NotFoundError()
            return self._read(row)

    @staticmethod
    def _settings(row):
        if row is None:
            return GuestProjectProfileSettings(version=0)
        return GuestProjectProfileSettings(
            version=row.version,
            **{key: getattr(row, key) for key in GuestProjectOverview.model_fields},
        )

    @retry_publication_operation
    def settings(self, original, user, project_id, data=None):
        """更新は対象案件の管理権限・確認した版を必要とする。"""
        with self.context.transaction(
            original,
            user,
            project_id,
            write=data is not None,
            management="project:update",
        ) as (db, actor):
            row = db.scalar(
                select(GuestProjectProfile).where(
                    GuestProjectProfile.project_id == project_id
                )
            )
            if data is None:
                return self._settings(row)
            current = self._settings(row)
            values = data.model_dump(exclude={"version"})
            if values == current.model_dump(exclude={"version"}):
                return current
            ArtifactPublicationAccessService._check_version(
                current.version, data.version
            )
            if row is None:
                row = GuestProjectProfile(project_id=project_id, version=0)
                db.add(row)
            for key, value in values.items():
                setattr(row, key, value)
            row.version += 1
            AuditLogService().record(
                db,
                event_type="guest_project_profile.updated",
                tenant_id=db.info["tenant_id"],
                actor_user_id=actor.id,
                project_id=project_id,
                resource_type="guest_project_profile",
                resource_id=project_id,
                metadata={"version": row.version},
                commit=False,
            )
            db.flush()
            return self._settings(row)
