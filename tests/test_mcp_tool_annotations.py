"""MCPの副作用をホストへ正しく伝える契約を検証する。"""

import pytest
from sqlalchemy.orm import Session

from app.models.mcp import McpConnection
from app.models.user import User
from app.services.mcp_operations import McpOperationsService

pytestmark = pytest.mark.no_db


def test_schedule_preview_declares_saved_plan_without_destructive_changes(monkeypatch):
    """保存する日程案は書込み、業務日程への適用は破壊的変更として伝える。"""
    service = McpOperationsService()
    monkeypatch.setattr(service, "allowed", lambda *args: True)
    with Session() as db:
        catalog = {
            tool["name"]: tool
            for tool in service.catalog(db, McpConnection(project_ids=[1]), User())
        }
    assert catalog["preview_task_schedule"]["annotations"] == {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    assert catalog["apply_task_schedule"]["annotations"]["readOnlyHint"] is False
    assert catalog["apply_task_schedule"]["annotations"]["destructiveHint"] is True
    assert catalog["get_task"]["annotations"]["readOnlyHint"] is True
    assert catalog["get_task"]["annotations"]["destructiveHint"] is False
