"""数値の正しさより先に、情報境界・並行性・透過性を検証する。"""

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.core import cpu_diagnostics as diagnostic

pytestmark = pytest.mark.no_db


def records(caplog):
    """通常ログと区別して診断JSONだけを読む。"""
    return [
        json.loads(r.message) for r in caplog.records if r.name == diagnostic.__name__
    ]


def test_private_fields_not_logged_and_response_unchanged(caplog):
    """未知のpath、header、body、SQL値は記録せず、例外queryも数える。"""
    engine = create_engine("sqlite://")
    diagnostic.install_db_probe(engine)
    diagnostic.install_db_probe(engine)

    async def endpoint(request):
        with engine.connect() as connection:
            connection.execute(text("select :private"), {"private": "SQL_SECRET"})
            with pytest.raises(Exception):
                connection.execute(text("select missing_column"))
        with diagnostic.phase("test"):
            return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/items/{private}", endpoint, methods=["POST"])])
    app.add_middleware(diagnostic.CpuDiagnosticsMiddleware)
    with caplog.at_level(logging.INFO, logger=diagnostic.__name__):
        response = TestClient(app).post(
            "/items/PATH_SECRET?secret=QUERY_SECRET",
            content="BODY_SECRET",
            headers={
                "Authorization": "Bearer TOKEN_SECRET",
                "Cookie": "COOKIE_SECRET",
                "X-Request-ID": "UNTRUSTED_SECRET",
            },
        )
    assert response.status_code == 200 and response.json() == {"ok": True}
    (row,) = records(caplog)
    assert row["endpoint"] == "/items/{private}"
    assert row["db_queries"] == 2 and row["db_execute_ms"] > 0
    assert row["response_bytes"] == len(response.content)
    assert row["serialization_ms"] is None
    assert not row["overlap"] and row["response_complete"]
    assert "SECRET" not in json.dumps(row)
    assert diagnostic.current.get() is None
    engine.dispose()


def test_concurrent_requests_mark_both_and_do_not_share_counts(caplog):
    """一方が先に終了しても、両方のCPU帰属不確実性を残す。"""

    async def exercise():
        gate = asyncio.Event()
        started = 0

        async def app(scope, receive, send):
            nonlocal started
            value = diagnostic.current.get()
            assert value is not None
            value.queries = scope["queries"]
            started += 1
            if started == 2:
                gate.set()
            await gate.wait()
            await send({"type": "http.response.start", "status": 200})
            await send({"type": "http.response.body", "body": b"ok"})

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            pass

        middleware = diagnostic.CpuDiagnosticsMiddleware(app)
        await asyncio.gather(
            *[
                middleware(
                    {
                        "type": "http",
                        "path": "/unknown/SECRET",
                        "method": "GET",
                        "queries": n,
                    },
                    receive,
                    send,
                )
                for n in (3, 7)
            ]
        )

    with caplog.at_level(logging.INFO, logger=diagnostic.__name__):
        asyncio.run(exercise())
    rows = records(caplog)
    assert len(rows) == 2 and all(r["overlap"] for r in rows)
    assert sorted(r["db_queries"] for r in rows) == [3, 7]
    assert all(r["endpoint"] == "unmatched" for r in rows)
    assert diagnostic.current.get() is None and not diagnostic._active


def test_disabled_app_does_not_install_probe(monkeypatch):
    """既定OFF時はASGIラッパーやengine listenerの追加処理を呼ばない。"""
    from app.core.config import settings
    from app.main import create_app

    monkeypatch.setattr(settings, "cpu_diagnostics_enabled", False)

    def forbidden(*args):
        pytest.fail("disabled diagnostics installed DB events")

    monkeypatch.setattr(diagnostic, "install_db_probe", forbidden)
    app = create_app()
    assert all(
        m.cls is not diagnostic.CpuDiagnosticsMiddleware for m in app.user_middleware
    )


def test_exception_preserves_failure_and_cleans_context(caplog):
    """アプリ例外を握り潰さず、不完全responseとして記録する。"""

    async def app(scope, receive, send):
        raise RuntimeError("PRIVATE_EXCEPTION")

    async def callback(*args):
        return {"type": "http.request", "body": b""}

    async def send(message):
        pass

    with caplog.at_level(logging.INFO, logger=diagnostic.__name__):
        with pytest.raises(RuntimeError, match="PRIVATE_EXCEPTION"):
            asyncio.run(
                diagnostic.CpuDiagnosticsMiddleware(app)(
                    {"type": "http", "path": "/private", "method": "GET"},
                    callback,
                    send,
                )
            )
    (row,) = records(caplog)
    assert row["status"] is None and not row["response_complete"]
    assert "PRIVATE_EXCEPTION" not in json.dumps(row)
    assert diagnostic.current.get() is None


def test_mcp_method_allowlist_and_aggregation():
    """未知tool/引数を記録せず、重複と重なったCPUを集計から除く。"""
    from scripts.summarize_cpu import summarize

    async def handler(ctx):
        return {"result": "unchanged"}

    value = diagnostic.Measurement()
    token = diagnostic.current.set(value)
    try:
        for name, expected in [
            ("list_projects", "list_projects"),
            (["PRIVATE"], None),
            ("PRIVATE", None),
        ]:
            result = asyncio.run(
                diagnostic.mcp_message(
                    SimpleNamespace(
                        method="tools/call",
                        params={"name": name, "arguments": {"private": "SECRET"}},
                    ),
                    handler,
                )
            )
            assert result == {"result": "unchanged"}
            assert value.tool == expected and value.method == "tools/call"
    finally:
        diagnostic.current.reset(token)
    row = {
        "event": "cpu_diagnostic",
        "diagnostic_id": "1",
        "endpoint": "/mcp",
        "mcp_method": "tools/call",
        "mcp_tool": "list_projects",
        "wall_ms": 10,
        "overlap": False,
        "process_cpu_ms": 3,
        "db_queries": 2,
        "db_execute_ms": 1,
        "response_bytes": 50,
        "serialization_ms": None,
    }
    (summary,) = summarize([row, row, {**row, "diagnostic_id": "2", "overlap": True}])
    assert summary["calls"] == 2 and summary["serial_cpu_samples"] == 1
    assert summary["process_cpu_total_ms_serial_only"] == 3
    assert summary["serialization_mean_ms"] == "未計測"
