"""認証dependencyを定義するモジュール。"""

import jwt
from fastapi import Cookie, Depends, Header
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ForbiddenError, InvalidCredentialsError
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User
from app.repositories.user import UserRepository
from app.services.authorization import AuthorizationService


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


def get_current_user(
    access_token: str | None = Cookie(default=None, alias=settings.auth_cookie_name),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """アクセストークンから現在のログインユーザーを取得する。

    Args:
        access_token: 認証Cookieに含まれるアクセストークン。
        authorization: Authorizationヘッダーの値。
        db: DBセッション。

    Returns:
        現在のログインユーザー。

    Raises:
        InvalidCredentialsError: tokenがない、または認証できない場合。
    """
    token = access_token
    if token is None and settings.allow_authorization_header:
        token = extract_bearer_token(authorization)

    if token is None:
        raise InvalidCredentialsError()

    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError as exc:
        raise InvalidCredentialsError() from exc

    email = payload.get("sub")
    if not isinstance(email, str):
        raise InvalidCredentialsError()

    user = UserRepository().get_by_email(db, email)
    if user is None:
        raise InvalidCredentialsError()

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
