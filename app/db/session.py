"""DBエンジンとセッション生成処理を定義するモジュール。"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(settings.database_url, echo=settings.sql_echo)
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
