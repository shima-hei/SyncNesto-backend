"""追加読取の境界・正式関連・上限・旧契約と取得量を隔離DBで検証する。"""

import asyncio
import json
from datetime import date
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator
from mcp.types import CallToolResult, TextContent, Tool
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ForbiddenError
from app.core.mcp import CLIENT_ID, PLUGIN_CLIENT_ID
from app.models.document import DocumentLink, ProjectDocument
from app.models.mcp import McpConnection, McpOperationReceipt
from app.models.project import Project
from app.models.rbac import Permission, RolePermission
from app.models.requirement import Requirement, RequirementDocument, RequirementSection
from app.models.specification import Specification, SpecificationLink
from app.models.task import Milestone, RequirementTaskRelation, Task
from app.models.tenant import Tenant
from app.models.test_design import RequirementTestItem
from app.models.test_design import TestDesign as Design
from app.models.test_design import TestItem as Item
from app.models.user import User
from app.schemas.mcp_read import (
    ProjectBriefInput,
    ProjectSearchInput,
    RequirementContextInput,
)
from app.services.mcp_operations import McpOperationsService
from app.services.mcp_read import McpReadService
from syncnesto_mcp.contracts import READ_CONTEXT_TOOLS
from syncnesto_mcp.server import SyncnestoMCP, operation_error
from tests.routers.test_mcp import context as context
from tests.routers.test_mcp_development import development as development


def invoke(development, name, **arguments):
    """読取に書込用キーを混ぜず実APIを通す。"""
    direct, headers, project = development
    return direct.post(
        "/integrations/mcp/operations",
        headers=headers,
        json={
            "tool": name,
            "arguments": {"project_id": project.id, **arguments},
        },
    )


