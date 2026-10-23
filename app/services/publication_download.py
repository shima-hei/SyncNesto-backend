"""共有添付の初回認可→外部IO→最終再認可→短期URL発行。"""

import math
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.core.config import settings
from app.core.exceptions import ConflictError, ForbiddenError, TokenExpiredError
from app.models.session import UserSession
from app.repositories.publication_download import PublicationDownloadRepository
from app.schemas.publication_download import SharedFileDownload
from app.services.audit_log import AuditLogService
from app.services.publication_access_context import retry_publication_operation
from app.services.publication_delivery_storage import PublicationDeliveryStorage
from app.services.shared_publication import SharedPublicationService


class PublicationDownloadService:
    """新DLだけへ現在の本人許可を要求し、旧操作を置換しない。"""

    def __init__(self):
        """配信用clientは認可・feature確認後にだけ生成する。"""
        self.shared = SharedPublicationService()
        self.repository = PublicationDownloadRepository()
        self.storage_factory = PublicationDeliveryStorage

    @retry_publication_operation
    def _resolve(self, original, user, identity, file_id, expected):
        """ネットワークの前に対象と現在の許可を確定する。"""
        with self.shared.context.transaction(original, user) as (db, actor):
            row, revision = self.shared._get(db, original, actor, identity)
            if revision != expected:
                raise ConflictError()
            return self.repository.resolve(db, row, file_id)

    @retry_publication_operation
    def _issue(self, original, user, identity, file_id, expected, source, storage, key):
        """session失効と公開停止を待ち、確定後の許可だけで署名する。"""
        with self.shared.context.transaction(original, user, download=True) as (
            db,
            actor,
        ):
            self.shared._get(db, original, actor, identity)
            active = original.info["auth_session"]
            db.scalar(
                select(UserSession)
                .where(UserSession.id == active.id)
                .with_for_update(read=True)
            )
            row, revision = self.shared._get(db, original, actor, identity)
            if (
                revision != expected
                or self.repository.resolve(db, row, file_id) != source
            ):
                raise ConflictError()
            value = original.info.get("auth_token_exp")
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
            ):
                raise TokenExpiredError()
            token_expiry = datetime.fromtimestamp(value, UTC)
            now = db.scalar(select(func.clock_timestamp()))
            session = db.info["auth_session"]
            deadline = min(
                row["effective_expiry"],
                session.expires_at,
                session.absolute_expires_at,
                token_expiry,
            )
            if deadline <= now:
                raise TokenExpiredError()
            url, ttl, expiry = storage.sign(source, key, deadline, now)
            return SharedFileDownload(
                url=url,
                expires_in=ttl,
                expires_at=expiry,
                authorization_revision=revision,
            )

    def download(self, original, user, identity, file_id, data):
        """IO中の停止を妨げず、最終再検査を通った時だけURLを返す。"""
        if not settings.shared_publication_download_enabled:
            raise ForbiddenError()
        source = self._resolve(
            original, user, identity, file_id, data.expected_authorization_revision
        )
        storage = self.storage_factory()
        key = storage.ensure_copy(source)
        response = self._issue(
            original,
            user,
            identity,
            file_id,
            data.expected_authorization_revision,
            source,
            storage,
            key,
        )
        # 発行した事実のみ。受信・保存の完了や合意とは記録しない。
        AuditLogService().record(
            original,
            event_type="shared_attachment.download_url_issued",
            tenant_id=source.tenant_id,
            actor_user_id=user.id,
            project_id=source.project_id,
            resource_type="artifact_publication",
            metadata={
                "publication_id": str(identity),
                "file_id": str(file_id),
                "expires_at": response.expires_at.isoformat(),
            },
        )
        return response
