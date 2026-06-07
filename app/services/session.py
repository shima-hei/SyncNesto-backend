"""認証セッションのビジネスロジックを提供するモジュール。"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from fastapi import Response
from sqlalchemy.orm import Session

from app.core.auth_cookie import set_auth_cookie
from app.core.config import settings
from app.core.exceptions import InvalidTokenError, TokenExpiredError
from app.core.security import create_access_token, decode_access_token
from app.models.session import UserSession
from app.models.user import User
from app.repositories.session import UserSessionRepository
from app.services.audit_log import AuditEventType, AuditLogService

SESSION_REVOKE_REASON_EXPIRED = "expired"
SESSION_REVOKE_REASON_ABSOLUTE_EXPIRED = "absolute_expired"
SESSION_REVOKE_REASON_PERMISSION_CHANGED = "permission_changed"


class SessionService:
    """DBセッション検証、延長、失効を提供する。"""

    def __init__(
        self,
        repository: UserSessionRepository | None = None,
        audit_log_service: AuditLogService | None = None,
    ) -> None:
        """SessionServiceを初期化する。

        Args:
            repository: ユーザーセッションRepository。
            audit_log_service: 監査ログサービス。
        """
        self.repository = repository or UserSessionRepository()
        self.audit_log_service = audit_log_service or AuditLogService()

    def create_session_token(
        self,
        db: Session,
        user: User,
    ) -> tuple[UserSession, str]:
        """ログイン用セッションとJWTを作成する。

        Args:
            db: DBセッション。
            user: ログインユーザー。

        Returns:
            作成したDBセッションとアクセストークン。
        """
        user_session = self.repository.create(db, user)
        access_token = create_access_token(
            subject=user.email,
            session_id=user_session.id,
            expires_at=user_session.expires_at,
        )
        return user_session, access_token

    def get_session_id(self, payload: dict[str, object]) -> UUID:
        """JWT payloadからセッションIDを取得する。

        Args:
            payload: JWT payload。

        Returns:
            セッションID。

        Raises:
            InvalidTokenError: sidが存在しない、またはUUIDとして不正な場合。
        """
        sid = payload.get("sid")
        if not isinstance(sid, str):
            raise InvalidTokenError()

        try:
            return UUID(sid)
        except ValueError as exc:
            raise InvalidTokenError() from exc

    def validate_session(
        self,
        db: Session,
        payload: dict[str, object],
    ) -> UserSession:
        """JWT payloadに紐づくDBセッションを検証する。

        Args:
            db: DBセッション。
            payload: JWT payload。

        Returns:
            検証済みセッション。

        Raises:
            InvalidTokenError: セッションが存在しない、または失効済みの場合。
            TokenExpiredError: セッション期限が切れている場合。
        """
        session_id = self.get_session_id(payload)
        user_session = self.repository.get_by_id(db, session_id)
        if user_session is None or user_session.revoked_at is not None:
            raise InvalidTokenError()

        now = datetime.now(UTC)
        payload_expires_at = self._get_payload_datetime(payload, "exp")
        if payload_expires_at is not None and payload_expires_at <= now:
            self.revoke_expired_session(
                db,
                user_session,
                SESSION_REVOKE_REASON_EXPIRED,
            )
            raise TokenExpiredError()

        if user_session.absolute_expires_at <= now:
            self.revoke_expired_session(
                db,
                user_session,
                SESSION_REVOKE_REASON_ABSOLUTE_EXPIRED,
            )
            raise TokenExpiredError()

        if user_session.expires_at <= now:
            self.revoke_expired_session(
                db,
                user_session,
                SESSION_REVOKE_REASON_EXPIRED,
            )
            raise TokenExpiredError()

        return user_session

    def revoke_expired_session(
        self,
        db: Session,
        user_session: UserSession,
        reason: str,
    ) -> None:
        """期限切れセッションを失効状態にする。

        Args:
            db: DBセッション。
            user_session: 失効対象セッション。
            reason: 失効理由。
        """
        self.repository.revoke(db, user_session, reason)

    def should_refresh_session(self, user_session: UserSession) -> bool:
        """セッションを延長すべきか判定する。

        Args:
            user_session: 判定対象セッション。

        Returns:
            延長すべき場合はTrue。
        """
        remaining = user_session.expires_at - datetime.now(UTC)
        threshold = timedelta(minutes=settings.session_refresh_threshold_minutes)
        return remaining <= threshold

    def refresh_session_cookie(
        self,
        db: Session,
        response: Response,
        user: User,
        user_session: UserSession,
    ) -> None:
        """セッションを延長し、認証Cookieを再発行する。

        Args:
            db: DBセッション。
            response: HTTPレスポンス。
            user: 認証済みユーザー。
            user_session: 延長対象セッション。
        """
        refreshed_session = self.repository.extend(db, user_session)
        token = create_access_token(
            subject=user.email,
            session_id=refreshed_session.id,
            expires_at=refreshed_session.expires_at,
        )
        max_age_seconds = max(
            0,
            int((refreshed_session.expires_at - datetime.now(UTC)).total_seconds()),
        )
        set_auth_cookie(response, token, max_age_seconds)

    def revoke_token_session(
        self,
        db: Session,
        token: str,
        reason: str,
    ) -> UserSession | None:
        """JWTに紐づくDBセッションを失効する。

        Args:
            db: DBセッション。
            token: JWTアクセストークン。
            reason: 失効理由。

        Returns:
            失効したDBセッション。取得できない場合はNone。
        """
        try:
            payload = decode_access_token(token, verify_exp=False)
            session_id = self.get_session_id(payload)
        except (jwt.PyJWTError, InvalidTokenError):
            return None

        user_session = self.repository.get_by_id(db, session_id)
        if user_session is not None:
            return self.repository.revoke(db, user_session, reason)

        return None

    def revoke_user_sessions(
        self,
        db: Session,
        *,
        user_id: int,
        reason: str = SESSION_REVOKE_REASON_PERMISSION_CHANGED,
        actor_user_id: int | None = None,
        project_id: int | None = None,
    ) -> int:
        """ユーザーの有効セッションをすべて失効する。

        Args:
            db: DBセッション。
            user_id: セッションを失効する対象ユーザーID。
            reason: 失効理由。
            actor_user_id: 操作ユーザーID。
            project_id: 関連プロジェクトID。

        Returns:
            失効したセッション件数。
        """
        revoked_count = self.repository.revoke_all_by_user_id(
            db,
            user_id=user_id,
            reason=reason,
        )
        if revoked_count > 0:
            self.audit_log_service.record(
                db,
                event_type=AuditEventType.AUTH_SESSION_REVOKED,
                actor_user_id=actor_user_id,
                target_user_id=user_id,
                project_id=project_id,
                resource_type="session",
                metadata={
                    "reason": reason,
                    "revoked_count": revoked_count,
                },
            )

        return revoked_count

    def _get_payload_datetime(
        self,
        payload: dict[str, object],
        key: str,
    ) -> datetime | None:
        """JWT payload内の日時値をdatetimeへ変換する。"""
        value = payload.get(key)
        if isinstance(value, int | float):
            return datetime.fromtimestamp(value, UTC)

        return None
