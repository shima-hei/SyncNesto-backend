"""固定本文を変えず、本人別公開と停止の業務操作を原子的に進める。"""

from datetime import datetime
from typing import NotRequired, TypedDict
from uuid import UUID, uuid5

from sqlalchemy import exists, func, select, text, update

from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    DuplicateResourceError,
    NotFoundError,
    VersionConflictError,
)
from app.models.user import User
from app.repositories.artifact_publication_access import PublicationAccessRepository
from app.repositories.guest_activity import PUBLICATION_SHARED, GuestActivityRepository
from app.schemas.artifact_publication_access import (
    GuestParticipationDecisionList,
    GuestParticipationDecisionRead,
    GuestParticipationList,
    GuestParticipationRead,
    GuestPublicationGrantList,
    GuestPublicationGrantRead,
    PublicationEventList,
    PublicationEventRead,
    PublicationGrantList,
    PublicationGrantRead,
    PublicationOperationResult,
    PublicationPublish,
)
from app.services.artifact_publication import ArtifactPublicationService
from app.services.artifact_publication_projection import NAMESPACE
from app.services.artifact_release import SOURCE_PERMISSIONS
from app.services.artifact_release_snapshot import digest
from app.services.audit_log import AuditLogService
from app.services.publication_access_context import (
    PublicationAccessContext,
    retry_publication_operation,
)


