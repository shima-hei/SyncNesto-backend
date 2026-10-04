"""DBエンジンとセッション生成処理を定義するモジュール。"""

import os
import ssl
from collections.abc import Generator

import certifi
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db import tenant_scope  # noqa: F401

connect_args = {}
if settings.app_env == "production":
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


def get_db() -> Generator[Session]:
    """リクエストごとのDBセッションを提供する。

    Yields:
        DBセッション。
    """
    db = session_local()
    try:
        yield db
    finally:
        db.close()
