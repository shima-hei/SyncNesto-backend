"""開発用MCPの文書・正本目標・既存審査契約の境界を検証する。"""

import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import NotFoundError
from app.core.mcp import CLIENT_ID, PLUGIN_CLIENT_ID
from app.models.document import DocumentRevision, ProjectDocument
from app.models.mcp import McpConnection
from app.models.user import User
from app.repositories.mcp import McpRepository
from app.schemas.mcp_tools import McpOperation
from app.services.mcp_operations import McpOperationsService
from tests.routers.test_mcp import api_headers, connect, operate, remote_call
from tests.routers.test_mcp import context as context
from tests.routers.test_mcp import remote_context as remote_context


@pytest.mark.no_db
@pytest.mark.parametrize(
    "enabled,client_id,objectives,count",
    [
        (False, CLIENT_ID, True, 28),
        (True, PLUGIN_CLIENT_ID, True, 28),
        (True, CLIENT_ID, False, 35),
        (True, CLIENT_ID, True, 36),
    ],
)
def test_catalog_preserves_review_contract(
    monkeypatch, enabled, client_id, objectives, count
):
    """既存28件の名前・説明・schema・注釈は元実装のdigestと一致する。"""
    monkeypatch.setattr(settings, "mcp_development_tools_enabled", enabled)
    monkeypatch.setattr(settings, "project_objectives_enabled", objectives)
    service = McpOperationsService()
    monkeypatch.setattr(service, "allowed", lambda *args: True)
    with Session() as db:
        catalog = service.catalog(
            db, McpConnection(project_ids=[1], client_id=client_id), User()
        )
    assert len(catalog) == count
    original = catalog[:28]
    digest = hashlib.sha256(
        json.dumps(
            original, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()
    assert digest == "77a603ee72c1dcd86bf6dc3038446c086570806041ddacb76f40e2ddd4467008"
    for tool in catalog[28:]:
        assert tool["annotations"]["idempotentHint"] is True
        assert tool["annotations"]["readOnlyHint"] == tool["name"].startswith(
            ("list_", "get_")
        )
    if count == 28:
        with Session() as db, pytest.raises(NotFoundError):
            service.execute(
                db,
                McpConnection(project_ids=[1], client_id=client_id),
                User(),
                McpOperation(tool="create_project_document", arguments={}),
            )


@pytest.fixture
def development(client, context, monkeypatch):
    """既存のOAuth・MFA・所属判定を通して開発用接続を作る。"""
    monkeypatch.setattr(settings, "mcp_development_tools_enabled", True)
    monkeypatch.setattr(settings, "project_objectives_enabled", True)
    monkeypatch.setattr(settings, "mcp_task_comments_enabled", True)
    direct, tokens, _ = connect(client, context[1].id)
    return direct, api_headers(direct, tokens["access_token"]), context[1]


def call(development, name, **arguments):
    """毎回の再送キーを既存helperで付与する。"""
    direct, headers, project = development
    return operate(direct, headers, name, project.id, **arguments)


def create(development):
    """業務文書の初版を実MCP操作APIで作る。"""
    response = call(
        development,
        "create_project_document",
        data={"title": "設計方針", "body": "初版本文"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_document_roundtrip_revision_and_replay(development, db):
    """作成/更新/読取・検索・再送・競合が履歴を壊さない。"""
    created = create(development)
    assert "body" not in created
    key = uuid4().hex
    arguments = {
        "document_id": created["id"],
        "key": key,
        "data": {"version": 1, "title": "改訂設計", "body": "更新本文"},
    }
    updated = call(development, "update_project_document", **arguments)
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    assert (
        call(development, "update_project_document", **arguments).json()
        == updated.json()
    )
    assert (
        call(
            development, "update_project_document", **{**arguments, "key": uuid4().hex}
        ).status_code
        == 409
    )
    listed = call(development, "list_project_documents", key=False, q="更新本文").json()
    assert listed["total"] == 1 and "body" not in listed["items"][0]
    current = call(
        development, "get_project_document", key=False, document_id=created["id"]
    ).json()
    assert current["body"] == "更新本文"
    assert db.scalar(select(func.count()).select_from(DocumentRevision)) == 2
    assert (
        db.scalar(select(DocumentRevision.body).where(DocumentRevision.number == 1))
        == "初版本文"
    )


def test_gate_and_current_permission_are_checked_on_every_operation(
    development, db, monkeypatch
):
    """flag OFF・審査client・read剥奪は一覧を経由せず指定しても拒否する。"""
    created = create(development)
    monkeypatch.setattr(settings, "mcp_development_tools_enabled", False)
    assert (
        call(
            development, "get_project_document", key=False, document_id=created["id"]
        ).status_code
        == 404
    )
    monkeypatch.setattr(settings, "mcp_development_tools_enabled", True)
    connection = db.scalar(select(McpConnection))
    connection.client_id = PLUGIN_CLIENT_ID
    db.commit()
    # token自体のclient bindingが無効なら401、認証済みでも追加操作は404。
    assert call(
        development, "get_project_document", key=False, document_id=created["id"]
    ).status_code in {401, 404}


def test_read_permission_and_project_isolation(
    development, monkeypatch, create_test_project
):
    """Project越境と現在のread欠落では本文を返さない。"""
    created = create(development)
    other = create_test_project()
    direct, headers, _ = development
    assert (
        operate(
            direct,
            headers,
            "get_project_document",
            other.id,
            key=False,
            document_id=created["id"],
        ).status_code
        == 403
    )
    from app.services.authorization import AuthorizationService

    original = AuthorizationService.has_project_permission

    def permissions(self, *args, **kwargs):
        if kwargs.get("permission_code") == "document:read":
            return False
        return original(self, *args, **kwargs)

    monkeypatch.setattr(AuthorizationService, "has_project_permission", permissions)
    assert (
        call(
            development, "get_project_document", key=False, document_id=created["id"]
        ).status_code
        == 403
    )
    catalog = direct.get("/integrations/mcp/catalog", headers=headers).json()["tools"]
    assert "get_project_document" not in {row["name"] for row in catalog}


def test_document_links_validate_target_and_roundtrip(
    development,
    create_test_requirement,
    create_test_requirement_document,
    create_test_project,
    monkeypatch,
):
    """同一Projectの要件へ実参照を作り、越境した対象は拒否する。"""
    project = development[2]
    rd = create_test_requirement_document(project=project)
    requirement = create_test_requirement(document=rd)
    created = create(development)
    result = call(
        development,
        "link_project_document",
        document_id=created["id"],
        data={"target_type": "requirement", "target_id": requirement.id},
    )
    assert result.status_code == 200, result.text
    assert result.json()["items"][0]["target_id"] == requirement.id
    current = call(
        development, "get_project_document", key=False, document_id=created["id"]
    ).json()
    assert current["links"] == result.json()["items"]
    other = create_test_project()
    other_rd = create_test_requirement_document(project=other)
    foreign = create_test_requirement(document=other_rd)
    assert (
        call(
            development,
            "link_project_document",
            document_id=created["id"],
            data={"target_type": "requirement", "target_id": foreign.id},
        ).status_code
        == 404
    )
    from app.services.authorization import AuthorizationService

    original = AuthorizationService.has_project_permission

    def permissions(self, *args, **kwargs):
        if kwargs.get("permission_code") == "requirement:read":
            return False
        return original(self, *args, **kwargs)

    monkeypatch.setattr(AuthorizationService, "has_project_permission", permissions)
    current = call(
        development, "get_project_document", key=False, document_id=created["id"]
    ).json()
    assert current["links"] == []


def test_objectives_preserve_purpose_omission_and_version(
    development, create_test_requirement_document, db, monkeypatch
):
    """専用3項目だけを更新し、目的・省略値・競合・機能停止を維持する。"""
    rd = create_test_requirement_document(project=development[2])
    rd.purpose, rd.project_scope = "文書の目的", "既存スコープ"
    db.commit()
    arguments = {
        "document_id": rd.id,
        "version": 1,
        "data": {"project_goal": "初心者が目的を理解する"},
    }
    updated = call(development, "set_requirement_document_objectives", **arguments)
    assert updated.status_code == 200, updated.text
    assert (
        call(
            development, "set_requirement_document_objectives", **arguments
        ).status_code
        == 409
    )
    current = call(
        development,
        "get_requirement_target",
        key=False,
        target_type="document",
        target_id=rd.id,
    ).json()
    assert (
        current["purpose"] == "文書の目的"
        and current["project_scope"] == "既存スコープ"
    )
    assert current["project_goal"] == arguments["data"]["project_goal"]
    assert (
        call(
            development,
            "set_requirement_document_objectives",
            document_id=rd.id,
            version=updated.json()["version"],
            data={"project_goal": None},
        ).status_code
        == 200
    )
    for invalid in [{}, {"purpose": "無断転記"}]:
        assert (
            call(
                development,
                "set_requirement_document_objectives",
                document_id=rd.id,
                version=3,
                data=invalid,
            ).status_code
            == 400
        )
    monkeypatch.setattr(settings, "project_objectives_enabled", False)
    assert (
        call(
            development, "set_requirement_document_objectives", **arguments
        ).status_code
        == 404
    )


def test_failed_receipt_rolls_back_document_and_initial_revision(
    development, db, monkeypatch
):
    """既存Serviceの内部commitがあってもreceipt失敗では文書を残さない。"""

    def fail(*args, **kwargs):
        raise RuntimeError("receipt unavailable")

    monkeypatch.setattr(McpRepository, "save_receipt", fail)
    with pytest.raises(RuntimeError, match="receipt unavailable"):
        create(development)
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(ProjectDocument)) == 0
    assert db.scalar(select(func.count()).select_from(DocumentRevision)) == 0


def test_development_documents_through_embedded_sdk(
    remote_context, context, monkeypatch
):
    """SDKのtools/listとtools/callで一般文書を実際に作成・読み返す。"""
    monkeypatch.setattr(settings, "mcp_development_tools_enabled", True)
    remote, _ = remote_context
    listed = remote.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    ).json()["result"]["tools"]
    assert "create_project_document" in {row["name"] for row in listed}
    created = remote_call(
        remote,
        "create_project_document",
        {
            "project_id": context[1].id,
            "idempotency_key": uuid4().hex,
            "data": {"title": "開発目的", "body": "なぜこのシステムを作るのか"},
        },
    )
    assert not created.get("isError")
    row = created["structuredContent"]
    read = remote_call(
        remote,
        "get_project_document",
        {
            "project_id": context[1].id,
            "document_id": row["id"],
        },
    )
    assert read["structuredContent"]["body"] == "なぜこのシステムを作るのか"
