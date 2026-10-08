"""内部Serviceのcommitをsavepointに閉じ込め、MCP操作全体を原子的にする。"""

from collections.abc import Generator

from sqlalchemy.orm import Session

from app.db.session import engine


def get_mcp_db() -> Generator[Session, None, None]:
    """通常DBだけに接続。失敗時は監査・再送結果も含めて戻す。"""
    with engine.connect() as connection, connection.begin():
        with Session(
            bind=connection, autoflush=False, join_transaction_mode="create_savepoint"
        ) as db:
            db.info["mcp_outer_connection"] = connection
            yield db
            db.commit()
