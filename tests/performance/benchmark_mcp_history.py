"""観測期間中に配布された旧revisionと現行を、同じMCP HTTPで比較する。"""

import hashlib
import json
import logging

from starlette.testclient import TestClient

from app.core.config import settings
from app.main import create_app
from app.models.task import Task
from scripts.summarize_cpu import summarize
from tests.helpers.auth import authorize_as
from tests.performance.benchmark_api import variant
from tests.performance.benchmark_mcp_cpu import DESTINATION, Collector
from tests.routers.test_mcp import connect

BASE = "8c69d813c80bfdcccb2b236c0ec413378e98ae9a"


def test_deployed_revision_comparison(
    db, monkeypatch, create_test_user, create_test_project, assign_project_role
):
    """8:50〜15:00に新旧版が混在した可能性を、条件を揃えて確認する。"""
    for flag in (
        "mcp_enabled",
        "mcp_development_tools_enabled",
        "mcp_read_context_enabled",
        "mcp_task_comments_enabled",
        "cpu_diagnostics_enabled",
    ):
        monkeypatch.setattr(settings, flag, True)
    monkeypatch.setattr(settings, "mcp_issuer_url", "http://127.0.0.1:8000")
    monkeypatch.setattr(settings, "mcp_resource_url", "http://127.0.0.1:8000/mcp")
    user, project = create_test_user(), create_test_project()
    assign_project_role(user=user, project=project, role_key="manager")
    db.add_all(
        [
            Task(project_id=project.id, title=f"CPU-{i}", task_code=f"CPU-{i}")
            for i in range(100)
        ]
    )
    db.commit()
    log = logging.getLogger("app.core.cpu_diagnostics")
    collector = Collector()
    result = {
        "base": BASE,
        "scope": "historical Service methods, current HTTP SDK",
        "scenarios": {},
        "complete": False,
    }
    with TestClient(create_app(), base_url="http://127.0.0.1:8000") as client:
        authorize_as(client, user, domain="127.0.0.1")
        _, token, _ = connect(client, project.id)
        client.cookies.clear()
        headers = {
            "Accept": "application/json, text/event-stream",
            "Authorization": "Bearer " + token["access_token"],
            "MCP-Protocol-Version": "2025-11-25",
        }
        log.addHandler(collector)
        log.setLevel(logging.INFO)
        try:
            for name, message in {
                "tools_list": {"method": "tools/list"},
                "tasks100": {
                    "method": "tools/call",
                    "params": {
                        "name": "list_tasks",
                        "arguments": {"project_id": project.id, "page_size": 100},
                    },
                },
            }.items():
                samples = {"before": [], "after": []}
                hashes = set()
                for before in (True, False, True, False):
                    with variant(monkeypatch, BASE, before):
                        for i in range(8):
                            response = client.post(
                                "/mcp",
                                headers=headers,
                                json={"jsonrpc": "2.0", "id": 1, **message},
                            )
                            assert response.status_code == 200, response.text
                            assert "error" not in response.json()
                            assert not response.json()["result"].get("isError")
                            hashes.add(hashlib.sha256(response.content).hexdigest())
                            if i >= 2:
                                samples["before" if before else "after"].append(
                                    collector.rows[-1]
                                )
                assert len(hashes) == 1, "HTTP response changed between revisions"
                result["scenarios"][name] = {
                    label: {"summary": summarize(rows), "samples": rows}
                    for label, rows in samples.items()
                }
                result["scenarios"][name]["body_sha256_equal"] = True
        finally:
            log.removeHandler(collector)
    result["complete"] = True
    DESTINATION.mkdir(parents=True, exist_ok=True)
    (DESTINATION / "historical-http.json").write_text(json.dumps(result, indent=2))
