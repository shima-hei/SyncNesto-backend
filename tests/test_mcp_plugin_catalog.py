"""公開接続の拡張を明示flagと既存の業務権限の交差に限定する。"""

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.mcp import CLIENT_ID, PLUGIN_CLIENT_ID
from app.models.mcp import McpConnection
from app.models.user import User
from app.schemas.mcp_tools import McpOperation
from app.services.mcp_catalog import TOOLS
from app.services.mcp_operations import McpOperationsService

pytestmark = pytest.mark.no_db

PUBLIC_ADDITIONS = {
    "list_project_documents",
    "get_project_document",
    "get_project_document_content",
    "create_project_document",
    "update_project_document",
    "patch_project_document",
    "link_project_document",
    "set_requirement_document_objectives",
    "search_project_items",
    "get_requirement_context",
    "get_project_brief",
    "list_task_comments",
    "create_task_comment",
}


@pytest.fixture
def enabled_domains(monkeypatch):
    """公開用のmaster flagとは独立した既存の機能制約を有効にする。"""
    for name in (
        "mcp_read_context_enabled",
        "mcp_task_comments_enabled",
        "project_objectives_enabled",
    ):
        monkeypatch.setattr(settings, name, True)


@pytest.mark.parametrize(
    "client_id,development,public,extended",
    [
        (PLUGIN_CLIENT_ID, True, False, False),
        (PLUGIN_CLIENT_ID, False, True, True),
        (CLIENT_ID, False, True, False),
        (CLIENT_ID, True, False, True),
        ("unknown-client", True, True, False),
    ],
)
def test_public_flag_is_independent_and_unknown_clients_stay_closed(
    monkeypatch, enabled_domains, client_id, development, public, extended
):
    """開発用ONだけで公開契約を変えず、最大41操作だけを提示する。"""
    monkeypatch.setattr(settings, "mcp_development_tools_enabled", development)
    monkeypatch.setattr(settings, "mcp_plugin_extended_tools_enabled", public)
    service = McpOperationsService()
    monkeypatch.setattr(service, "allowed", lambda *args: True)
    with Session() as db:
        catalog = service.catalog(
            db, McpConnection(project_ids=[1], client_id=client_id), User()
        )
    names = {row["name"] for row in catalog}
    assert names == set(TOOLS) | (PUBLIC_ADDITIONS if extended else set())
    if extended:
        monkeypatch.setattr(settings, "mcp_plugin_extended_tools_enabled", False)
        monkeypatch.setattr(settings, "mcp_development_tools_enabled", False)
        with Session() as db:
            baseline = service.catalog(
                db, McpConnection(project_ids=[1], client_id=client_id), User()
            )
        assert catalog[:28] == baseline


@pytest.mark.parametrize(
    "flag,removed",
    [
        (
            "mcp_read_context_enabled",
            {"search_project_items", "get_requirement_context", "get_project_brief"},
        ),
        ("mcp_task_comments_enabled", {"list_task_comments", "create_task_comment"}),
        ("project_objectives_enabled", {"set_requirement_document_objectives"}),
    ],
)
def test_public_flag_preserves_domain_flags(
    monkeypatch, enabled_domains, flag, removed
):
    """公開masterがONでも、停止した個別機能を呼べるようにしない。"""
    monkeypatch.setattr(settings, "mcp_plugin_extended_tools_enabled", True)
    monkeypatch.setattr(settings, flag, False)
    service = McpOperationsService()
    monkeypatch.setattr(service, "allowed", lambda *args: True)
    connection = McpConnection(project_ids=[1], client_id=PLUGIN_CLIENT_ID)
    with Session() as db:
        names = {row["name"] for row in service.catalog(db, connection, User())}
        for name in removed:
            with pytest.raises(NotFoundError):
                service.execute(
                    db, connection, User(), McpOperation(tool=name, arguments={})
                )
    assert names == set(TOOLS) | (PUBLIC_ADDITIONS - removed)


@pytest.mark.parametrize(
    "enabled,error", [(False, NotFoundError), (True, ForbiddenError)]
)
def test_public_calls_require_both_publication_and_current_permission(
    monkeypatch, enabled_domains, enabled, error
):
    """catalogの非表示を迂回した直接callも既存の認可前に拒否する。"""
    monkeypatch.setattr(settings, "mcp_plugin_extended_tools_enabled", enabled)
    service = McpOperationsService()
    monkeypatch.setattr(service, "allowed", lambda *args: False)
    connection = McpConnection(project_ids=[1], client_id=PLUGIN_CLIENT_ID)
    operation = McpOperation(
        tool="get_project_document", arguments={"project_id": 1, "document_id": 1}
    )
    with Session() as db:
        assert PUBLIC_ADDITIONS.isdisjoint(
            {row["name"] for row in service.catalog(db, connection, User())}
        )
        with pytest.raises(error):
            service.execute(db, connection, User(), operation)
