"""文書MCPの転送量を制限し、部分更新でも版・認可・履歴を維持する。"""

import json
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.config import settings
from app.core.exceptions import BadRequestError, ConflictError
from app.models.document import DocumentRevision
from app.repositories.mcp import McpRepository
from app.schemas.mcp_development import (
    DocumentTextEdit,
    ProjectDocumentContentInput,
    ProjectDocumentPatchInput,
)
from app.services.mcp_document_content import edited_body, read_content
from tests.routers.test_mcp import context as context
from tests.routers.test_mcp import operate, remote_call
from tests.routers.test_mcp import remote_context as remote_context
from tests.routers.test_mcp_development import call
from tests.routers.test_mcp_development import development as development


def document(development, body):
    result = call(
        development,
        "create_project_document",
        data={"title": "部分取得の検証", "body": body},
    )
    assert result.status_code == 200, result.text
    return result.json()


def content(body, **args):
    return read_content(
        body, ProjectDocumentContentInput(project_id=1, document_id=1, **args)
    )


@pytest.mark.no_db
def test_unicode_outline_ranges_search_and_limits():
    """Unicode/CRLF・fence・見出し階層と分割境界を保つ。"""
    body = (
        "😀\r\n# 目的\r\n中🧪\r\n```md\r\n# 除外\r\n````\r\n## 子\r\nx\r\n# 完了\r\ny"
    )
    outline = content(body)
    assert [h["title"] for h in outline["headings"]] == ["目的", "子", "完了"]
    assert outline["headings"][0]["start"] == 3
    assert outline["headings"][0]["end"] == body.index("# 完了")
    assert outline["offset_unit"] == "unicode_code_point"
    assert outline["body_length"] == len(body) and "text" not in outline
    parts, position = [], 0
    while True:
        row = content(body, mode="text", version=1, offset=position, length=7)
        parts.append(row["text"])
        if row["next_offset"] is None:
            break
        position = row["next_offset"]
    assert "".join(parts) == body
    many = "# " + "題" * 500 + "\n" + "## 候補\n" * 51
    first = content(many)
    assert len(first["headings"]) == 50 and first["next_offset"] is not None
    assert first["headings"][0]["title_truncated"]
    assert len(first["headings"][0]["title"]) == 200
    assert len(content(many, version=1, offset=first["next_offset"])["headings"]) == 2
    search_body = "前🧪\n" + "候補\n" * 21
    first = content(search_body, mode="search", version=1, query="候補")
    last = content(
        search_body, mode="search", version=1, query="候補", offset=first["next_offset"]
    )
    assert len(first["matches"]) == 20 and len(last["matches"]) == 1
    assert last["next_offset"] is None
    assert (
        search_body[last["matches"][0]["start"] : last["matches"][0]["end"]] == "候補"
    )
    assert content(body, mode="text", version=1, offset=len(body))["text"] == ""
    with pytest.raises(BadRequestError):
        content(body, mode="text", version=1, offset=len(body) + 1)
    for args in [
        {"mode": "text"},
        {"offset": 1},
        {"mode": "search", "version": 1},
        {"length": 10},
        {"query": "x"},
        {"mode": "text", "version": 1, "length": 8001},
    ]:
        with pytest.raises(ValidationError):
            content(body, **args)


@pytest.mark.no_db
def test_edit_boundaries_capacity_and_transfer_size():
    """重なる編集・原文不一致・上限を拒否し、長文の転送量を比較できる。"""

    def edit(start, end, expected, replacement):
        return DocumentTextEdit(
            start=start, end=end, expected_text=expected, replacement=replacement
        )

    assert (
        edited_body("😀abc\r\n", [edit(1, 2, "a", "中"), edit(6, 6, "", "末尾")])
        == "😀中bc\r\n末尾"
    )
    for edits in [
        [edit(0, 2, "😀a", "x"), edit(1, 2, "a", "y")],
        [edit(1, 1, "", "x"), edit(1, 1, "", "y")],
    ]:
        with pytest.raises(BadRequestError):
            edited_body("😀abc", edits)
    with pytest.raises(ConflictError):
        edited_body("abc", [edit(0, 1, "x", "y")])
    with pytest.raises(BadRequestError):
        edited_body("x" * 100_000, [edit(100_000, 100_000, "", "x")])
    for args in [
        {},
        {"title": None},
        {"title": "  "},
        {"edits": [{"start": 0, "end": 2, "expected_text": "😀", "replacement": "x"}]},
        {"edits": [edit(0, 8000, "x" * 8000, "y" * 8000).model_dump()] * 2},
    ]:
        with pytest.raises(ValidationError):
            ProjectDocumentPatchInput(
                project_id=1, document_id=1, version=1, idempotency_key="test", **args
            )
    body = "# 方針\n" + "本文の確認。" * 10000
    excerpt = content(body, mode="text", version=1, length=2000)

    def size(data):
        return len(json.dumps(data, ensure_ascii=False).encode())

    assert size(excerpt) < size({"body": body}) / 10


