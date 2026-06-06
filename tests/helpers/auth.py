"""認証済みTestClientを作成するテストヘルパー。"""

from uuid import UUID

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.csrf import generate_csrf_token
from app.core.security import create_access_token
from app.db.session import session_local
from app.models.user import User
from app.repositories.session import UserSessionRepository


def create_session_token(user: User) -> tuple[str, UUID]:
    """テスト用セッションを作成してsid入りアクセストークンを返す。

    Args:
        user: 認証対象ユーザー。

    Returns:
        アクセストークンとセッションID。
    """
    with session_local() as db:
        managed_user = db.merge(user)
        user_session = UserSessionRepository().create(db, managed_user)
        session_id = user_session.id
        expires_at = user_session.expires_at
        access_token = create_access_token(
            subject=user.email,
            session_id=session_id,
            expires_at=expires_at,
        )
        return access_token, session_id


def authorize_as(
    client: TestClient,
    user: User,
    *,
    domain: str = "testserver.local",
    path: str = "/",
) -> UUID:
    """TestClientを指定ユーザーとして認証済みにする。

    Args:
        client: テスト用FastAPIクライアント。
        user: 認証対象ユーザー。
        domain: Cookie domain。
        path: Cookie path。

    Returns:
        作成したセッションID。
    """
    access_token, session_id = create_session_token(user)
    csrf_token = generate_csrf_token()
    client.cookies.set(
        settings.auth_cookie_name,
        access_token,
        domain=domain,
        path=path,
    )
    client.cookies.set(
        settings.csrf_cookie_name,
        csrf_token,
        domain=domain,
        path=path,
    )
    client.headers[settings.csrf_header_name] = csrf_token
    return session_id