def result(development, name, **arguments):
    """失敗を成功データとして扱わない。"""
    response = invoke(development, name, **arguments)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def read_setup(development, context, db, monkeypatch):
    """実業務DBを参照せず、長文と正式/未関連のレコードを用意する。"""
    monkeypatch.setattr(settings, "mcp_read_context_enabled", True)
    monkeypatch.setattr(settings, "project_traceability_enabled", True)
    user, project, _ = context
    doc = RequirementDocument(
        project_id=project.id,
        title="目的の正本",
        document_code="RD-READ",
        project_goal="全員が同じ目標から作業する",
        project_scope="内部業務",
        project_completion_criteria="追跡できる",
    )
    db.add(doc)
    db.flush()
    section = RequirementSection(
        document_id=doc.id, title="機能", section_type="functional"
    )
    db.add(section)
    db.flush()
    req = Requirement(
        document_id=doc.id,
        section_id=section.id,
        requirement_code="REQ-READ",
        requirement_type="functional",
        title="追跡要件",
        description="本文🙂" * 4000,
        acceptance_criteria="条件" * 6000,
        rationale="名称の中のTASK-NOT-LINKEDは正式関連ではない",
    )
    task = Task(
        project_id=project.id,
        task_code="TASK-RELATED",
        title="正式な作業",
        status="in_progress",
        due_date=date(2026, 10, 23),
    )
    unlinked = Task(
        project_id=project.id,
        task_code="TASK-NOT-LINKED",
        title="未関連作業",
        status="done",
        due_date=date(2026, 10, 23),
    )
    spec = Specification(
        project_id=project.id,
        kind="screen",
        title="画面の設計",
        content={},
        created_by=user.id,
        updated_by=user.id,
    )
    design = Design(
        project_id=project.id, name="確認方法", created_by=user.id, updated_by=user.id
    )
    narrative = ProjectDocument(
        project_id=project.id,
        title="関連する設計文書",
        body="前文" * 4000 + "検索の目印" + "後文" * 4000,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add_all([req, task, unlinked, spec, design, narrative])
    db.flush()
    item = Item(
        design_id=design.id, code="ITEM-READ", content="期待する動作", position=1
    )
    db.add(item)
    db.flush()
    db.add_all(
        [
            RequirementTaskRelation(
                requirement_id=req.id, task_id=task.id, relation_type="implementation"
            ),
            SpecificationLink(specification_id=spec.id, requirement_id=req.id),
            RequirementTestItem(
                requirement_id=req.id, item_id=item.id, created_by=user.id
            ),
            DocumentLink(
                document_id=narrative.id, requirement_id=req.id, created_by=user.id
            ),
            Milestone(
                project_id=project.id, title="確認期限", target_date=date(2026, 10, 31)
            ),
        ]
    )
    project.goal_document_id = doc.id
    db.commit()
    return dict(
        development=development,
        user=user,
        project=project,
        doc=doc,
        req=req,
        task=task,
        spec=spec,
        design=design,
        item=item,
        narrative=narrative,
    )


@pytest.mark.no_db
def test_read_catalog_is_opt_in_with_schemas_and_original_contract(monkeypatch):
    """新flagとclient境界を守り、旧28件の定義を変えない。"""
    monkeypatch.setattr(settings, "mcp_development_tools_enabled", True)
    monkeypatch.setattr(settings, "project_objectives_enabled", True)
    service = McpOperationsService()
    monkeypatch.setattr(service, "allowed", lambda *args: True)
    with Session() as db:
        monkeypatch.setattr(settings, "mcp_read_context_enabled", False)
        old = service.catalog(
            db, McpConnection(project_ids=[1], client_id=CLIENT_ID), User()
        )
        monkeypatch.setattr(settings, "mcp_read_context_enabled", True)
        new = service.catalog(
            db, McpConnection(project_ids=[1], client_id=CLIENT_ID), User()
        )
        review = service.catalog(
            db, McpConnection(project_ids=[1], client_id=PLUGIN_CLIENT_ID), User()
        )
    assert len(old) == 36 and len(new) == 39 and len(review) == 28
    assert old[:28] == new[:28] == review
    for tool in new:
        Tool.model_validate(tool)
        if tool["name"] in READ_CONTEXT_TOOLS:
            assert tool["annotations"]["readOnlyHint"] is True
            assert tool["annotations"]["destructiveHint"] is False
            Draft202012Validator.check_schema(tool["outputSchema"])


@pytest.mark.no_db
@pytest.mark.parametrize(
    "schema,arguments",
    [
        (ProjectSearchInput, {"query": "\x00"}),
        (ProjectSearchInput, {"query": "x", "page_size": 51}),
        (RequirementContextInput, {"requirement_id": 1, "relation_page_size": 21}),
        (ProjectBriefInput, {"limit": 21}),
    ],
)
def test_input_bounds(schema, arguments):
    """境界の代表例をモデルで拒否し、無制限のSQLへ進めない。"""
    with pytest.raises(ValidationError):
        schema.model_validate({"project_id": 1, **arguments})


@pytest.mark.no_db
def test_read_error_codes_and_legacy_error_text(monkeypatch):
    """追加読取だけへ安全なエラーを返し、既存の表示を保持する。"""
    server = SyncnestoMCP("http://127.0.0.1:8000", "http://127.0.0.1:8765/mcp")

    async def failure(*args, **kwargs):
        raise operation_error(404)

    monkeypatch.setattr(server, "request", failure)
    new = asyncio.run(server.call_tool("get_project_brief", {"project_id": 1}))
    assert (
        new.is_error
        and new.structured_content["error"]["code"] == "NOT_FOUND_OR_UNAVAILABLE"
    )
    assert isinstance(new.content[0], TextContent)
    assert json.loads(new.content[0].text) == new.structured_content
    old = asyncio.run(server.call_tool("get_task", {"project_id": 1, "task_id": 1}))
    assert old.is_error and old.structured_content is None
    assert isinstance(old.content[0], TextContent)
    assert old.content[0].text == "Syncnestoの操作に失敗しました (404)"
    oversized = asyncio.run(
        server.call_tool("search_project_items", {"query": "x" * 270000})
    )
    assert oversized.structured_content["error"]["code"] == "INVALID_ARGUMENT"


@pytest.mark.no_db
@pytest.mark.parametrize("user_type,realm", [("guest", "normal"), ("internal", "demo")])
def test_read_keeps_internal_normal_boundary(user_type, realm):
    """新しいMCP取得経路からguest/demoへ対象を広げない。"""
    with Session() as db:
        db.info["data_realm"] = realm
        with pytest.raises(ForbiddenError):
            McpReadService().read(
                db,
                User(user_type=user_type),
                "get_project_brief",
                ProjectBriefInput(project_id=1),
            )


def test_context_brief_and_response_schemas(read_setup, db):
    """保存された目的と正式関連だけを返し、読取で業務版やreceiptを更新しない。"""
    c = read_setup
    dev = c["development"]
    before = db.scalar(select(func.count()).select_from(McpOperationReceipt))
    context_data = result(dev, "get_requirement_context", requirement_id=c["req"].id)
    assert context_data["entity"]["id"] == str(c["req"].id)
    assert len(context_data["content"]["description"]) == 2000
    assert "description" in context_data["content"]["truncated_fields"]
    assert context_data["relations"]["tasks"]["pagination"]["total"] == 1
    assert context_data["relations"]["tasks"]["items"][0]["id"] == str(c["task"].id)
    assert context_data["relations"]["tests"]["items"][0]["version"] is None
    assert context_data["relations"]["tests"]["items"][0]["updated_at"] is None
    assert context_data["section"]["id"] == str(c["req"].section_id)
    assert (
        context_data["relations"]["tests"]["items"][0]["container_version"]
        == c["design"].version
    )
    assert context_data["relations"]["designs"]["items"][0]["id"] == str(c["spec"].id)
    assert context_data["relations"]["documents"]["items"][0]["id"] == str(
        c["narrative"].id
    )
    assert context_data["review"]["state"] == "not_requested"
    summary = result(
        dev,
        "get_requirement_context",
        requirement_id=c["req"].id,
        include=[],
        detail_level="summary",
    )
    assert (
        summary["content"]["state"] == "not_requested"
        and summary["content"]["description"] is None
    )
    assert all(
        f["state"] == "not_requested" and f["pagination"] is None
        for f in summary["relations"].values()
    )
    full = result(
        dev,
        "get_requirement_context",
        requirement_id=c["req"].id,
        include=[],
        detail_level="full",
    )
    assert len(full["content"]["description"]) == 8000
    assert len(full["content"]["acceptance_criteria"]) == 8000
    filtered = result(
        dev,
        "search_project_items",
        query="TASK",
        entity_types=["task"],
        status="done",
    )
    assert filtered["pagination"]["total"] == 1
    assert filtered["items"][0]["code"] == "TASK-NOT-LINKED"
    assert filtered["items"][0]["version"] == 1
    brief = result(dev, "get_project_brief", as_of_date="2026-10-25")
    assert brief["objectives"]["goal"] == "全員が同じ目標から作業する"
    assert brief["tasks"]["total"] == 2 and brief["tasks"]["overdue"] == 1
    assert brief["milestones"]["items"][0]["target_date"] == "2026-10-31"
    assert brief["current_phase"] == "unsupported"
    search = result(
        dev,
        "search_project_items",
        query="全員が同じ目標",
        entity_types=["requirement_document"],
    )
    assert search["items"][0]["id"] == str(c["doc"].id)
    response = dev[0].get("/integrations/mcp/catalog", headers=dev[1])
    assert response.status_code == 200, response.text
    catalog = {t["name"]: t for t in response.json()["tools"]}
    for name, data in [
        ("get_requirement_context", context_data),
        ("get_project_brief", brief),
        ("search_project_items", search),
    ]:
        Draft202012Validator(catalog[name]["outputSchema"]).validate(data)
    db.expire_all()
    assert c["req"].version == c["doc"].version == c["task"].version == 1
    assert db.scalar(select(func.count()).select_from(McpOperationReceipt)) == before


def test_page_empty_flags_and_missing_target(read_setup, db, monkeypatch):
    """0件・未取得・機能OFF・不正対象を取り違えない。"""
    c = read_setup
    dev = c["development"]
    for i in range(2):
        doc = ProjectDocument(
            project_id=c["project"].id,
            title=f"ページング {i}",
            body="検索の目印",
            created_by=c["user"].id,
            updated_by=c["user"].id,
        )
        db.add(doc)
    db.commit()
    first = result(
        dev,
        "search_project_items",
        query="検索の目印",
        entity_types=["document"],
        page_size=1,
    )
    second = result(
        dev,
        "search_project_items",
        query="検索の目印",
        entity_types=["document"],
        page_size=1,
        page=2,
    )
    assert first["pagination"]["total"] == 3 and first["pagination"]["next_page"] == 2
    assert first["items"][0]["id"] != second["items"][0]["id"]
    empty = result(dev, "search_project_items", query=uuid4().hex)
    assert empty["pagination"]["total"] == 0 and empty["items"] == []
    extra = Task(
        project_id=c["project"].id, task_code="PAGE-TASK", title="別の正式作業"
    )
    db.add(
        RequirementTaskRelation(
            requirement_id=c["req"].id,
            task_id=c["task"].id,
            relation_type="reviews",
        )
    )
    db.add(extra)
    db.flush()
    db.add(
        RequirementTaskRelation(
            requirement_id=c["req"].id,
            task_id=extra.id,
            relation_type="implementation",
        )
    )
    db.commit()
    pages = [
        result(
            dev,
            "get_requirement_context",
            requirement_id=c["req"].id,
            include=["tasks"],
            relation_page=page,
            relation_page_size=1,
        )["relations"]["tasks"]
        for page in (1, 2)
    ]
    assert pages[0]["pagination"]["total"] == 2
    assert pages[0]["pagination"]["has_more"] is True
    assert pages[0]["pagination"]["next_page"] == 2
    assert pages[1]["pagination"]["has_more"] is False
    assert pages[0]["items"][0]["id"] != pages[1]["items"][0]["id"]
    assert (
        invoke(dev, "get_requirement_context", requirement_id=99999999).status_code
        == 404
    )
    assert (
        invoke(dev, "search_project_items", query="x", page_size=51).status_code == 400
    )
    monkeypatch.setattr(settings, "project_traceability_enabled", False)
    disabled = result(dev, "get_requirement_context", requirement_id=c["req"].id)
    assert all(
        f["state"] == "disabled" and f["pagination"] is None
        for f in disabled["relations"].values()
    )
    monkeypatch.setattr(settings, "mcp_read_context_enabled", False)
    assert invoke(dev, "get_project_brief").status_code == 404


def test_current_permissions_and_project_boundaries(
    read_setup, context, db, create_test_project
):
    """接続後の権限変更・未同意Projectで名前や件数を漏らさない。"""
    c = read_setup
    dev = c["development"]
    member = context[2]
    denied = ["document:read", "task:read", "test_plan:read", "test_case:read"]
    ids = select(Permission.id).where(Permission.code.in_(denied))
    db.execute(
        delete(RolePermission).where(
            RolePermission.role_id == member.role_id,
            RolePermission.permission_id.in_(ids),
        )
    )
    db.commit()
    restricted = result(dev, "get_requirement_context", requirement_id=c["req"].id)
    assert all(
        f["state"] == "unavailable" and f["items"] == [] and f["pagination"] is None
        for f in restricted["relations"].values()
    )
    brief = result(dev, "get_project_brief")
    assert brief["tasks"]["state"] == "unavailable" and brief["tasks"]["total"] is None
    search = result(dev, "search_project_items", query="検索の目印")
    assert search["items"] == []
    tenant = Tenant(name="Other MCP tenant", slug=f"other-mcp-{uuid4().hex}")
    db.add(tenant)
    db.flush()
    other = Project(tenant_id=tenant.id, project_code="PRIVATE", name="Private MCP")
    db.add(other)
    db.flush()
    foreign_doc = RequirementDocument(
        project_id=other.id, document_code="PRIVATE", title="Private"
    )
    db.add(foreign_doc)
    db.flush()
    foreign_req = Requirement(
        document_id=foreign_doc.id,
        requirement_code="PRIVATE",
        title="Private",
        requirement_type="functional",
    )
    db.add(foreign_req)
    db.commit()
    assert (
        invoke(
            dev, "get_requirement_context", requirement_id=foreign_req.id
        ).status_code
        == 404
    )
    for name, extra in [
        ("get_project_brief", {}),
        ("get_requirement_context", {"requirement_id": c["req"].id}),
        ("search_project_items", {"query": "x"}),
    ]:
        assert invoke(dev, name, project_id=other.id, **extra).status_code == 403
    rid = db.scalar(select(Permission.id).where(Permission.code == "requirement:read"))
    db.execute(
        delete(RolePermission).where(
            RolePermission.role_id == member.role_id,
            RolePermission.permission_id == rid,
        )
    )
    db.commit()
    assert (
        invoke(dev, "get_requirement_context", requirement_id=c["req"].id).status_code
        == 403
    )
    revoked = result(dev, "get_project_brief")["objectives"]
    assert revoked["state"] == "unavailable"
    assert revoked["document"] is None and revoked["source_version"] is None


def test_read_efficiency_comparison(read_setup):
    """同じ文書の特定と短い文脈取得を比較。実トークンや実通信量とは呼ばない。"""
    dev = read_setup["development"]
    started = perf_counter()
    old_list = result(dev, "list_project_documents", q="検索の目印")
    old_doc = result(
        dev, "get_project_document", document_id=old_list["items"][0]["id"]
    )
    old_seconds = perf_counter() - started
    started = perf_counter()
    new = result(
        dev, "search_project_items", query="検索の目印", entity_types=["document"]
    )
    new_seconds = perf_counter() - started
    assert new["items"][0]["id"] == str(old_doc["id"])
    assert (
        "検索の目印" in old_doc["body"] and "検索の目印" in new["items"][0]["excerpt"]
    )

    def size(data):
        wire = CallToolResult(
            content=[
                TextContent(type="text", text=json.dumps(data, ensure_ascii=False))
            ],
            structured_content=data,
        )
        return len(wire.model_dump_json(by_alias=True, exclude_none=True).encode())

    report = {
        "scenario": "find_matching_document_and_read_short_context",
        "environment": "isolated_db_inprocess_api",
        "representation": "SDK CallToolResult JSON (aliases, excluding nulls)",
        "old": {
            "calls": 2,
            "result_json_bytes": size(old_list) + size(old_doc),
            "seconds": old_seconds,
        },
        "new": {"calls": 1, "result_json_bytes": size(new), "seconds": new_seconds},
        "same_document_and_match": True,
        "actual_model_tokens": None,
        "token_estimate": None,
        "not_measured": ["OAuth_and_network_bytes", "real_client_model_calls"],
        "caveat": "既存q検索は既に1回。抜粋を取得する追加getだけを省略した比較。",
    }
    assert report["new"]["result_json_bytes"] < report["old"]["result_json_bytes"]
    out = Path(".verify/mcp-read")
    out.mkdir(parents=True, exist_ok=True)
    (out / "benchmark.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
