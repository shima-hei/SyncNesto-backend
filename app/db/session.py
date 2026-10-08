"""DBエンジンとセッション生成処理を定義するモジュール。"""

import os
import ssl
from collections.abc import Generator
from functools import lru_cache

import certifi
import jwt
from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.error_messages import AUTHENTICATION_CONTEXT_CHANGED
from app.core.security import DEMO_AUDIENCE, decode_access_token
from app.db import (
    demo_scope,  # noqa: F401
    tenant_scope,  # noqa: F401
)

connect_args = {}
if settings.is_public_environment:
    settings.validate_production()
    ca_file = (
        os.getenv("PGSSLROOTCERT")
        or ssl.get_default_verify_paths().cafile
        or certifi.where()
    )
    if not ca_file:
        raise RuntimeError("Production requires a trusted PostgreSQL CA bundle")
    connect_args["sslrootcert"] = ca_file

engine = create_engine(
    settings.database_url, echo=settings.sql_echo, connect_args=connect_args
)
session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@lru_cache(maxsize=1)
def _demo_factory(database_url: str) -> sessionmaker[Session]:
    """専用接続を遅延生成し、通常engineを変更しない。"""
    demo_engine = create_engine(
        database_url, echo=settings.sql_echo, connect_args=connect_args
    )
    return sessionmaker(
        autocommit=False, autoflush=False, bind=demo_engine, info={"data_realm": "demo"}
    )


def demo_session_local() -> Session:
    """受付停止中も回収に使える専用DBセッションを作る。"""
    settings.validate_demo_database()
    return _demo_factory(settings.demo_database_url)()


def verified_request_realm(request: Request) -> str | None:
    """署名とaudienceを検証したCookieだけで接続を選ぶ。"""
    token = request.cookies.get(settings.auth_cookie_name)
    if not token and settings.allow_authorization_header:
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() == "bearer":
            token = value
    if not token:
        return None
    try:
        payload = decode_access_token(token, verify_exp=False)
        return "demo" if payload.get("aud") == DEMO_AUDIENCE else "normal"
    except jwt.PyJWTError:
        # 認証・logout側で不正tokenを処理する。未検証の内容をDB選択に使わない。
        return None


def request_is_demo(request: Request) -> bool:
    """共有レート制限の保存先も検証済みrealmに合わせる。"""
    return verified_request_realm(request) == "demo"


def get_normal_db() -> Generator[Session]:
    """通常ログイン・本人確認リンクは常に既存DBで扱う。"""
    with session_local() as db:
        yield db


def get_demo_db() -> Generator[Session]:
    """匿名デモの発行先を専用DBへ固定する。"""
    from app.core.exceptions import NotFoundError

    if not settings.demo_mode:
        raise NotFoundError()
    with demo_session_local() as db:
        yield db


def get_db(request: Request) -> Generator[Session]:
    """リクエストごとのDBセッションを提供する。

    Yields:
        DBセッション。
    """
    realm = verified_request_realm(request)
    expected = request.headers.get("X-Syncnesto-Data-Realm")
    if expected is not None and realm is not None and expected != realm:
        from app.core.exceptions import ForbiddenError

        # 別タブでCookieが通常/デモに変わった後の旧画面の操作を拒否する。
        raise ForbiddenError(AUTHENTICATION_CONTEXT_CHANGED)
    db = demo_session_local() if realm == "demo" else session_local()
    try:
        yield db
    finally:
        db.close()
