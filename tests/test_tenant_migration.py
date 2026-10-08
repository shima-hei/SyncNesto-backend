"""別のテスト用DBで既存データの保持とDDL失敗時の原子的なrollbackを検証する。"""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models.requirement import Requirement, RequirementDocument
from app.models.task import Task


@pytest.mark.parametrize("fail", [False, True])
def test_legacy_data_survives_tenant_migration(fail):
    """成功時のIDと参照を維持し、失敗時は元のrevisionと全データを維持する。"""
    from app.db.session import engine

    name = "tenant_migration_" + uuid4().hex
    legacy_url = engine.url.set(database=name)
    admin = create_engine(
        engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    legacy = create_engine(legacy_url)
    env = {
        **os.environ,
        "DATABASE_URL": legacy_url.render_as_string(hide_password=False),
    }
    root = Path(__file__).resolve().parents[1]

    def migrate(revision: str):
        return subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", revision],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    try:
        result = migrate("b94f7d20c315")
        assert result.returncode == 0, result.stderr
        with legacy.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(id,email,name,hashed_password,is_active,version,user_type) "
                    "VALUES (1,'legacy@example.com','Legacy','preserved-hash',"
                    "true,1,'internal')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO roles (key,name,scope) "
                    "VALUES ('project_admin','Project Admin','project')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO projects (id,project_code,name,status,version) "
                    "VALUES (1,'LEGACY','Preserved Project','active',1)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO project_members "
                    "(project_id,user_id,role_id,version) VALUES (1,1,1,1)"
                )
            )
            if fail:
                connection.execute(
                    text(
                        "INSERT INTO roles (key,name,scope) "
                        "VALUES ('tenant_owner','Already exists','tenant')"
                    )
                )
        with Session(legacy) as db:
            task = Task(project_id=1, task_code="KEEP", title="Preserved Task")
            document = RequirementDocument(
                project_id=1, document_code="KEEP", title="Preserved Document"
            )
            db.add_all([task, document])
            db.flush()
            db.add(
                Requirement(
                    document_id=document.id,
                    requirement_code="KEEP",
                    requirement_type="functional",
                    title="Preserved Requirement",
                )
            )
            # 新しいtenant_id列を含むモデルを古いDBへ書かず、既存形式の通知を作成する。
            db.commit()
        with legacy.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO notifications (recipient_user_id,type,project_id,"
                    "target_type,target_id,event_key,snapshot,context) "
                    "VALUES (1,'assigned',1,'task','1','keep','{}','{}')"
                )
            )
        result = migrate("head")
        with legacy.connect() as connection:
            if fail:
                assert result.returncode != 0
                assert (
                    connection.scalar(text("SELECT version_num FROM alembic_version"))
                    == "b94f7d20c315"
                )
                assert connection.scalar(text("SELECT to_regclass('tenants')")) is None
            else:
                assert result.returncode == 0, result.stderr
                assert (
                    connection.scalar(text("SELECT version_num FROM alembic_version"))
                    == "e39b52d8c013"
                )
                assert (
                    connection.scalar(
                        text("SELECT password_change_required FROM users WHERE id=1")
                    )
                    is False
                )
                assert (
                    connection.scalar(
                        text("SELECT initial_password_expires_at FROM users WHERE id=1")
                    )
                    is None
                )
                assert (
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM projects p JOIN tenant_members m "
                            "ON m.tenant_id=p.tenant_id JOIN notifications n "
                            "ON n.tenant_id=p.tenant_id WHERE p.id=1 AND m.user_id=1"
                        )
                    )
                    == 1
                )
                assert (
                    connection.scalar(
                        text(
                            "SELECT r.key FROM tenant_members m JOIN roles r "
                            "ON r.id=m.role_id WHERE m.user_id=1"
                        )
                    )
                    == "tenant_member"
                )
            assert (
                connection.scalar(text("SELECT hashed_password FROM users WHERE id=1"))
                == "preserved-hash"
            )
            assert (
                connection.scalar(text("SELECT title FROM tasks WHERE id=1"))
                == "Preserved Task"
            )
            assert (
                connection.scalar(text("SELECT title FROM requirements WHERE id=1"))
                == "Preserved Requirement"
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT role_id FROM project_members "
                        "WHERE project_id=1 AND user_id=1"
                    )
                )
                == 1
            )
    finally:
        legacy.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()
