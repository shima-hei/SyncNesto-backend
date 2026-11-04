"""専用DBでPR基点と変更後を同じデータ・HTTP条件で比較する手動計測。

実行: uv run python scripts/verify.py --profile full tests/performance/benchmark_api.py
通常のテスト収集対象外。成功時の比較は .verify/api-efficiency/measurement.json。
"""

import ast
import hashlib
import json
import statistics
import subprocess
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter, process_time

from sqlalchemy import event

from app.core.config import settings
from app.core.mcp import PLUGIN_CLIENT_ID
from app.models.mcp import McpConnection
from app.models.project import Project
from app.models.task import Task, TaskDependency
from app.models.tenant import Tenant, TenantMember
from app.services import (
    authorization,
    mcp_operations,
    requirement_trace,
    task_presentation,
    trash,
)
from tests.helpers.auth import authorize_as

ROOT = Path(__file__).resolve().parents[2]


def baseline_class(base: str, module, name: str):
    """Git基点のクラスだけを取り出し、設定や接続先を変更せずロードする。"""
    path = Path(module.__file__).relative_to(ROOT).as_posix()
    source = subprocess.check_output(["git", "show", f"{base}:{path}"], text=True)
    node = next(
        n
        for n in ast.parse(source).body
        if isinstance(n, ast.ClassDef) and n.name == name
    )
    namespace = dict(module.__dict__)
    namespace.update(
        TaskDependency=TaskDependency,
        Project=Project,
        Tenant=Tenant,
        TenantMember=TenantMember,
    )
    exec(
        compile(ast.Module(body=[node], type_ignores=[]), f"<baseline:{path}>", "exec"),
        namespace,
    )
    return namespace[name]


@contextmanager
def variant(monkeypatch, base: str, before: bool):
    """一つの標本群だけ基点の処理へ戻し、終了時は必ず現行処理へ戻す。"""
    with monkeypatch.context() as patch:
        if before:
            for module, name, methods in (
                (authorization, "AuthorizationService", ["has_project_permission"]),
                (mcp_operations, "McpOperationsService", ["catalog"]),
                (requirement_trace, "RequirementTraceService", ["page", "detail"]),
                (trash, "TrashService", ["list"]),
                (
                    task_presentation,
                    "TaskPresentationDataService",
                    [
                        "get_task_presentation_data",
                        "is_blocked",
                        "_is_unfinished_predecessor",
                    ],
                ),
            ):
                old = baseline_class(base, module, name)
                for method in methods:
                    patch.setattr(
                        getattr(module, name),
                        method,
                        getattr(old, method),
                        raising=False,
                    )
        yield


def test_compare_cpu_and_sql(
    client, db, monkeypatch, create_test_user, create_test_project, assign_project_role
):
    """前→後→前→後、各12標本のCPU・経過・SQL数と全応答一致を記録する。"""
    base = subprocess.check_output(
        ["git", "merge-base", "HEAD", "origin/main"], text=True
    ).strip()
    # 署名生成は実行し、時刻由来のURL差だけを固定する。
    signing_time = datetime.now(UTC).replace(tzinfo=None)
    monkeypatch.setattr("botocore.auth.get_current_datetime", lambda: signing_time)
    actor = create_test_user()
    project = create_test_project()
    assign_project_role(user=actor, project=project, role_key="manager")
    tasks = [
        Task(project_id=project.id, task_code=f"BENCH-{i}", title=f"計測対象{i}")
        for i in range(100)
    ]
    predecessor = Task(project_id=project.id, task_code="PRE", title="計測先行")
    db.add_all([*tasks, predecessor])
    db.flush()
    db.add_all(
        [
            TaskDependency(
                predecessor_task_id=predecessor.id,
                successor_task_id=t.id,
                dependency_type="finish_to_start",
            )
            for t in tasks[::3]
        ]
    )
    db.commit()
    authorize_as(client, actor)
    for flag in (
        "mcp_plugin_extended_tools_enabled",
        "mcp_read_context_enabled",
        "mcp_task_comments_enabled",
    ):
        monkeypatch.setattr(settings, flag, True)
    db.info.update(tenant_id=project.tenant_id, tenant_user_id=actor.id)
    service = mcp_operations.McpOperationsService()
    connection = McpConnection(client_id=PLUGIN_CLIENT_ID, project_ids=[project.id])
    scenarios = {
        "tasks_20": f"/projects/{project.id}/tasks?q=計測対象&page_size=20",
        "tasks_100": f"/projects/{project.id}/tasks?q=計測対象&page_size=100",
        "task_detail": f"/tasks/{tasks[0].id}",
        "member_users": f"/projects/{project.id}/member-users?limit=20",
        "unread_count": "/notifications/unread-count",
        "requirements": f"/projects/{project.id}/requirements",
        "requirement_trace": f"/projects/{project.id}/requirement-trace",
        "trash": f"/projects/{project.id}/trash",
        "mcp_catalog": None,
    }
    result = {
        "base": base,
        "scope": (
            "local serial process CPU; isolated PostgreSQL; warm requests; "
            "not Vercel billing or monthly savings"
        ),
        "authentication": (
            "valid server-side session via test helper; login/MFA CPU excluded"
        ),
        "scenarios": {},
        "complete": False,
    }
    destination = ROOT / ".verify" / "api-efficiency"
    destination.mkdir(parents=True, exist_ok=True)
    for name, url in scenarios.items():
        samples = {"before": [], "after": []}
        expected = None
        for before in (True, False, True, False):
            with variant(monkeypatch, base, before):
                for index in range(8):
                    queries = []

                    def count(
                        conn, cursor, statement, parameters, context, executemany
                    ):
                        queries.append(1)

                    event.listen(db.get_bind(), "before_cursor_execute", count)
                    cpu, wall = process_time(), perf_counter()
                    try:
                        if url is None:
                            body = service.catalog(db, connection, actor)
                        else:
                            response = client.get(url)
                            assert response.status_code == 200, (
                                name,
                                response.status_code,
                            )
                            body = response.json()
                        cpu_ms, wall_ms = (
                            (process_time() - cpu) * 1000,
                            (perf_counter() - wall) * 1000,
                        )
                    finally:
                        event.remove(db.get_bind(), "before_cursor_execute", count)
                    digest = hashlib.sha256(
                        json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
                    ).hexdigest()
                    expected = expected or digest
                    assert digest == expected, f"response changed: {name}"
                    if index >= 2:
                        samples["before" if before else "after"].append(
                            {"cpu_ms": cpu_ms, "wall_ms": wall_ms, "sql": len(queries)}
                        )
        result["scenarios"][name] = {
            label: {
                "samples": len(rows),
                "median_cpu_ms": round(statistics.median(r["cpu_ms"] for r in rows), 3),
                "median_wall_ms": round(
                    statistics.median(r["wall_ms"] for r in rows), 3
                ),
                "sql_min": min(r["sql"] for r in rows),
                "sql_max": max(r["sql"] for r in rows),
            }
            for label, rows in samples.items()
        }
        result["scenarios"][name]["response_sha256"] = expected
        (destination / "measurement.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
    result["complete"] = True
    (destination / "measurement.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2)
    )