def test_version_bound_read_and_patch_history_replay(development, db):
    """原版のUnicode範囲で編集し、再送・no-op・旧版・短い競合を保つ。"""
    body = "😀開始\r\n## 状態\r\n準備中\r\n"
    created = document(development, body)
    args = {"document_id": created["id"]}
    index = call(development, "get_project_document_content", key=False, **args).json()
    assert index["version"] == 1 and "body" not in index
    start = body.index("準備中")
    patch = {
        "version": 1,
        "edits": [
            {
                "start": start,
                "end": start + 3,
                "expected_text": "準備中",
                "replacement": "完了",
            }
        ],
    }
    key = uuid4().hex
    result = call(development, "patch_project_document", key=key, **args, **patch)
    assert result.status_code == 200, result.text
    assert result.json()["version"] == 2 and result.json()["changed"]
    assert "body" not in result.json()
    assert (
        call(development, "patch_project_document", key=key, **args, **patch).json()
        == result.json()
    )
    assert (
        call(
            development,
            "patch_project_document",
            key=key,
            **args,
            **{**patch, "title": "別の入力"},
        ).status_code
        == 409
    )
    current = call(development, "get_project_document", key=False, **args).json()
    assert current["body"] == body.replace("準備中", "完了")
    assert current["title"] == "部分取得の検証"
    assert (
        db.scalar(select(DocumentRevision.body).where(DocumentRevision.number == 1))
        == body
    )
    stale = call(
        development,
        "get_project_document_content",
        key=False,
        mode="text",
        version=1,
        **args,
    )
    assert stale.status_code == 409 and "body" not in json.dumps(stale.json())
    wrong = call(
        development,
        "patch_project_document",
        version=2,
        edits=[{"start": 0, "end": 1, "expected_text": "x", "replacement": "y"}],
        **args,
    )
    assert wrong.status_code == 409
    noop = call(
        development, "patch_project_document", version=2, title=current["title"], **args
    )
    assert noop.status_code == 200 and not noop.json()["changed"]
    assert db.scalar(select(func.count()).select_from(DocumentRevision)) == 2
    titled = call(
        development, "patch_project_document", version=2, title="変更後の題名", **args
    )
    assert titled.status_code == 200 and titled.json()["version"] == 3
    assert (
        call(development, "get_project_document", key=False, **args).json()["body"]
        == current["body"]
    )


def test_partial_operations_scope_gate_and_permissions(
    development, create_test_project, monkeypatch
):
    """新操作でも同意Project・現在のread/update・開発flagを毎回確認する。"""
    from app.services.authorization import AuthorizationService

    created = document(development, "本文")
    queries = [
        ("get_project_document_content", {"key": False}),
        ("patch_project_document", {"version": 1, "title": "更新"}),
    ]
    other = create_test_project()
    direct, headers, _ = development
    for name, extra in queries:
        assert (
            operate(
                direct, headers, name, other.id, document_id=created["id"], **extra
            ).status_code
            == 403
        )
    original = AuthorizationService.has_project_permission

    def allowed(self, *args, **kwargs):
        return kwargs.get("permission_code") != "document:update" and original(
            self, *args, **kwargs
        )

    monkeypatch.setattr(AuthorizationService, "has_project_permission", allowed)
    assert (
        call(
            development,
            "get_project_document_content",
            key=False,
            document_id=created["id"],
        ).status_code
        == 200
    )
    assert (
        call(
            development,
            "patch_project_document",
            document_id=created["id"],
            version=1,
            title="更新",
        ).status_code
        == 403
    )
    monkeypatch.setattr(
        AuthorizationService, "has_project_permission", lambda *a, **kw: False
    )
    for name, extra in queries:
        assert (
            call(development, name, document_id=created["id"], **extra).status_code
            == 403
        )
    monkeypatch.setattr(AuthorizationService, "has_project_permission", original)
    monkeypatch.setattr(settings, "mcp_development_tools_enabled", False)
    for name, extra in queries:
        assert (
            call(development, name, document_id=created["id"], **extra).status_code
            == 404
        )


@pytest.mark.parametrize("failure", ["receipt", "audit"])
def test_patch_receipt_failure_rolls_back(development, db, monkeypatch, failure):
    """本文更新後の再送記録失敗でも本文と版を一緒に戻す。"""
    created = document(development, "変更前")

    def fail(*args, **kwargs):
        raise RuntimeError("receipt unavailable")

    if failure == "receipt":
        monkeypatch.setattr(McpRepository, "save_receipt", fail)
    else:
        from app.repositories.audit_log import AuditLogRepository

        original = AuditLogRepository.create

        def audit(self, *args, **kwargs):
            if kwargs.get("event_type") == "mcp.operation":
                return fail()
            return original(self, *args, **kwargs)

        monkeypatch.setattr(AuditLogRepository, "create", audit)
    with pytest.raises(RuntimeError, match="receipt unavailable"):
        call(
            development,
            "patch_project_document",
            document_id=created["id"],
            version=1,
            title="変更後",
        )
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(DocumentRevision)) == 1
    assert (
        call(
            development, "get_project_document", key=False, document_id=created["id"]
        ).json()["title"]
        == created["title"]
    )


def test_partial_operations_through_embedded_sdk(remote_context, context, monkeypatch):
    """公開と同じSDK/JSON-RPC経路で取得・追記を実行する。"""
    monkeypatch.setattr(settings, "mcp_development_tools_enabled", True)
    remote, _ = remote_context
    args = {"project_id": context[1].id}
    created = remote_call(
        remote,
        "create_project_document",
        {
            **args,
            "idempotency_key": uuid4().hex,
            "data": {"title": "分割読込", "body": "# 目的\n😀本文\n"},
        },
    )["structuredContent"]
    args["document_id"] = created["id"]
    index = remote_call(remote, "get_project_document_content", args)[
        "structuredContent"
    ]
    assert "body" not in index and index["headings"][0]["title"] == "目的"
    size = index["body_length"]
    updated = remote_call(
        remote,
        "patch_project_document",
        {
            **args,
            "version": 1,
            "idempotency_key": uuid4().hex,
            "edits": [
                {
                    "start": size,
                    "end": size,
                    "expected_text": "",
                    "replacement": "追記\n",
                }
            ],
        },
    )
    assert not updated.get("isError") and updated["structuredContent"]["version"] == 2
    last = remote_call(
        remote,
        "get_project_document_content",
        {**args, "version": 2, "mode": "text", "offset": size},
    )["structuredContent"]
    assert last["text"] == "追記\n"
