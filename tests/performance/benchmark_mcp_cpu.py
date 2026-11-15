"""隔離DB・実SDK HTTP・本物の認証による手動CPU診断。通常収集対象外。

uv run python scripts/verify.py --profile full tests/performance/benchmark_mcp_cpu.py
"""

import cProfile
import json
import logging
import pstats
from contextlib import nullcontext
from pathlib import Path
from time import perf_counter_ns, process_time_ns, sleep
from uuid import uuid4

from starlette.testclient import TestClient

from app.core.config import settings
from app.core.cpu_diagnostics import Measurement, current, phase
from app.main import create_app
from app.models.document import ProjectDocument
from app.models.task import Task
from app.models.test_design import TestDesign as Design
from scripts.summarize_cpu import summarize
from tests.helpers.auth import authorize_as
from tests.routers.test_mcp import connect

DESTINATION = Path(__file__).resolve().parents[2] / ".verify/mcp-cpu-20261105"


class Collector(logging.Handler):
    """診断の数値ログだけをメモリに集める。通常ログを保存しない。"""

    def __init__(self):
        super().__init__()
        self.rows = []

    def emit(self, record):
        self.rows.append(json.loads(record.getMessage()))


def test_serial_http_cpu(
    db,
    monkeypatch,
    create_test_user,
    create_test_project,
    assign_project_role,
    create_test_requirement_document,
    create_test_requirement,
):
    """A〜H、全読取tool、隔離書込、OFF/ONを同じデータで比較する。"""
    DESTINATION.mkdir(parents=True, exist_ok=True)
    for flag in (
        "mcp_enabled",
        "mcp_development_tools_enabled",
        "mcp_plugin_extended_tools_enabled",
        "mcp_read_context_enabled",
        "mcp_task_comments_enabled",
        "cpu_diagnostics_enabled",
    ):
        monkeypatch.setattr(settings, flag, True)
    monkeypatch.setattr(settings, "mcp_issuer_url", "http://127.0.0.1:8000")
    monkeypatch.setattr(settings, "mcp_resource_url", "http://127.0.0.1:8000/mcp")
    monkeypatch.setenv("PROJECT_OBJECTIVES_ENABLED", "true")
    monkeypatch.setenv("PROJECT_TRACEABILITY_ENABLED", "true")
    actor, project = create_test_user(), create_test_project()
    assign_project_role(user=actor, project=project, role_key="manager")
    requirement_doc = create_test_requirement_document(project=project)
    requirement = create_test_requirement(document=requirement_doc)
    tasks = [
        Task(project_id=project.id, task_code=f"CPU-{i}", title=f"計測{i}")
        for i in range(100)
    ]
    document = ProjectDocument(
        project_id=project.id,
        title="診断文書",
        body=("# 計測\n本文\n" * 10000)[:100000],
        created_by=actor.id,
        updated_by=actor.id,
    )
    design = Design(
        project_id=project.id, name="診断設計", created_by=actor.id, updated_by=actor.id
    )
    db.add_all([*tasks, document, design])
    db.commit()
    log = logging.getLogger("app.core.cpu_diagnostics")
    collector = Collector()
    app = create_app()
    with TestClient(app, base_url="http://127.0.0.1:8000") as browser:
        authorize_as(browser, actor, domain="127.0.0.1")
        _, tokens, _ = connect(browser, project.id)
        with nullcontext(browser) as rpc:
            normal = TestClient(app, base_url="http://127.0.0.1:8000")
            normal.cookies.update(browser.cookies)
            rpc.cookies.clear()
            headers = {
                "Authorization": "Bearer " + tokens["access_token"],
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-11-25",
            }

            def call(method, params=None):
                response = rpc.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": method,
                        **({"params": params} if params else {}),
                    },
                    headers=headers,
                )
                assert response.status_code == 200, (method, response.status_code)
                value = response.json()
                assert "error" not in value, value
                assert not value["result"].get("isError"), value
                return response

            def init():
                return call(
                    "initialize",
                    {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {
                            "name": "isolated-cpu-diagnostic",
                            "version": "1",
                        },
                    },
                )

            init()
            catalog = call("tools/list").json()["result"]["tools"]
            args = {"project_id": project.id}
            cases = {
                "B_health": lambda: rpc.get("/"),
                "C_initialize": init,
                "D_tools_list": lambda: call("tools/list"),
                "oauth_metadata": lambda: rpc.get(
                    "/.well-known/oauth-authorization-server"
                ),
                "resource_metadata": lambda: rpc.get(
                    "/.well-known/oauth-protected-resource/mcp"
                ),
                "G_rest_tasks100": lambda: normal.get(
                    f"/projects/{project.id}/tasks?page_size=100"
                ),
            }
            tools = {
                "list_projects": {},
                "get_project_context": args,
                "list_requirement_documents": args,
                "list_requirements": {**args, "document_id": requirement_doc.id},
                "list_requirement_sections": {
                    **args,
                    "document_id": requirement_doc.id,
                },
                "get_requirement_target": {
                    **args,
                    "target_type": "requirement_item",
                    "target_id": requirement.id,
                },
                "list_requirement_comments": {
                    **args,
                    "target_type": "document",
                    "target_id": requirement_doc.id,
                },
                "list_test_designs": args,
                "get_test_design": {
                    **args,
                    "design_id": design.id,
                    "component": "items",
                },
                "list_test_design_comments": {**args, "design_id": design.id},
                "list_tasks": {**args, "page_size": 100},
                "get_task": {**args, "task_id": tasks[0].id},
                "list_task_dependencies": {**args, "task_id": tasks[0].id},
                "list_milestones": args,
                "list_task_comments": {**args, "task_id": tasks[0].id},
                "get_project_brief": args,
                "search_project_items": {**args, "query": "計測"},
                "get_requirement_context": {**args, "requirement_id": requirement.id},
                "list_project_documents": args,
                "get_project_document": {**args, "document_id": document.id},
                "get_project_document_content": {
                    **args,
                    "document_id": document.id,
                    "mode": "outline",
                },
            }
            for name, arguments in tools.items():
                cases[name] = lambda n=name, a=arguments: call(
                    "tools/call", {"name": n, "arguments": a}
                )

            def patch_document():
                db.refresh(document)
                first = document.body[:1]
                return call(
                    "tools/call",
                    {
                        "name": "patch_project_document",
                        "arguments": {
                            **args,
                            "document_id": document.id,
                            "version": document.version,
                            "idempotency_key": str(uuid4()),
                            "edits": [
                                {
                                    "start": 0,
                                    "end": 1,
                                    "expected_text": first,
                                    "replacement": "#" if first != "#" else "A",
                                }
                            ],
                        },
                    },
                )

            exposed_reads = {
                t["name"] for t in catalog if t["annotations"]["readOnlyHint"]
            }
            assert set(tools) == exposed_reads, set(tools) ^ exposed_reads
            log.addHandler(collector)
            log.setLevel(logging.INFO)
            runs = {}
            cpu, wall = process_time_ns(), perf_counter_ns()
            sleep(0.25)
            runs["A_idle"] = {
                "http_calls": 0,
                "wall_ms": (perf_counter_ns() - wall) / 1e6,
                "process_cpu_ms": (process_time_ns() - cpu) / 1e6,
                "db_queries": 0,
                "response_bytes": 0,
            }
            try:
                for name, action in cases.items():
                    rows, external = [], []
                    for index in range(13):
                        previous = len(collector.rows)
                        cpu, wall = process_time_ns(), perf_counter_ns()
                        response = action()
                        elapsed = {
                            "cpu_ms": (process_time_ns() - cpu) / 1e6,
                            "wall_ms": (perf_counter_ns() - wall) / 1e6,
                        }
                        assert response.status_code == 200, (name, response.status_code)
                        assert len(collector.rows) == previous + 1, name
                        if index:
                            rows.append(collector.rows[-1])
                            external.append(elapsed)
                    runs[name] = {
                        "first": collector.rows[-13],
                        "warm": summarize(rows),
                        "client_process_samples": external,
                        "samples": rows,
                    }
                patch_samples = []
                for _ in range(5):
                    patch_document()
                    patch_samples.append(collector.rows[-1])
                runs["isolated_patch_document"] = {
                    "warm": summarize(patch_samples),
                    "samples": patch_samples,
                }
                # 観測OFF/ONの影響。同じclientと入力で交互に4群ずつ測る。
                overhead = {"off": [], "on": []}
                middleware = app.middleware_stack
                assert middleware is not None
                # 計装の有無は別appで比較。token・DB・SDK設定は同じ。
                monkeypatch.setattr(settings, "cpu_diagnostics_enabled", False)
                off_app = create_app()
                with TestClient(off_app, base_url="http://127.0.0.1:8000") as off:
                    for enabled in (False, True, False, True):
                        selected = rpc if enabled else off
                        for i in range(22):
                            cpu = process_time_ns()
                            response = selected.get("/")
                            elapsed = (process_time_ns() - cpu) / 1e6
                            assert response.status_code == 200
                            if i >= 2:
                                overhead["on" if enabled else "off"].append(elapsed)
                # worker thread内をCPU時計でprofile。タイミング標本へ混ぜない。
                from app import mcp as embedded

                original_threadpool = embedded.run_in_threadpool
                profile_rows = []

                async def profiled_threadpool(func, *a, **kw):
                    def worker():
                        profile = cProfile.Profile(timer=process_time_ns, timeunit=1e-9)
                        value = profile.runcall(func, *a, **kw)
                        top = sorted(
                            getattr(pstats.Stats(profile), "stats").items(),
                            key=lambda item: item[1][3],
                            reverse=True,
                        )[:25]
                        profile_rows.append(
                            [
                                {
                                    "file": Path(k[0]).name,
                                    "line": k[1],
                                    "function": k[2],
                                    "calls": v[1],
                                    "self_cpu_ms": v[2] * 1000,
                                    "cumulative_cpu_ms": v[3] * 1000,
                                }
                                for k, v in top
                            ]
                        )
                        return value

                    return await original_threadpool(worker)

                monkeypatch.setattr(embedded, "run_in_threadpool", profiled_threadpool)
                call("tools/list")
                call(
                    "tools/call",
                    {"name": "list_tasks", "arguments": tools["list_tasks"]},
                )
                monkeypatch.setattr(embedded, "run_in_threadpool", original_threadpool)
                output = {
                    "complete": True,
                    "scope": "isolated serial TestClient HTTP",
                    "tools_exposed": len(catalog),
                    "read_tools": len(tools),
                    "runs": runs,
                    "overhead_health": overhead,
                    "profile_worker_cpu": profile_rows,
                    "limitations": [
                        "not Vercel billing",
                        "not production call counts",
                        "serialization measures inner MCP text JSON only",
                        "DB execute excludes fetch/ORM/checkout/commit",
                        "TestClient CPU includes client and server threads",
                    ],
                }
                (DESTINATION / "http.json").write_text(
                    json.dumps(output, ensure_ascii=False, indent=2)
                )
            finally:
                log.removeHandler(collector)


def test_phase_off_overhead():
    """ContextVar照会の残存コストを別の単独CPU loopで測る。"""
    result = {}
    for enabled in (False, True):
        token = current.set(Measurement() if enabled else None)
        start = process_time_ns()
        for _ in range(10000):
            with phase("noop"):
                pass
        result[str(enabled)] = (process_time_ns() - start) / 10000
        current.reset(token)
    DESTINATION.mkdir(parents=True, exist_ok=True)
    (DESTINATION / "phase-overhead.json").write_text(json.dumps(result))
