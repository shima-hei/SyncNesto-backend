"""固定版の認可・一貫した読取・冪等保存を統括する。"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4, uuid5

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import (
    ArtifactReleaseTooLargeError,
    BadRequestError,
    ConflictError,
    DuplicateResourceError,
    ForbiddenError,
    NotFoundError,
    TokenExpiredError,
    VersionConflictError,
)
from app.core.mfa import require_verified
from app.models.artifact_release import ArtifactRelease
from app.models.requirement import Requirement, RequirementDocument
from app.models.session import UserSession
from app.models.specification import Specification
from app.models.test_design import TestDesign
from app.models.user import User, UserType
from app.repositories.artifact_release import ArtifactReleaseRepository
from app.schemas.artifact_release import (
    ArtifactCreateRequest,
    ArtifactNodePage,
    ArtifactNodeRead,
    ArtifactPreview,
    ArtifactPreviewRequest,
    ArtifactReleaseList,
    ArtifactReleaseMetadata,
    ArtifactReleaseSummary,
    ArtifactSource,
)
from app.services.artifact_release_cursor import decode_cursor, encode_cursor
from app.services.artifact_release_snapshot import (
    MAX_PAGE_BYTES,
    ArtifactSnapshotBuilder,
    digest,
)
from app.services.audit_log import AuditLogService
from app.services.authorization import AuthorizationService

SOURCE_PERMISSIONS = {
    "requirement": "requirement",
    "requirement_document": "requirement",
    "specification": "document",
    "test_design": "test_plan",
}


class ArtifactReleaseService:
    """既存の認可やengine設定を変更せず固定版だけを扱う。"""

    def __init__(self) -> None:
        """保存、投影、既存の認可と監査を組み合わせる。"""
        self.repository = ArtifactReleaseRepository()
        self.builder = ArtifactSnapshotBuilder()
        self.authorization = AuthorizationService()

    @contextmanager
    def _transaction(
        self, original: Session, user: User, project_id: int, operation: str
    ) -> Iterator[tuple[Session, User]]:
        """最初のSQL前にisolationを設定し、検証済みcontextを再確認する。"""
        if (
            original.info.get("tenant_id") is None
            or original.info.get("tenant_user_id") != user.id
            or original.info.get("demo_id") is not None
            or original.info.get("data_realm") == "demo"
            or original.info.get("mcp_connection_id") is not None
        ):
            raise ForbiddenError()
        context = {
            k: original.info[k]
            for k in ("tenant_id", "tenant_user_id", "data_realm")
            if k in original.info
        }
        bind = original.get_bind()
        with (
            bind.engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as connection,
            Session(bind=connection, info=context) as db,
            db.begin(),
        ):
            actor = db.get(User, user.id)
            if (
                actor is None
                or not actor.is_active
                or actor.deleted_at is not None
                or actor.user_type != UserType.INTERNAL.value
                or actor.password_change_required
            ):
                raise ForbiddenError()
            active = original.info.get("auth_session")
            if active is not None:
                current = db.get(UserSession, active.id)
                now = datetime.now(UTC)
                if (
                    current is None
                    or current.user_id != actor.id
                    or current.revoked_at is not None
                    or current.expires_at <= now
                    or current.absolute_expires_at <= now
                ):
                    raise TokenExpiredError()
                require_verified(actor, current)
            self._permission(db, actor, project_id, f"artifact_release:{operation}")
            yield db, actor

    def _permission(self, db, user, project_id, permission) -> None:
        """運営・組織roleでProject所属を迂回しない。"""
        if not self.authorization.has_project_permission(
            db, user=user, project_id=project_id, permission_code=permission
        ):
            raise ForbiddenError()

    def _sources(self, db, user, project_id, kinds, *, create=False) -> None:
        """構成に含まれる全種類へ現在の権限を適用する。"""
        for kind in set(kinds):
            prefix = SOURCE_PERMISSIONS[kind]
            self._permission(db, user, project_id, f"{prefix}:read")
            if create:
                self._permission(db, user, project_id, f"{prefix}:update")

    def preview(
        self, db: Session, user: User, project_id: int, data: ArtifactPreviewRequest
    ) -> ArtifactPreview:
        """副作用なしで、固定する構成とhashを確認する。"""
        with self._transaction(db, user, project_id, "create") as (work, actor):
            self._sources(
                work,
                actor,
                project_id,
                [s.kind for s in [data.source, *data.included_sources]],
                create=True,
            )
            captured = work.execute(select(func.transaction_timestamp())).scalar_one()
            return self.builder.build(work, project_id, data, captured).preview()

    def create(
        self, db: Session, user: User, project_id: int, data: ArtifactCreateRequest
    ) -> tuple[ArtifactReleaseMetadata, bool]:
        """構成を再確認し、本文と作成操作を冪等に保存する。"""
        for attempt in range(3):
            try:
                with self._transaction(db, user, project_id, "create") as (work, actor):
                    kinds = [s.kind for s in [data.source, *data.included_sources]]
                    self._sources(work, actor, project_id, kinds, create=True)
                    previous = self.repository.by_request(
                        work, project_id, actor.id, data.request_id
                    )
                    if previous is not None:
                        self._sources(work, actor, project_id, previous.source_kinds)
                        if previous.request_digest != digest(
                            data.model_dump(mode="json")
                        ):
                            raise DuplicateResourceError()
                        return self._metadata(work, previous), False
                    request = ArtifactPreviewRequest.model_validate(
                        data.model_dump(exclude={"request_id", "expected_scope_digest"})
                    )
                    captured = work.execute(
                        select(func.transaction_timestamp())
                    ).scalar_one()
                    snapshot = self.builder.build(work, project_id, request, captured)
                    if snapshot.scope_digest != data.expected_scope_digest:
                        raise VersionConflictError(
                            {"scope_digest": snapshot.scope_digest}
                        )
                    row = self._save(work, project_id, actor.id, data, snapshot)
                    AuditLogService().record(
                        work,
                        event_type="artifact_release.created",
                        actor_user_id=actor.id,
                        project_id=project_id,
                        resource_type="artifact_release",
                        metadata={
                            "release_id": str(row.id),
                            "source_kind": row.source_kind,
                        },
                        commit=False,
                    )
                    work.flush()
                    result = self._metadata(work, row)
                return result, True
            except DBAPIError as exc:
                code = getattr(exc.orig, "pgcode", None)
                constraint = getattr(
                    getattr(exc.orig, "diag", None), "constraint_name", None
                )
                retry = code in {"40001", "40P01"} or (
                    code == "23505" and constraint == "uq_artifact_release_request"
                )
                if not retry:
                    raise
                if attempt == 2:
                    raise ConflictError(
                        error_messages.ARTIFACT_RETRY_CONFLICT
                    ) from None
        raise AssertionError("unreachable")

    def _save(self, db, project_id, actor_id, data, snapshot):
        """認可済みの投影だけから固定版の保存データを組み立てる。"""
        release_id = uuid4()
        refs = [s.model_dump() for s in [data.source, *data.included_sources]]
        header = dict(
            id=release_id,
            project_id=project_id,
            source_kind=data.source.kind,
            source_id=data.source.id,
            source_version=data.source.version,
            source_refs=refs,
            source_kinds=sorted({r["kind"] for r in refs}),
            schema_version=1,
            projection_version=data.projection_version,
            title=snapshot.title,
            content_digest=snapshot.scope_digest,
            node_count=len(snapshot.nodes),
            byte_size=snapshot.byte_size,
            captured_at=snapshot.captured_at,
            created_by=actor_id,
            request_id=data.request_id,
            request_digest=digest(data.model_dump(mode="json")),
            sealed=False,
        )
        nodes = [
            dict(
                id=uuid5(release_id, n["key"]),
                release_id=release_id,
                parent_id=uuid5(release_id, n["parent"]) if n["parent"] else None,
                ordinal=ordinal,
                position=n["position"],
                node_kind=n["node_kind"],
                source_id=n["source_id"],
                source_version=n["source_version"],
                payload=n["payload"],
                content_digest=digest(n),
            )
            for ordinal, n in enumerate(snapshot.nodes)
        ]
        return self.repository.create(db, header, nodes)

    def _get(self, db, user, project_id, release_id):
        """同一Projectの完成版と、その全種類への権限を確認する。"""
        row = self.repository.get(db, project_id, release_id)
        if row is None:
            raise NotFoundError()
        self._sources(db, user, project_id, row.source_kinds)
        return row

    @staticmethod
    def _metadata(db, row) -> ArtifactReleaseMetadata:
        """現在の元対象はmetadataだけ読み、固定本文へ混ぜない。"""
        model = {
            "requirement": Requirement,
            "requirement_document": RequirementDocument,
            "specification": Specification,
            "test_design": TestDesign,
        }[row.source_kind]
        query = select(model).where(
            model.id == row.source_id, model.deleted_at.is_(None)
        )
        if model is Requirement:
            query = query.join(RequirementDocument).where(
                RequirementDocument.project_id == row.project_id,
                RequirementDocument.deleted_at.is_(None),
            )
        else:
            query = query.where(model.project_id == row.project_id)
        source = db.scalar(query)
        return ArtifactReleaseMetadata(
            **ArtifactReleaseSummary.model_validate(row).model_dump(),
            current_source_version=source.version if source is not None else None,
            source_state="present" if source is not None else "deleted",
            sources=[ArtifactSource.model_validate(s) for s in row.source_refs],
        )

    def metadata(self, db, user, project_id, release_id) -> ArtifactReleaseMetadata:
        """現在の権限で固定版metadataを取得する。"""
        with self._transaction(db, user, project_id, "read") as (work, actor):
            return self._metadata(work, self._get(work, actor, project_id, release_id))

    def list(self, db, user, project_id, kind, source_id, page, page_size):
        """読めない種類を件数からも除外する。"""
        with self._transaction(db, user, project_id, "read") as (work, actor):
            allowed = [
                k
                for k, prefix in SOURCE_PERMISSIONS.items()
                if self.authorization.has_project_permission(
                    work,
                    user=actor,
                    project_id=project_id,
                    permission_code=f"{prefix}:read",
                )
            ]
            query = self.repository.visible(project_id, allowed)
            if kind is not None:
                query = query.where(ArtifactRelease.source_kind == kind)
            if source_id is not None:
                query = query.where(ArtifactRelease.source_id == source_id)
            total = work.scalar(select(func.count()).select_from(query.subquery())) or 0
            rows = work.scalars(
                query.order_by(
                    ArtifactRelease.created_at.desc(), ArtifactRelease.id.desc()
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            return ArtifactReleaseList(
                items=[ArtifactReleaseSummary.model_validate(r) for r in rows],
                total=total,
                page=page,
                page_size=page_size,
            )

    def nodes(self, db, user, project_id, release_id, cursor, page_size):
        """署名cursorと応答byte数の両方を検証して本文を分割する。"""
        with self._transaction(db, user, project_id, "read") as (work, actor):
            row = self._get(work, actor, project_id, release_id)
            after = decode_cursor(cursor, project_id, release_id) if cursor else -1
            if after >= row.node_count:
                raise BadRequestError(error_messages.ARTIFACT_CURSOR_INVALID)
            candidates = self.repository.nodes(work, release_id, after, page_size)
            items = []
            result = ArtifactNodePage(
                release_id=release_id, items=[], total=row.node_count, next_cursor=None
            )
            for candidate in candidates[:page_size]:
                proposed = [*items, ArtifactNodeRead.model_validate(candidate)]
                last = proposed[-1].ordinal
                next_cursor = (
                    encode_cursor(project_id, release_id, last)
                    if last < row.node_count - 1
                    else None
                )
                page = result.model_copy(
                    update={"items": proposed, "next_cursor": next_cursor}
                )
                if len(page.model_dump_json().encode()) > MAX_PAGE_BYTES:
                    if not items:
                        raise ArtifactReleaseTooLargeError()
                    break
                items, result = proposed, page
            return result
