"""pytest共通設定。"""

from collections.abc import Callable, Generator
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from dotenv import load_dotenv
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
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


def pytest_sessionstart() -> None:
    """pytestセッション開始時にテストDBを準備する。"""
    load_dotenv(PROJECT_ROOT / ".env.test", override=True)
    start_test_database()
    wait_for_test_database()
    run_migrations()


def pytest_sessionfinish() -> None:
    """pytestセッション終了時にテストDBを破棄する。"""
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
    """各テストの前にDB内のデータと採番をリセットする。"""
    from app.db.base import Base
    from app.db.session import engine

    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(
                text(f'TRUNCATE TABLE "{table.name}" RESTART IDENTITY CASCADE')
            )


@pytest.fixture(autouse=True)
def seed_rbac_data(clean_database: None) -> None:
    """各テストの前にRBAC初期データを投入する。

    Args:
        clean_database: DBリセットfixture。
    """
    from app.repositories.rbac import RbacRepository
    from scripts.seed_rbac import seed_roles_and_permissions

    seed_roles_and_permissions(RbacRepository())


@pytest.fixture
def db() -> Generator[Session, None, None]:
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


@pytest.fixture
def create_test_user(db: Session) -> Callable[..., "User"]:
    """テスト用ユーザーをDBへ直接作成するfixture。

    Args:
        db: テスト用DBセッション。

    Returns:
        任意のemail/name/passwordでユーザーを作成する関数。
    """
    from app.core.security import get_password_hash
    from app.repositories.rbac import RbacRepository
    from app.models.user import User

    def _create_test_user(
        *,
        email: str = "user@example.com",
        name: str = "User Name",
        password: str = "password123",
        system_role: str | None = None,
    ) -> User:
        user = User(
            email=email,
            name=name,
            hashed_password=get_password_hash(password),
        )
        db.add(user)
        db.flush()

        if system_role is not None:
            repository = RbacRepository()
            role = repository.get_role_by_key_scope(
                db,
                key=system_role,
                scope="system",
            )
            if role is None:
                raise RuntimeError(f"system role not found: {system_role}")
            repository.assign_role_to_user(db, user=user, role=role)

        db.commit()
        db.refresh(user)
        return user

    return _create_test_user


@pytest.fixture
def create_test_project(db: Session) -> Callable[..., "Project"]:
    """テスト用プロジェクトをDBへ直接作成するfixture。

    Args:
        db: テスト用DBセッション。

    Returns:
        任意のname/descriptionでプロジェクトを作成する関数。
    """
    from app.models.project import Project

    def _create_test_project(
        *,
        project_code: str | None = None,
        name: str = "Project Name",
        description: str | None = "Project Description",
        status: str = "active",
    ) -> Project:
        project = Project(
            project_code=project_code or f"PRJ-{uuid4().hex[:8].upper()}",
            name=name,
            description=description,
            status=status,
        )
        db.add(project)
        db.commit()
        db.refresh(project)
        return project

    return _create_test_project


@pytest.fixture
def assign_project_role(db: Session) -> Callable[..., "ProjectMember"]:
    """テスト用プロジェクトロールをユーザーへ付与するfixture。

    Args:
        db: テスト用DBセッション。

    Returns:
        プロジェクトメンバーを作成する関数。
    """
    from app.models.project import Project, ProjectMember
    from app.models.user import User
    from app.repositories.rbac import RbacRepository

    def _assign_project_role(
        *,
        user: User,
        project: Project,
        role_key: str,
    ) -> ProjectMember:
        repository = RbacRepository()
        role = repository.get_role_by_key_scope(
            db,
            key=role_key,
            scope="project",
        )
        if role is None:
            raise RuntimeError(f"project role not found: {role_key}")

        member = ProjectMember(
            project_id=project.id,
            user_id=user.id,
            role_id=role.id,
        )
        db.add(member)
        db.commit()
        db.refresh(member)
        return member

    return _assign_project_role


@pytest.fixture
def create_test_requirement_document(
    db: Session,
) -> Callable[..., "RequirementDocument"]:
    """テスト用要件定義書をDBへ直接作成するfixture。

    Args:
        db: テスト用DBセッション。

    Returns:
        任意のproject/title/document_codeで要件定義書を作成する関数。
    """
    from app.models.project import Project
    from app.models.requirement import RequirementDocument

    def _create_test_requirement_document(
        *,
        project: Project,
        title: str = "Requirement Document",
        document_code: str | None = None,
        status: str = "draft",
    ) -> RequirementDocument:
        document = RequirementDocument(
            project_id=project.id,
            title=title,
            document_code=document_code or f"RD-{uuid4().hex[:8].upper()}",
            status=status,
        )
        db.add(document)
        db.commit()
        db.refresh(document)
        return document

    return _create_test_requirement_document
