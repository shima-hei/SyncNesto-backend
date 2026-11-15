"""計装ON/OFFの応答・認証境界とCPU差分を隔離環境で確認する。"""

import json
from time import process_time_ns

from starlette.testclient import TestClient

from app.core.config import settings
from app.main import create_app
from tests.helpers.auth import authorize_as
from tests.performance.benchmark_mcp_cpu import DESTINATION
from tests.routers.test_mcp import connect


def test_mcp_probe_transparency(
    db, monkeypatch, create_test_user, create_test_project, assign_project_role
):
    """同じ資格情報でON/OFFの本文一致・失敗境界・追加CPUを比較する。"""
    monkeypatch.setattr(settings, "mcp_enabled", True)
    monkeypatch.setattr(settings, "mcp_issuer_url", "http://127.0.0.1:8000")
    monkeypatch.setattr(settings, "mcp_resource_url", "http://127.0.0.1:8000/mcp")
    actor, project = create_test_user(), create_test_project()
    assign_project_role(user=actor, project=project, role_key="manager")
    monkeypatch.setattr(settings, "cpu_diagnostics_enabled", True)
    on_app = create_app()
    monkeypatch.setattr(settings, "cpu_diagnostics_enabled", False)
    off_app = create_app()
    samples = {"off": [], "on": []}
    with TestClient(on_app, base_url="http://127.0.0.1:8000") as on:
        authorize_as(on, actor, domain="127.0.0.1")
        _, tokens, _ = connect(on, project.id)
        on.cookies.clear()
        headers = {
            "Authorization": "Bearer " + tokens["access_token"],
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-11-25",
        }
        message = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "list_projects", "arguments": {}},
        }
        with TestClient(off_app, base_url="http://127.0.0.1:8000") as off:
            expected = None
            for enabled in (False, True, False, True):
                client = on if enabled else off
                for index in range(22):
                    start = process_time_ns()
                    response = client.post("/mcp", json=message, headers=headers)
                    cpu_ms = (process_time_ns() - start) / 1e6
                    assert response.status_code == 200
                    expected = expected or response.content
                    assert response.content == expected
                    if index >= 2:
                        samples["on" if enabled else "off"].append(cpu_ms)
            boundaries = []
            for extra in (
                {"Authorization": "Bearer invalid"},
                {"Cookie": "blocked=dummy"},
                {"Origin": "https://invalid.example"},
            ):
                responses = [
                    c.post("/mcp", json=message, headers={**headers, **extra})
                    for c in (off, on)
                ]
                assert responses[0].status_code == responses[1].status_code
                assert responses[0].content == responses[1].content
                boundaries.append(responses[0].status_code)
            assert boundaries == [401, 400, 403]
    DESTINATION.mkdir(parents=True, exist_ok=True)
    (DESTINATION / "mcp-probe-overhead.json").write_text(
        json.dumps(
            {
                "process_cpu_ms": samples,
                "responses_equal": True,
                "boundary_statuses": boundaries,
            },
            indent=2,
        )
    )
