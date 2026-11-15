"""明示的に有効化した環境だけで使う、本文を保持しないCPU診断。"""

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock
from time import perf_counter_ns, process_time_ns
from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.engine import Engine
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)


@dataclass
class Measurement:
    """一つのHTTP操作の数値のみを保持し、workerへ参照を引き継ぐ。"""

    method: str | None = None
    tool: str | None = None
    queries: int = 0
    db_ns: int = 0
    phases: dict[str, dict[str, float]] = field(default_factory=dict)


current: ContextVar[Measurement | None] = ContextVar("cpu_measurement", default=None)
_lock = Lock()
_active: dict[int, Measurement] = {}
_overlapped: set[int] = set()
_first_request = True
_instance_id = uuid4().hex
_loaded_at = perf_counter_ns()


@contextmanager
def phase(name: str):
    """診断中だけ区間の経過時間とプロセスCPU差分を記録する。"""
    value = current.get()
    if value is None:
        yield
        return
    wall, cpu = perf_counter_ns(), process_time_ns()
    try:
        yield
    finally:
        row = value.phases.setdefault(name, {"wall_ms": 0.0, "process_cpu_ms": 0.0})
        row["wall_ms"] += (perf_counter_ns() - wall) / 1e6
        row["process_cpu_ms"] += (process_time_ns() - cpu) / 1e6


async def mcp_message(ctx, call_next):
    """SDKが解釈したmethodと既知tool名だけを採用する。引数は保持しない。"""
    value = current.get()
    if value is not None:
        from app.services.mcp_catalog import TOOLS
        from app.services.mcp_development_catalog import DEVELOPMENT_TOOLS

        value.method = (
            ctx.method
            if ctx.method
            in {
                "initialize",
                "notifications/initialized",
                "tools/list",
                "tools/call",
                "ping",
            }
            else "other"
        )
        name = (ctx.params or {}).get("name")
        value.tool = (
            name
            if isinstance(name, str) and (name in TOOLS or name in DEVELOPMENT_TOOLS)
            else None
        )
    with phase("mcp_handler"):
        return await call_next(ctx)


def install_db_probe(engine: Engine) -> None:
    """engine一つへ一度だけeventを登録し、SQLやparametersを読まずに計測する。"""
    if event.contains(engine, "before_cursor_execute", _before_query):
        return
    event.listen(engine, "before_cursor_execute", _before_query)
    event.listen(engine, "after_cursor_execute", _after_query)
    event.listen(engine, "handle_error", _query_error)


def _before_query(conn, cursor, statement, parameters, context, executemany):
    """context固有に開始時刻を保存し、別requestと混ぜない。"""
    value = current.get()
    if value is not None:
        context._cpu_diagnostic = (value, perf_counter_ns())
        value.queries += 1


def _finish_query(context):
    """成功・失敗の両方を数え、fetch/ORM変換は含めない。"""
    pending = getattr(context, "_cpu_diagnostic", None)
    if pending is not None:
        value, started = pending
        value.db_ns += perf_counter_ns() - started
        del context._cpu_diagnostic


def _after_query(conn, cursor, statement, parameters, context, executemany):
    """cursor.executeの経過時間を回収する。"""
    _finish_query(context)


def _query_error(context):
    """例外本文を参照せず、失敗したcursor.executeも回収する。"""
    _finish_query(context.execution_context)


class CpuDiagnosticsMiddleware:
    """リクエスト本文を読まず、ASGIが送るバイト数を数える。"""

    def __init__(self, app: ASGIApp) -> None:
        """request固有データはインスタンスへ保持しない。"""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """CPUはプロセス全体の差分。並行した値は必ずoverlap=trueとする。"""
        global _first_request
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        value = Measurement()
        token = current.set(value)
        with _lock:
            first = _first_request
            _first_request = False
            if _active:
                _overlapped.update([*_active, id(value)])
            _active[id(value)] = value
        wall, cpu = perf_counter_ns(), process_time_ns()
        size, status, completed = 0, None, False

        async def observe(message):
            nonlocal size, status, completed
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                size += len(message.get("body", b""))
                completed = not message.get("more_body", False)
            await send(message)

        try:
            await self.app(scope, receive, observe)
        finally:
            wall_ms = (perf_counter_ns() - wall) / 1e6
            cpu_ms = (process_time_ns() - cpu) / 1e6
            current.reset(token)
            with _lock:
                overlap = id(value) in _overlapped
                _overlapped.discard(id(value))
                del _active[id(value)]
            route = scope.get("route")
            endpoint = getattr(route, "path", None)
            # mounted Starlette appや未解決ルートに生のURLを流さない。
            if scope["path"] == "/mcp":
                endpoint = "/mcp"
            payload = {
                "event": "cpu_diagnostic",
                "diagnostic_id": uuid4().hex,
                "process_instance": _instance_id,
                "diagnostic_module_age_ms": (perf_counter_ns() - _loaded_at) / 1e6,
                "endpoint": endpoint or "unmatched",
                "http_method": scope["method"],
                "mcp_method": value.method,
                "mcp_tool": value.tool,
                "wall_ms": wall_ms,
                "process_cpu_ms": cpu_ms,
                "cpu_scope": "process_delta_not_billing",
                "overlap": overlap,
                "db_execute_ms": value.db_ns / 1e6,
                "db_queries": value.queries,
                "db_probe_scope": "normal_engine_only",
                "serialization_ms": value.phases.get("mcp_text_serialization", {}).get(
                    "wall_ms"
                ),
                "serialization_scope": "mcp_text_content_only",
                "response_bytes": size,
                "status": status,
                "response_complete": completed,
                "first_observed_request_in_process": first,
                "phases": value.phases,
            }
            # 診断IDは内部生成。外部Request ID/UA/IP/Cookie/SQL/本文は記録しない。
            logger.info(json.dumps(payload, separators=(",", ":")))
