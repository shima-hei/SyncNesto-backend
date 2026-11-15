"""権限やフィルタを変えず、既存のSQL条件構築だけをCPU時計で測る。"""

import json
from pathlib import Path
from time import process_time_ns
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.tenant_scope import scope_orm_statement, scoped_models
from app.models.task import Task


def test_condition_construction(db, create_test_user, create_test_project):
    """新Session・同じSession・SQL cache keyの作成を直列で比較する。"""
    actor, project = create_test_user(), create_test_project()
    result = {
        "models": len(scoped_models()),
        "scope_build_ms": [],
        "reuse_ms": [],
        "cache_key_ms": [],
    }
    for _ in range(30):
        with Session(
            info={"tenant_id": project.tenant_id, "tenant_user_id": actor.id}
        ) as session:

            def state():
                return SimpleNamespace(
                    session=session,
                    is_select=True,
                    is_update=False,
                    is_delete=False,
                    statement=select(Task),
                )

            fresh = state()
            cpu = process_time_ns()
            scope_orm_statement(fresh)
            result["scope_build_ms"].append((process_time_ns() - cpu) / 1e6)
            reused = state()
            cpu = process_time_ns()
            scope_orm_statement(reused)
            result["reuse_ms"].append((process_time_ns() - cpu) / 1e6)
            cpu = process_time_ns()
            assert fresh.statement._generate_cache_key() is not None
            result["cache_key_ms"].append((process_time_ns() - cpu) / 1e6)
    destination = Path(__file__).resolve().parents[2] / ".verify/mcp-cpu-20261105"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "tenant-scope.json").write_text(json.dumps(result, indent=2))
