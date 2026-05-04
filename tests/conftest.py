"""pytest共通設定。"""

from pathlib import Path
import subprocess
import sys
import time

from dotenv import load_dotenv
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_command(command: list[str]) -> None:
    """コマンドをプロジェクトルートで実行する。

    Args:
        command: 実行するコマンド。
    """
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def start_test_database() -> None:
    """テスト用PostgreSQLコンテナを起動する。"""
    run_command(["docker", "compose", "--profile", "test", "up", "-d", "postgres_test"])


def wait_for_test_database() -> None:
    """テスト用PostgreSQLが接続可能になるまで待機する。"""
    command = [
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres_test",
        "pg_isready",
        "-U",
        "admin",
        "-d",
        "syncnesto_test",
    ]

    for _ in range(30):
        result = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
        if result.returncode == 0:
            return

        time.sleep(1)

    raise RuntimeError("test database is not ready")


def stop_test_database() -> None:
    """テスト用PostgreSQLコンテナを停止・削除する。"""
    subprocess.run(
        ["docker", "compose", "stop", "postgres_test"],
        cwd=PROJECT_ROOT,
        check=False,
    )
    subprocess.run(
        ["docker", "compose", "rm", "-f", "postgres_test"],
        cwd=PROJECT_ROOT,
        check=False,
    )


def run_migrations() -> None:
    """テストDBへAlembic migrationを適用する。"""
    run_command([sys.executable, "-m", "alembic", "upgrade", "head"])


def pytest_sessionstart(session: pytest.Session) -> None:
    """pytestセッション開始時にテストDBを準備する。

    Args:
        session: pytestセッション。
    """
    load_dotenv(PROJECT_ROOT / ".env.test", override=True)
    start_test_database()
    wait_for_test_database()
    run_migrations()


def pytest_sessionfinish(
    session: pytest.Session,
    exitstatus: int | pytest.ExitCode,
) -> None:
    """pytestセッション終了時にテストDBを破棄する。

    Args:
        session: pytestセッション。
        exitstatus: pytestの終了ステータス。
    """
    stop_test_database()


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClientを作成する。

    Returns:
        テスト用FastAPIクライアント。
    """
    from app.main import create_app

    return TestClient(create_app())


@pytest.fixture(autouse=True)
def clean_database() -> None:
    """各テストの前にDB内のデータを削除する。"""
    from app.db.base import Base
    from app.db.session import engine

    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())


@pytest.fixture
def db() -> Session:
    """テスト用DBセッションを作成する。

    Yields:
        テスト用DBセッション。
    """
    from app.db.session import session_local

    session = session_local()
    try:
        yield session
    finally:
        session.close()
