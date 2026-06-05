"""認証dependencyを定義するモジュール。"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from fastapi import Cookie, Depends, Header, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import (
    AuthenticationRequiredError,
    ForbiddenError,
    InvalidTokenError,
    TokenExpiredError,
)
from app.core.security import create_access_token, decode_access_token
from app.db.session import get_db
from app.models.session import UserSession
from app.models.user import User
from app.repositories.session import UserSessionRepository
from app.repositories.user import UserRepository
from app.services.authorization import AuthorizationService

SESSION_REVOKE_REASON_EXPIRED = "expired"
SESSION_REVOKE_REASON_ABSOLUTE_EXPIRED = "absolute_expired"


def set_auth_cookie(response: Response, token: str, max_age_seconds: int) -> None:
    """認証Cookieを設定する。

    Args:
        response: HTTPレスポンス。
        token: Cookieへ保存するアクセストークン。
        max_age_seconds: Cookieの有効秒数。
    """
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        max_age=max_age_seconds,
        path="/",
    )


def delete_auth_cookie(response: Response) -> None:
    """認証Cookieを削除する。

    Args:
        response: HTTPレスポンス。
    """
    response.delete_cookie(
        key=settings.auth_cookie_name,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        path="/",
    )


def extract_bearer_token(authorization: str | None) -> str | None:
    """AuthorizationヘッダーからBearer tokenを取り出す。

    Args:
        authorization: Authorizationヘッダーの値。

    Returns:
        Bearer token。取得できない場合はNone。
    """
    if authorization is None:
        return None

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None

    return token


def get_payload_datetime(payload: dict[str, object], key: str) -> datetime | None:
    """JWT payload内の日時値をdatetimeへ変換する。

    Args:
        payload: JWT payload。
        key: 取得するpayload key。

    Returns:
        datetime。取得できない場合はNone。
    """
    value = payload.get(key)
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, UTC)

    return None


def get_session_id(payload: dict[str, object]) -> UUID:
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


def revoke_expired_session(
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
    UserSessionRepository().revoke(db, user_session, reason)


def validate_session(
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
    session_id = get_session_id(payload)
    user_session = UserSessionRepository().get_by_id(db, session_id)
    if user_session is None or user_session.revoked_at is not None:
        raise InvalidTokenError()

    now = datetime.now(UTC)
    payload_expires_at = get_payload_datetime(payload, "exp")
    if payload_expires_at is not None and payload_expires_at <= now:
        revoke_expired_session(db, user_session, SESSION_REVOKE_REASON_EXPIRED)
        raise TokenExpiredError()

    if user_session.absolute_expires_at <= now:
        revoke_expired_session(
            db,
            user_session,
            SESSION_REVOKE_REASON_ABSOLUTE_EXPIRED,
        )
        raise TokenExpiredError()

    if user_session.expires_at <= now:
        revoke_expired_session(db, user_session, SESSION_REVOKE_REASON_EXPIRED)
        raise TokenExpiredError()

    return user_session


def should_refresh_session(user_session: UserSession) -> bool:
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
    refreshed_session = UserSessionRepository().extend(db, user_session)
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


def get_current_user(
    response: Response,
    access_token: str | None = Cookie(default=None, alias=settings.auth_cookie_name),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """アクセストークンから現在のログインユーザーを取得する。

    Args:
        response: HTTPレスポンス。
        access_token: 認証Cookieに含まれるアクセストークン。
        authorization: Authorizationヘッダーの値。
        db: DBセッション。

    Returns:
        現在のログインユーザー。

    Raises:
        AuthenticationRequiredError: tokenがない場合。
        TokenExpiredError: tokenの有効期限が切れている場合。
        InvalidTokenError: tokenが不正な場合。
    """
    token = access_token
    if token is None and settings.allow_authorization_header:
        token = extract_bearer_token(authorization)

    if token is None:
        raise AuthenticationRequiredError()

    try:
        payload = decode_access_token(token, verify_exp=False)
    except jwt.PyJWTError as exc:
        raise InvalidTokenError() from exc

    user_session = validate_session(db, payload)

    email = payload.get("sub")
    if not isinstance(email, str):
        raise InvalidTokenError()

    user = UserRepository().get_by_email(db, email)
    if user is None or not user.is_active or user.id != user_session.user_id:
        raise InvalidTokenError()

    if access_token is not None and should_refresh_session(user_session):
        refresh_session_cookie(db, response, user, user_session)

    return user


def require_system_permission(permission_code: str):
    """システム権限を要求するDependencyを作成する。

    Args:
        permission_code: 要求する権限コード。

    Returns:
        認可済みユーザーを返すDependency。
    """

    def dependency(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        """システム権限を持つことを確認する。

        Args:
            current_user: 認証済みユーザー。
            db: DBセッション。

        Returns:
            認可済みユーザー。

        Raises:
            ForbiddenError: 権限がない場合。
        """
        if not AuthorizationService().has_system_permission(
            db,
            user=current_user,
            permission_code=permission_code,
        ):
            raise ForbiddenError()

        return current_user

    return dependency


def require_project_permission(permission_code: str):
    """プロジェクト権限を要求するDependencyを作成する。

    Args:
        permission_code: 要求する権限コード。

    Returns:
        認可済みユーザーを返すDependency。
    """

    def dependency(
        project_id: int,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        """プロジェクト権限を持つことを確認する。

        Args:
            project_id: 対象プロジェクトID。
            current_user: 認証済みユーザー。
            db: DBセッション。

        Returns:
            認可済みユーザー。

        Raises:
            ForbiddenError: 権限がない場合。
        """
        if not AuthorizationService().has_project_permission(
            db,
            user=current_user,
            project_id=project_id,
            permission_code=permission_code,
        ):
            raise ForbiddenError()

        return current_user

    return dependency
