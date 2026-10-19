"""固定本文を変えず、本人別公開と停止の業務操作を原子的に進める。"""

from uuid import uuid5

from sqlalchemy import func, select, text

from app.core.exceptions import (
    ConflictError,
    DuplicateResourceError,
    ForbiddenError,
    NotFoundError,
    VersionConflictError,
)
from app.repositories.artifact_publication_access import PublicationAccessRepository
from app.schemas.artifact_publication_access import (
    GuestParticipationList,
    GuestParticipationRead,
    PublicationEventList,
    PublicationEventRead,
    PublicationGrantList,
    PublicationGrantRead,
    PublicationOperationResult,
)
from app.services import publication_access_context
from app.services.artifact_publication import ArtifactPublicationService
from app.services.artifact_publication_projection import NAMESPACE
from app.services.artifact_release_snapshot import digest
from app.services.audit_log import AuditLogService
from app.services.publication_access_context import (
    PublicationAccessContext,
    retry_publication_operation,
)


class ArtifactPublicationAccessService:
    """新しい許可だけで通常read/approveや組織所属を与えない。"""

    def __init__(self):
        """既存の固定本文認可と新しい可変状態の境界を分離する。"""
        self.context = PublicationAccessContext()
        self.repository = PublicationAccessRepository()
        self.preview = ArtifactPublicationService()

    def _publication(self, db, actor, project_id, identity, *, body=True):
        """本文のない停止操作と、全source readが必要な取得を区別する。"""
        row = self.repository.publication(db, project_id, identity)
        if row is None:
            raise NotFoundError()
        if body:
            self.context.permission(db, actor, project_id, "artifact_release:read")
            self.preview._readable(db, actor, project_id, row)
        return row

    def _operation(self, db, actor, project_id, kind, identity, data):
        """境界取得後に同一操作の再送を確認し、履歴の根IDを設定する。"""
        fingerprint = digest(
            {
                "kind": kind,
                "target": str(identity),
                "data": data.model_dump(mode="json"),
            }
        )
        previous = self.repository.previous(db, project_id, actor.id, data.request_id)
        if previous is not None:
            if previous["request_digest"] != fingerprint:
                raise DuplicateResourceError()
            return (
                None,
                PublicationOperationResult.model_validate(previous["result"]),
                fingerprint,
            )
        operation = uuid5(
            NAMESPACE, f"access:{project_id}:{actor.id}:{data.request_id}"
        )
        db.execute(
            text(
                "SELECT set_config('syncnesto.publication_actor',:actor,true),"
                "set_config('syncnesto.publication_operation',:operation,true),"
                "set_config('syncnesto.publication_reason',:reason,true)"
            ),
            {
                "actor": str(actor.id),
                "operation": str(operation),
                "reason": getattr(data, "reason", "confirmed_publication"),
            },
        )
        return operation, None, fingerprint

    def _record(
        self,
        db,
        actor,
        project_id,
        operation,
        fingerprint,
        kind,
        identity,
        state,
        version,
        data,
        *,
        publication_id=None,
        participation_id=None,
        grant_id=None,
    ):
        """実際のactorと操作結果を子イベントと一緒に保持する。"""
        result = PublicationOperationResult(
            id=identity, state=state, version=version, operation_id=operation
        )
        self.repository.root(
            db,
            dict(
                id=operation,
                project_id=project_id,
                publication_id=publication_id,
                participation_id=participation_id,
                grant_id=grant_id,
                event_kind=kind,
                actor_id=actor.id,
                actor_kind="user",
                reason=getattr(data, "reason", None),
                target_state=state,
                target_version=version,
                public_digest=getattr(data, "public_digest", None),
                operation_id=operation,
                is_root=True,
                request_id=data.request_id,
                request_digest=fingerprint,
                result=result.model_dump(mode="json"),
            ),
        )
        AuditLogService().record(
            db,
            event_type=f"artifact_publication.{kind}",
            actor_user_id=actor.id,
            project_id=project_id,
            resource_type="artifact_publication",
            metadata={"operation_id": str(operation), "target_id": str(identity)},
            commit=False,
        )
        return result

    @staticmethod
    def _check_version(version, requested, *, state=None):
        """409で本文や他人のmetadataを余分に返さない。"""
        if version != requested:
            raise VersionConflictError(
                {"version": version, **({"state": state} if state else {})}
            )

    @staticmethod
    def _effective(row, now):
        """期限切れを人の失効判断イベントへ変換しない。"""
        return (
            row["status"]
            if row["status"] == "revoked"
            else "expired"
            if row["expires_at"] <= now
            else "active"
        )

    @retry_publication_operation
    def recipients(self, original, user, project_id, q, page, page_size):
        """有効な参加台帳の候補だけを返す。"""
        with self.context.transaction(original, user, project_id) as (db, actor):
            self.context.permission(
                db, actor, project_id, "artifact_publication:publish"
            )
            now = db.scalar(select(func.clock_timestamp()))
            rows, total = self.repository.page(
                db,
                self.repository.candidates(project_id, now, q),
                [
                    self.repository.participations.c.display_label,
                    self.repository.participations.c.id,
                ],
                page,
                page_size,
            )
            return GuestParticipationList(
                items=[
                    GuestParticipationRead(
                        **r, effective_status=self._effective(r, now)
                    )
                    for r in rows
                ],
                total=total,
                page=page,
                page_size=page_size,
            )

    @retry_publication_operation
    def participations(self, original, user, project_id, page, page_size):
        """管理権限だけでは本文や公開件数を返さない。"""
        with self.context.transaction(
            original, user, project_id, management="project:invite_member"
        ) as (db, actor):
            now = db.scalar(select(func.clock_timestamp()))
            rows, total = self.repository.page(
                db,
                self.repository.participations_query(project_id),
                [
                    self.repository.participations.c.created_at.desc(),
                    self.repository.participations.c.id,
                ],
                page,
                page_size,
            )
            return GuestParticipationList(
                items=[
                    GuestParticipationRead(
                        **r, effective_status=self._effective(r, now)
                    )
                    for r in rows
                ],
                total=total,
                page=page,
                page_size=page_size,
            )

    @retry_publication_operation
    def publish(self, original, user, project_id, identity, data, *, adding=False):
        """全文確認・相手・期限を一括確認して、部分成功なしで許可する。"""
        if not publication_access_context.PUBLICATION_ACCESS_ENABLED:
            raise ForbiddenError()
        with self.context.transaction(original, user, project_id, write=True) as (
            db,
            actor,
        ):
            row = self._publication(db, actor, project_id, identity)
            kind = "grants_added" if adding else "published"
            operation, replay, fingerprint = self._operation(
                db, actor, project_id, kind, identity, data
            )
            if replay is not None:
                return replay
            assert operation is not None
            self._check_version(
                row.publication_version,
                data.publication_version,
                state=row.publication_state,
            )
            expected = "published" if adding else "prepared"
            if (
                row.publication_state != expected
                or data.expected_state != expected
                or row.public_digest != data.public_digest
            ):
                raise ConflictError()
            now = db.scalar(select(func.clock_timestamp()))
            for recipient in data.recipients:
                part = self.repository.participation(
                    db, project_id, recipient.participation_id
                )
                if part is None:
                    raise NotFoundError()
                self._check_version(part["version"], recipient.version)
                if (
                    self._effective(part, now) != "active"
                    or recipient.expires_at <= now
                    or recipient.expires_at > part["expires_at"]
                ):
                    raise ConflictError()
            if not adding:
                self.repository.update_publication(
                    db, project_id, identity, "published", row.publication_version + 1
                )
            for recipient in data.recipients:
                grant = uuid5(operation, f"grant:{recipient.participation_id}")
                self.repository.add_grant(
                    db,
                    dict(
                        id=grant,
                        project_id=project_id,
                        publication_id=identity,
                        participation_id=recipient.participation_id,
                        status="active",
                        version=1,
                        expires_at=recipient.expires_at,
                        created_by=actor.id,
                    ),
                )
            return self._record(
                db,
                actor,
                project_id,
                operation,
                fingerprint,
                kind,
                identity,
                "published",
                row.publication_version + (not adding),
                data,
                publication_id=identity,
            )

    @retry_publication_operation
    def withdraw(self, original, user, project_id, identity, data):
        """本文のreadを失っていても、公開全体を停止できる。"""
        with self.context.transaction(original, user, project_id, write=True) as (
            db,
            actor,
        ):
            row = self._publication(db, actor, project_id, identity, body=False)
            operation, replay, fingerprint = self._operation(
                db, actor, project_id, "withdrawn", identity, data
            )
            if replay is not None:
                return replay
            self._check_version(
                row.publication_version, data.version, state=row.publication_state
            )
            if row.publication_state == "withdrawn":
                raise ConflictError()
            self.repository.update_publication(
                db, project_id, identity, "withdrawn", row.publication_version + 1
            )
            return self._record(
                db,
                actor,
                project_id,
                operation,
                fingerprint,
                "withdrawn",
                identity,
                "withdrawn",
                row.publication_version + 1,
                data,
                publication_id=identity,
            )

    @retry_publication_operation
    def revoke_grant(self, original, user, project_id, identity, grant_id, data):
        """指定本人だけを失効し、他本人の許可は保持する。"""
        with self.context.transaction(original, user, project_id, write=True) as (
            db,
            actor,
        ):
            self._publication(db, actor, project_id, identity, body=False)
            row = self.repository.grant(db, project_id, identity, grant_id)
            if row is None:
                raise NotFoundError()
            operation, replay, fingerprint = self._operation(
                db, actor, project_id, "grant_revoked", grant_id, data
            )
            if replay is not None:
                return replay
            self._check_version(row["version"], data.version)
            if row["status"] != "active":
                raise ConflictError()
            self.repository.revoke(
                db,
                self.repository.grants,
                grant_id,
                actor.id,
                data.reason,
                db.scalar(select(func.clock_timestamp())),
            )
            return self._record(
                db,
                actor,
                project_id,
                operation,
                fingerprint,
                "grant_revoked",
                grant_id,
                "revoked",
                row["version"] + 1,
                data,
                publication_id=identity,
                participation_id=row["participation_id"],
                grant_id=grant_id,
            )

    @retry_publication_operation
    def revoke_participation(self, original, user, project_id, identity, data):
        """管理者が参加世代とその全許可を失効する。"""
        with self.context.transaction(
            original,
            user,
            project_id,
            write=True,
            qualifying=True,
            management="project:remove_member",
        ) as (db, actor):
            row = self.repository.participation(db, project_id, identity)
            if row is None:
                raise NotFoundError()
            operation, replay, fingerprint = self._operation(
                db, actor, project_id, "participation_revoked", identity, data
            )
            if replay is not None:
                return replay
            self._check_version(row["version"], data.version)
            if row["status"] != "active":
                raise ConflictError()
            self.repository.revoke(
                db,
                self.repository.participations,
                identity,
                actor.id,
                data.reason,
                db.scalar(select(func.clock_timestamp())),
            )
            return self._record(
                db,
                actor,
                project_id,
                operation,
                fingerprint,
                "participation_revoked",
                identity,
                "revoked",
                row["version"] + 1,
                data,
                participation_id=identity,
            )

    @retry_publication_operation
    def grants(self, original, user, project_id, identity, page, page_size):
        """全構成を現在読める社内メンバーだけに宛先を返す。"""
        with self.context.transaction(original, user, project_id) as (db, actor):
            self._publication(db, actor, project_id, identity)
            now = db.scalar(select(func.clock_timestamp()))
            rows, total = self.repository.page(
                db,
                self.repository.grants_query(project_id, identity),
                [
                    self.repository.grants.c.created_at.desc(),
                    self.repository.grants.c.id,
                ],
                page,
                page_size,
            )
            return PublicationGrantList(
                items=[
                    PublicationGrantRead(**r, effective_status=self._effective(r, now))
                    for r in rows
                ],
                total=total,
                page=page,
                page_size=page_size,
            )

    @retry_publication_operation
    def events(self, original, user, project_id, identity, page, page_size):
        """監査ログと区別した公開業務の履歴を返す。"""
        with self.context.transaction(original, user, project_id) as (db, actor):
            self._publication(db, actor, project_id, identity)
            rows, total = self.repository.page(
                db,
                self.repository.events_query(project_id, identity),
                [
                    self.repository.events.c.occurred_at.desc(),
                    self.repository.events.c.id,
                ],
                page,
                page_size,
            )
            return PublicationEventList(
                items=[PublicationEventRead.model_validate(r) for r in rows],
                total=total,
                page=page,
                page_size=page_size,
            )