class DecisionShareRecipient(TypedDict):
    participation_id: UUID
    user_id: int
    name: str
    expires_at: datetime
    grant_version: int
    participant_version: int
    grant_id: NotRequired[UUID]


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
        payload = data.model_dump(mode="json")
        for recipient in payload.get("recipients", []):
            if recipient.get("download_allowed") is None:
                recipient.pop("download_allowed", None)
            if not recipient.get("comment_allowed"):
                recipient.pop("comment_allowed", None)
        fingerprint = digest(
            {
                "kind": kind,
                "target": str(identity),
                "data": payload,
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
            if min(
                row["expires_at"],
                row.get("participation_expires_at", row["expires_at"]),
            )
            <= now
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
        with self.context.transaction(original, user, project_id, write=True) as (
            db,
            actor,
        ):
            return self._publish(db, actor, project_id, identity, data, adding=adding)

    def publish_for_decision(self, db, actor, project_id, identity, data, request_id):
        """判断依頼と同じ公開トランザクション内で、付随許可を発行する。"""
        if any(recipient.download_allowed is None for recipient in data.recipients):
            raise BadRequestError("添付のダウンロード可否を指定してください。")
        result = self._publish(
            db,
            actor,
            project_id,
            identity,
            PublicationPublish(**data.model_dump(), request_id=request_id),
            adding=data.expected_state == "published",
            decision_only=True,
        )
        return [
            uuid5(result.operation_id, f"grant:{recipient.participation_id}")
            for recipient in data.recipients
        ]

    def preview_decision_recipients(
        self, db, actor, project_id, publication, sharing
    ) -> list[DecisionShareRecipient]:
        """依頼に付随する閲覧許可を検証し、保存前の宛先を返す。"""
        self._validate_share(publication, sharing, sharing.expected_state)
        now = db.scalar(select(func.clock_timestamp()))
        participants = self._confirmed_participants(db, project_id, sharing, now)
        candidates = self.repository.candidates(project_id, now, "").subquery()
        grants = self.repository.grants
        people = {
            row.id: row.name
            for row in db.execute(
                select(User.id, User.name)
                .join(candidates, candidates.c.user_id == User.id)
                .where(
                    candidates.c.id.in_(participants),
                    User.id != actor.id,
                    User.password_change_required.is_(False),
                    ~exists().where(
                        grants.c.project_id == project_id,
                        grants.c.publication_id == publication.id,
                        grants.c.participation_id == candidates.c.id,
                        grants.c.status == "active",
                    ),
                )
            )
        }
        if len(people) != len(participants) or any(
            recipient.download_allowed is None for recipient in sharing.recipients
        ):
            raise BadRequestError(
                "共有許可を新規作成できるゲストと操作の可否を指定してください。"
            )
        return [
            DecisionShareRecipient(
                participation_id=recipient.participation_id,
                user_id=participants[recipient.participation_id],
                name=people[participants[recipient.participation_id]],
                expires_at=recipient.expires_at,
                grant_version=1,
                participant_version=recipient.version,
            )
            for recipient in sharing.recipients
        ]

    def decision_users(self, original, project_id, publication_id, grant_ids, sharing):
        """公開境界の前に取得する本人行のIDを、同じ案件の許可か参加から解決する。"""
        grant, part = self.repository.grants, self.repository.participations
        query = (
            select(part.c.user_id)
            .join(grant, grant.c.participation_id == part.c.id)
            .where(
                grant.c.project_id == project_id,
                grant.c.publication_id == publication_id,
                grant.c.id.in_(grant_ids),
            )
        )
        if sharing is not None:
            query = query.union(
                select(part.c.user_id).where(
                    part.c.project_id == project_id,
                    part.c.id.in_(
                        [recipient.participation_id for recipient in sharing.recipients]
                    ),
                )
            )
        with original.get_bind().engine.connect() as connection:
            return tuple(connection.scalars(query))

    def _validate_share(self, publication, data, expected):
        self._check_version(
            publication.publication_version,
            data.publication_version,
            state=publication.publication_state,
        )
        if (
            publication.publication_state != expected
            or data.expected_state != expected
            or publication.public_digest != data.public_digest
        ):
            raise ConflictError()

    def _confirmed_participants(self, db, project_id, data, now):
        participants = {}
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
            participants[recipient.participation_id] = part["user_id"]
        return participants

    def _publish(
        self,
        db,
        actor,
        project_id,
        identity,
        data,
        *,
        adding=False,
        decision_only=False,
    ):
        """公開ロックを取得したトランザクションで本文・版・本人を照合する。"""
        row = self._publication(db, actor, project_id, identity)
        kind = "grants_added" if adding else "published"
        if decision_only:
            kind = f"decision_{kind}"
        operation, replay, fingerprint = self._operation(
            db, actor, project_id, kind, identity, data
        )
        if replay is not None:
            if decision_only:
                raise DuplicateResourceError()
            return replay
        assert operation is not None
        self._validate_share(row, data, "published" if adding else "prepared")
        now = db.scalar(select(func.clock_timestamp()))
        participants = self._confirmed_participants(db, project_id, data, now)
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
                    download_allowed=recipient.download_allowed,
                    comment_allowed=recipient.comment_allowed,
                    decision_only=decision_only,
                    created_by=actor.id,
                ),
            )
            if recipient.download_allowed is not None or recipient.comment_allowed:
                GuestActivityRepository().emit(
                    db,
                    tenant_id=db.info["tenant_id"],
                    project_id=project_id,
                    actor_id=actor.id,
                    recipient_id=participants[recipient.participation_id],
                    grant_id=grant,
                    publication_id=identity,
                    event_id=grant,
                    kind=PUBLICATION_SHARED,
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

    def revoke_for_decision(self, db, actor, project_id, binding, target, reason):
        """同じ依頼に付随して作成された現在の許可だけを失効する。"""
        grant = self.repository.grants
        db.execute(
            text("SELECT set_config('syncnesto.publication_actor',:actor,true)"),
            {"actor": str(actor.id)},
        )
        db.execute(
            update(grant)
            .where(
                grant.c.project_id == project_id,
                grant.c.decision_only.is_(True),
                grant.c.status == "active",
                exists(
                    select(binding.c.grant_id).where(
                        target,
                        binding.c.project_id == grant.c.project_id,
                        binding.c.grant_id == grant.c.id,
                        binding.c.publication_id == grant.c.publication_id,
                        binding.c.participation_id == grant.c.participation_id,
                    )
                ),
            )
            .values(
                status="revoked",
                version=grant.c.version + 1,
                revoked_at=func.clock_timestamp(),
                revoked_by=actor.id,
                revocation_reason=reason,
            )
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
    def change_participation_expiry(self, original, user, project_id, identity, data):
        """有効な世代だけを変更し、公開許可ごとの期限は保持する。"""
        with self.context.transaction(
            original,
            user,
            project_id,
            write=True,
            qualifying=True,
            management="project:invite_member",
        ) as (db, actor):
            row = self.repository.participation(db, project_id, identity)
            if row is None:
                raise NotFoundError()
            kind = "participation_expiry_changed"
            operation, replay, fingerprint = self._operation(
                db, actor, project_id, kind, identity, data
            )
            if replay is not None:
                return replay
            self._check_version(row["version"], data.version)
            now = db.scalar(select(func.clock_timestamp()))
            if self._effective(row, now) != "active" or data.expires_at <= now:
                raise ConflictError()
            self.repository.change_participation_expiry(
                db, project_id, identity, data.expires_at
            )
            return self._record(
                db,
                actor,
                project_id,
                operation,
                fingerprint,
                kind,
                identity,
                "active",
                row["version"] + 1,
                data,
                participation_id=identity,
            )

    def _participant_grants_query(self, db, actor, project_id, identity):
        self.context.permission(db, actor, project_id, "artifact_publication:read")
        self.context.permission(db, actor, project_id, "artifact_release:read")
        if self.repository.participation(db, project_id, identity) is None:
            raise NotFoundError()
        allowed = {
            permission
            for permission in set(SOURCE_PERMISSIONS.values()) | {"document"}
            if self.context.authorization.has_project_permission(
                db,
                user=actor,
                project_id=project_id,
                permission_code=f"{permission}:read",
            )
        }
        kinds = [
            kind
            for kind, permission in SOURCE_PERMISSIONS.items()
            if permission in allowed
        ]
        read_documents = "document" in allowed
        return (
            self.repository.participant_grants_query(
                project_id, identity, kinds, read_documents
            ),
            kinds,
            read_documents,
        )

    @retry_publication_operation
    def participant_grants(self, original, user, project_id, identity, page, page_size):
        """ゲスト管理からも、担当者が閲覧可能な共有許可を確認する。"""
        with self.context.transaction(
            original, user, project_id, management="project:invite_member"
        ) as (db, actor):
            query, _, _ = self._participant_grants_query(
                db, actor, project_id, identity
            )
            now = db.scalar(select(func.clock_timestamp()))
            g = self.repository.grants
            rows, total = self.repository.page(
                db, query, [g.c.created_at.desc(), g.c.id], page, page_size
            )
            return GuestPublicationGrantList(
                items=[
                    GuestPublicationGrantRead(
                        **row, effective_status=self._effective(row, now)
                    )
                    for row in rows
                ],
                total=total,
                page=page,
                page_size=page_size,
            )

    @retry_publication_operation
    def participant_decisions(
        self, original, user, project_id, identity, page, page_size
    ):
        """対象の参加世代に対する、読める判断履歴だけを返す。"""
        with self.context.transaction(
            original, user, project_id, management="project:invite_member"
        ) as (db, actor):
            self.context.permission(db, actor, project_id, "requirement:read")
            grants, kinds, read_documents = self._participant_grants_query(
                db, actor, project_id, identity
            )
            query = self.repository.participant_decisions_query(
                grants, kinds, read_documents
            )
            columns = query.selected_columns
            rows, total = self.repository.page(
                db,
                query,
                [columns.created_at.desc(), columns.id, columns.kind],
                page,
                page_size,
            )
            return GuestParticipationDecisionList(
                items=[
                    GuestParticipationDecisionRead.model_validate(row) for row in rows
                ],
                total=total,
                page=page,
                page_size=page_size,
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
