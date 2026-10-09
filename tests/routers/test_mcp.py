"""OAuthと業務連携の権限境界・競合・原子性を検証する。"""

import base64
import hashlib
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.core.mcp import CLIENT_ID
from app.models.mcp import McpConnection, McpCredential
from app.models.task import Task
from tests.helpers.auth import authorize_as


@pytest.fixture
def context(
    client, db, monkeypatch, create_test_user, create_test_project, assign_project_role
):
    """通常ユーザーにProjectの編集権限を付ける。"""
    monkeypatch.setattr(settings, "mcp_enabled", True)
    monkeypatch.setattr(settings, "mcp_issuer_url", "http://127.0.0.1:8000")
    monkeypatch.setattr(settings, "mcp_resource_url", "http://127.0.0.1:8765/mcp")
    user = create_test_user()
    project = create_test_project()
    member = assign_project_role(user=user, project=project, role_key="member")
    authorize_as(client, user)
    return user, project, member


def begin(client, **overrides):
    """登録済みpublic clientのPKCE要求を開始する。"""
    verifier = "a" * 64
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": "http://127.0.0.1:54321/callback",
        "resource": settings.mcp_resource_url,
        "scope": "mcp:work",
        "state": "random-state",
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        **overrides,
    }
    response = client.get("/oauth/authorize", params=params, follow_redirects=False)
    return response, params, verifier


def connect(client, project_id):
    """ブラウザ同意後、Cookieなしでコードを交換する。"""
    response, params, verifier = begin(client)
    assert response.status_code == 302, response.text
    request_id = parse_qs(urlsplit(response.headers["location"]).query)["request_id"][0]
    root = "/integrations/mcp/authorization-requests/" + request_id
    consent = client.get(root)
    assert consent.status_code == 200, consent.text
    approved = client.post(root + "/approve", json={"project_ids": [project_id]})
    assert approved.status_code == 200, approved.text
    query = parse_qs(urlsplit(approved.json()["redirect_url"]).query)
    assert query["state"] == [params["state"]]
    direct = TestClient(client.app)
    token_params = {
        "client_id": CLIENT_ID,
        "resource": settings.mcp_resource_url,
        "grant_type": "authorization_code",
        "code": query["code"][0],
        "code_verifier": verifier,
        "redirect_uri": params["redirect_uri"],
    }
    response = direct.post("/oauth/token", data=token_params)
    assert response.status_code == 200, response.text
    return direct, response.json(), token_params


def api_headers(direct, access_token):
    """用途を分離した短命資格情報へ交換する。"""
    response = direct.post(
        "/oauth/exchange", headers={"Authorization": "Bearer " + access_token}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def operate(direct, headers, name, project_id, *, key=None, **arguments):
    """明示的な操作を一回呼ぶ。"""
    return direct.post(
        "/integrations/mcp/operations",
        headers=headers,
        json={
            "tool": name,
            "arguments": {
                "project_id": project_id,
                **({"idempotency_key": key or uuid4().hex} if key is not False else {}),
                **arguments,
            },
        },
    )


@pytest.fixture
def remote_context(context, monkeypatch):
    """本体のHTTPS入口・lifespan・OAuth・SDK・通常DBを実物で接続する。"""
    from app.main import create_app

    user, project, _ = context
    monkeypatch.setattr(settings, "mcp_issuer_url", "https://api.example")
    monkeypatch.setattr(settings, "mcp_resource_url", "https://api.example/mcp")
    with TestClient(create_app(), base_url="https://api.example") as remote:
        authorize_as(remote, user, domain="api.example")
        _, tokens, _ = connect(remote, project.id)
        remote.cookies.clear()
        remote.headers["Authorization"] = "Bearer " + tokens["access_token"]
        remote.headers["Accept"] = "application/json, text/event-stream"
        initialized = remote.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        assert initialized.status_code == 200, initialized.text
        remote.headers["MCP-Protocol-Version"] = initialized.json()["result"][
            "protocolVersion"
        ]
        yield remote, tokens


def remote_call(remote, name, arguments):
    """実際のMCP JSON-RPCで一操作だけ実行する。"""
    response = remote.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


def test_embedded_mcp_discovery_tools_auth_and_flag(
    remote_context, client, monkeypatch
):
    """本体で初期化・tool一覧が動き、Host/Origin/Cookie・無効化を守る。"""
    remote, _ = remote_context
    metadata = remote.get("/.well-known/oauth-protected-resource/mcp")
    assert metadata.status_code == 200, metadata.text
    assert metadata.json()["resource"] == "https://api.example/mcp"
    assert metadata.json()["authorization_servers"] == ["https://api.example/"]
    listed = remote.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    )
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["result"]["tools"]) == 28
    assert "mcp-session-id" not in listed.headers
    assert remote.get("/mcp").status_code == 405
    assert remote.delete("/mcp").status_code == 405
    assert (
        remote.post("/mcp", json={}, headers={"Host": "evil.example"}).status_code
        == 421
    )
    assert (
        remote.post(
            "/mcp", json={}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert (
        remote.post("/mcp", json={}, headers={"Cookie": "other=value"}).status_code
        == 400
    )
    assert (
        remote.post(
            "/mcp", json={}, headers={"Authorization": "Bearer invalid"}
        ).status_code
        == 401
    )
    normal_token = client.cookies.get(settings.auth_cookie_name)
    assert (
        remote.post(
            "/mcp", json={}, headers={"Authorization": "Bearer " + normal_token}
        ).status_code
        == 401
    )
    assert remote.get("/auth/me").status_code == 401

    def unavailable(*args):
        raise RuntimeError("private credential must not be returned")

    monkeypatch.setattr("app.services.mcp_auth.McpAuthService.exchange", unavailable)
    failed = remote.post("/mcp", json={})
    assert failed.status_code == 503
    assert failed.json() == {"error": "temporarily_unavailable"}
    monkeypatch.setattr(settings, "mcp_enabled", False)
    assert remote.post("/mcp", json={}).status_code == 404


def test_embedded_mcp_writes_are_atomic_idempotent_and_audited(
    remote_context, context, db, monkeypatch
):
    """自己宛HTTPなしでもsavepoint・再送・監査・失敗時rollbackを維持する。"""
    from app.models.audit_log import AuditLog
    from app.repositories.mcp import McpRepository

    remote, _ = remote_context
    _, project, _ = context
    arguments = {
        "project_id": project.id,
        "idempotency_key": uuid4().hex,
        "data": {"title": "remote-once"},
    }
    first = remote_call(remote, "create_task", arguments)
    assert not first.get("isError"), first
    again = remote_call(remote, "create_task", arguments)
    assert again["structuredContent"] == first["structuredContent"]
    assert len(db.scalars(select(Task).where(Task.title == "remote-once")).all()) == 1
    audit = db.scalar(select(AuditLog).where(AuditLog.event_type == "mcp.operation"))
    assert audit is not None
    assert audit.actor_user_id == context[0].id
    assert audit.extra_metadata["source"] == "mcp"
    assert audit.extra_metadata["operation"] == "create_task"

    def fail(*args, **kwargs):
        raise RuntimeError("private SQL or credential must not be returned")

    monkeypatch.setattr(McpRepository, "save_receipt", fail)
    failed = remote_call(
        remote,
        "create_task",
        {
            **arguments,
            "idempotency_key": uuid4().hex,
            "data": {"title": "remote-rollback"},
        },
    )
    assert failed["isError"] is True
    assert "private SQL" not in str(failed)
    db.expire_all()
    assert db.scalar(select(Task).where(Task.title == "remote-rollback")) is None


def test_embedded_mcp_project_scope_downgrade_and_revoke(
    remote_context, context, db, client, create_test_project
):
    """接続時の許可Projectと、操作時の降格・明示的失効を確認する。"""
    from app.models.rbac import Role

    remote, _ = remote_context
    _, project, member = context
    other = create_test_project()
    denied = remote_call(remote, "list_tasks", {"project_id": other.id})
    assert denied["isError"] is True
    member.role_id = db.scalar(
        select(Role.id).where(Role.key == "viewer", Role.scope == "project")
    )
    db.commit()
    # 利用資格そのものを失う降格は、tool実行より前の資格情報検証で拒否する。
    assert remote.post("/mcp", json={}).status_code == 401
    connection = db.scalar(select(McpConnection))
    assert connection is not None
    assert (
        client.delete(f"/integrations/mcp/connections/{connection.id}").status_code
        == 204
    )
    assert remote.post("/mcp", json={}).status_code == 401


def test_embedded_mcp_requirement_review_uses_exact_quote(remote_context, context):
    """リモートから下書きを作成し、指摘の引用位置・版を再検証する。"""
    remote, _ = remote_context
    _, project, _ = context

    def write(name, **arguments):
        return remote_call(
            remote,
            name,
            {"project_id": project.id, "idempotency_key": uuid4().hex, **arguments},
        )

    document = write(
        "create_requirement_document",
        data={"title": "要求仕様", "document_code": "REMOTE"},
    )["structuredContent"]
    requirement = write(
        "create_requirement",
        data={
            "document_id": document["id"],
            "title": "ログイン",
            "requirement_type": "functional",
            "description": "🔐認証する",
        },
    )["structuredContent"]
    anchor = {
        "target_type": "requirement_item",
        "target_id": requirement["id"],
        "version": requirement["version"],
        "field": "description",
        "quote": "認証",
        "quote_start": 1,
        "body": "認証手段を明記してください",
    }
    comment = write("comment_requirement", **anchor)
    assert not comment.get("isError"), comment
    assert comment["structuredContent"]["target_anchor"]["quote_start"] == 1
    stale = write("comment_requirement", **{**anchor, "version": 99})
    assert stale["isError"] is True
    assert "409" in stale["content"][0]["text"]


@pytest.mark.parametrize(
    "redirect",
    [
        "https://evil.test/callback",
        "http://localhost:54321/callback",
        "http://127.0.0.1:54321/evil",
        "http://127.0.0.1:54321/callback?next=evil",
        "http://127.0.0.1:bad/callback",
    ],
)
def test_reject_redirects(client, context, redirect):
    """リダイレクト先のすり替えを拒否する。"""
    assert begin(client, redirect_uri=redirect)[0].status_code == 400


def test_oauth_pkce_cookie_and_token_boundaries(client, db, context):
    """保存はハッシュのみ、通常CookieとMCP資格情報は相互流用不可。"""
    _, project, _ = context
    direct, tokens, params = connect(client, project.id)
    assert all(
        row.token_hash not in {tokens["access_token"], tokens["refresh_token"]}
        for row in db.scalars(select(McpCredential))
    )
    assert (
        client.post(
            "/oauth/exchange",
            headers={"Authorization": "Bearer " + tokens["access_token"]},
        ).status_code
        == 400
    )
    headers = api_headers(direct, tokens["access_token"])
    assert (
        direct.get(
            "/integrations/mcp/catalog",
            headers={"Authorization": "Bearer " + tokens["access_token"]},
        ).status_code
        == 401
    )
    assert direct.get("/auth/me", headers=headers).status_code == 401
    assert direct.get("/integrations/mcp/catalog", headers=headers).status_code == 200
    assert (
        direct.post(
            "/oauth/token", data={**params, "code_verifier": "b" * 64}
        ).status_code
        == 400
    )
    assert direct.post("/oauth/token", data=params).status_code == 400
    assert direct.get("/integrations/mcp/catalog", headers=headers).status_code == 401


def test_refresh_rotation_and_reuse_revokes_connection(client, context):
    """使用済みrefreshの再利用時には接続を失効する。"""
    _, project, _ = context
    direct, tokens, _ = connect(client, project.id)
    data = {
        "client_id": CLIENT_ID,
        "resource": settings.mcp_resource_url,
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
    }
    new = direct.post("/oauth/token", data=data)
    assert new.status_code == 200
    assert new.json()["refresh_token"] != tokens["refresh_token"]
    assert direct.post("/oauth/token", data=data).status_code == 400
    assert (
        direct.post(
            "/oauth/exchange",
            headers={"Authorization": "Bearer " + new.json()["access_token"]},
        ).status_code
        == 401
    )


def test_live_role_downgrade_and_project_scope(
    client, db, context, create_test_project
):
    """許可外Projectと、接続後の閲覧専用への降格を拒否する。"""
    _, project, member = context
    other = create_test_project()
    direct, tokens, _ = connect(client, project.id)
    headers = api_headers(direct, tokens["access_token"])
    assert (
        operate(
            direct, headers, "create_task", other.id, data={"title": "escape"}
        ).status_code
        == 403
    )
    from app.models.rbac import Role

    member.role_id = db.scalar(
        select(Role.id).where(Role.key == "viewer", Role.scope == "project")
    )
    db.commit()
    assert (
        operate(
            direct, headers, "create_task", project.id, data={"title": "denied"}
        ).status_code
        == 403
    )


def test_guest_editor_can_connect_viewer_cannot(client, db, context):
    """guest属性だけでは拒否しない。閲覧権限だけなら接続候補から除外。"""
    user, project, member = context
    user.user_type = "guest"
    db.commit()
    connect(client, project.id)
    from app.models.rbac import Role

    member.role_id = db.scalar(
        select(Role.id).where(Role.key == "viewer", Role.scope == "project")
    )
    db.commit()
    response, _, _ = begin(client)
    request_id = parse_qs(urlsplit(response.headers["location"]).query)["request_id"][0]
    assert (
        client.get(f"/integrations/mcp/authorization-requests/{request_id}").json()[
            "projects"
        ]
        == []
    )
    assert (
        client.post(
            f"/integrations/mcp/authorization-requests/{request_id}/approve",
            json={"project_ids": [project.id]},
        ).status_code
        == 403
    )


def test_task_idempotency_and_atomic_rollback(client, db, context, monkeypatch):
    """再送は一回だけ作成し、内部commit後の例外でも全て戻す。"""
    _, project, _ = context
    direct, tokens, _ = connect(client, project.id)
    headers = api_headers(direct, tokens["access_token"])
    key = uuid4().hex
    created = operate(
        direct, headers, "create_task", project.id, key=key, data={"title": "once"}
    )
    assert created.status_code == 200, created.text
    assert (
        operate(
            direct, headers, "create_task", project.id, key=key, data={"title": "once"}
        ).json()
        == created.json()
    )
    assert (
        operate(
            direct, headers, "create_task", project.id, key=key, data={"title": "twice"}
        ).status_code
        == 409
    )
    from app.repositories.mcp import McpRepository

    def fail(*args, **kwargs):
        raise RuntimeError("receipt unavailable")

    monkeypatch.setattr(McpRepository, "save_receipt", fail)
    with pytest.raises(RuntimeError, match="receipt unavailable"):
        operate(direct, headers, "create_task", project.id, data={"title": "rollback"})
    db.expire_all()
    assert db.scalar(select(Task).where(Task.title == "rollback")) is None
    assert len(db.scalars(select(Task).where(Task.title == "once")).all()) == 1


def test_revoke_owned_connection(client, db, context, create_test_user):
    """本人だけ取り消せて、既存の派生資格情報も次の操作で失効する。"""
    _, project, _ = context
    direct, tokens, _ = connect(client, project.id)
    headers = api_headers(direct, tokens["access_token"])
    connection = db.scalar(select(McpConnection))
    assert connection is not None
    assert (
        client.delete(f"/integrations/mcp/connections/{connection.id}").status_code
        == 204
    )
    assert direct.get("/integrations/mcp/catalog", headers=headers).status_code == 401


def test_requirement_quote_and_stale_version(client, context):
    """指摘を一件ずつ特定箇所へ投稿し、曖昧・古い参照を拒否する。"""
    _, project, _ = context
    direct, tokens, _ = connect(client, project.id)
    headers = api_headers(direct, tokens["access_token"])
    doc = operate(
        direct,
        headers,
        "create_requirement_document",
        project.id,
        data={"title": "要求仕様", "document_code": "REQ"},
    ).json()
    req = operate(
        direct,
        headers,
        "create_requirement",
        project.id,
        data={
            "document_id": doc["id"],
            "title": "ログイン",
            "requirement_type": "functional",
            "description": "認証する",
        },
    )
    assert req.status_code == 200, req.text
    data = {
        "target_type": "requirement_item",
        "target_id": req.json()["id"],
        "version": 1,
        "field": "description",
        "quote": "認証",
        "quote_start": 0,
        "body": "認証手段を明記してください",
    }
    result = operate(direct, headers, "comment_requirement", project.id, **data)
    assert result.status_code == 200, result.text
    assert result.json()["target_anchor"]["quote"] == "認証"
    assert (
        operate(
            direct,
            headers,
            "comment_requirement",
            project.id,
            **{**data, "version": 99},
        ).status_code
        == 409
    )
    assert (
        operate(
            direct,
            headers,
            "comment_requirement",
            project.id,
            **{**data, "quote": "別の文"},
        ).status_code
        == 409
    )


def test_design_append_preserves_execution_and_exact_matrix_cell(client, db, context):
    """追加で既存設計・実行履歴を壊さず、未選択のセルも正確に指摘する。"""
    _, project, _ = context
    direct, tokens, _ = connect(client, project.id)
    headers = api_headers(direct, tokens["access_token"])
    design = operate(
        direct, headers, "create_test_design", project.id, data={"name": "設計"}
    ).json()
    table, factor, first, second, pattern, item = [str(uuid4()) for _ in range(6)]
    payload = {
        "design_id": design["id"],
        "version": 1,
        "pattern_tables": [{"id": table, "name": "入力"}],
        "factors": [{"id": factor, "table_id": table, "name": "文字数"}],
        "levels": [
            {"id": first, "factor_id": factor, "name": "0"},
            {"id": second, "factor_id": factor, "name": "1"},
        ],
        "patterns": [{"id": pattern, "table_id": table, "code": "P1"}],
        "values": [
            {
                "id": str(uuid4()),
                "pattern_id": pattern,
                "factor_id": factor,
                "level_id": first,
            }
        ],
        "items": [
            {"id": item, "code": "T1", "content": "入力する", "pattern_table_id": table}
        ],
    }
    added = operate(direct, headers, "append_test_design", project.id, **payload)
    assert added.status_code == 200, added.text
    assert added.json()["version"] == 2
    root = f"/projects/{project.id}/test-designs/{design['id']}"
    case = client.get(root + "/cases").json()[0]
    executed = client.patch(
        root + f"/cases/{case['id']}",
        json={"version": case["version"], "status": "passed", "actual_result": "OK"},
    )
    assert executed.status_code == 200, executed.text
    original = client.get(root).json()
    cell = {
        "target_type": "combination",
        "target_id": pattern,
        "field": f"level:{factor}:{second}",
        "body": "この境界値も確認してください",
    }
    comment = operate(
        direct,
        headers,
        "comment_test_design",
        project.id,
        design_id=design["id"],
        version=2,
        data=cell,
    )
    assert comment.status_code == 200, comment.text
    assert comment.json()["target_snapshot"]["label"].endswith(" · 1")
    assert (
        operate(
            direct,
            headers,
            "comment_test_design",
            project.id,
            design_id=design["id"],
            version=1,
            data=cell,
        ).status_code
        == 409
    )
    assert (
        operate(
            direct,
            headers,
            "comment_test_design",
            project.id,
            design_id=design["id"],
            version=2,
            data={**cell, "field": f"level:{factor}"},
        ).status_code
        == 400
    )
    another = operate(
        direct,
        headers,
        "append_test_design",
        project.id,
        design_id=design["id"],
        version=2,
        items=[{"id": str(uuid4()), "code": "T2", "content": "追加"}],
    )
    assert another.status_code == 200, another.text
    latest = client.get(root).json()
    assert latest["pattern_tables"] == original["pattern_tables"]
    assert latest["layout"] == original["layout"]
    assert (
        latest["items"][0] in original["items"]
        or latest["items"][1] in original["items"]
    )
    cases = client.get(root + "/cases").json()
    assert (
        next(row for row in cases if row["id"] == case["id"])["actual_result"] == "OK"
    )
    assert len(client.get(root + f"/cases/{case['id']}/executions").json()) == 1


def test_schedule_preview_conflict_and_atomic_apply(client, db, context):
    """日程案は読むだけで、確認済みの同一版だけを全件反映する。"""
    _, project, _ = context
    direct, tokens, _ = connect(client, project.id)
    headers = api_headers(direct, tokens["access_token"])
    rows = [
        operate(
            direct, headers, "create_task", project.id, data={"title": title}
        ).json()
        for title in ("A", "B")
    ]
    changes = [
        {
            "task_id": row["id"],
            "version": row["version"],
            "start_date": "2026-10-10",
            "due_date": "2026-10-11",
        }
        for row in rows
    ]
    key = uuid4().hex
    preview = operate(
        direct,
        headers,
        "preview_task_schedule",
        project.id,
        key=key,
        changes=changes,
        assumptions=["休日と稼働量は未考慮"],
    )
    assert preview.status_code == 200, preview.text
    assert all(db.get(Task, row["id"]).start_date is None for row in rows)
    applied = operate(
        direct,
        headers,
        "apply_task_schedule",
        project.id,
        preview_key=key,
        confirmation_hash=preview.json()["confirmation_hash"],
    )
    assert applied.status_code == 200, applied.text
    assert len(applied.json()["items"]) == 2
    # 別の再送キーでも古いプレビューから再適用しない。
    assert (
        operate(
            direct,
            headers,
            "apply_task_schedule",
            project.id,
            preview_key=key,
            confirmation_hash=preview.json()["confirmation_hash"],
        ).status_code
        == 409
    )
    key = uuid4().hex
    for change in changes:
        change["version"] = 2
        change["due_date"] = "2026-10-12"
    preview = operate(
        direct,
        headers,
        "preview_task_schedule",
        project.id,
        key=key,
        changes=changes,
        assumptions=["休日未考慮"],
    )
    assert preview.status_code == 200
    operate(
        direct,
        headers,
        "update_task",
        project.id,
        task_id=rows[1]["id"],
        data={"version": 2, "title": "B edited"},
    )
    assert (
        operate(
            direct,
            headers,
            "apply_task_schedule",
            project.id,
            preview_key=key,
            confirmation_hash=preview.json()["confirmation_hash"],
        ).status_code
        == 409
    )
    db.expire_all()
    assert str(db.get(Task, rows[0]["id"]).due_date) == "2026-10-11"


def test_patch_idempotency_distinguishes_omitted_and_null(client, context):
    """省略とnullは異なる更新なので再送キーを使い回せない。"""
    _, project, _ = context
    direct, tokens, _ = connect(client, project.id)
    headers = api_headers(direct, tokens["access_token"])
    task = operate(
        direct,
        headers,
        "create_task",
        project.id,
        data={"title": "日程あり", "start_date": "2026-10-10"},
    ).json()
    key = uuid4().hex
    assert (
        operate(
            direct,
            headers,
            "update_task",
            project.id,
            key=key,
            task_id=task["id"],
            data={"version": 1, "title": "変更"},
        ).status_code
        == 200
    )
    assert (
        operate(
            direct,
            headers,
            "update_task",
            project.id,
            key=key,
            task_id=task["id"],
            data={"version": 1, "title": "変更", "start_date": None},
        ).status_code
        == 409
    )


def test_local_mcp_to_real_api_end_to_end(client, context, monkeypatch):
    """同意・token交換・公式SDK・専用API・保存まで実物をつなぐ。"""
    import httpx

    from syncnesto_mcp.server import create_server

    _, project, _ = context
    _, tokens, _ = connect(client, project.id)
    original = httpx.AsyncClient

    def backend_client(**kwargs):
        return original(transport=httpx.ASGITransport(app=client.app), **kwargs)

    monkeypatch.setattr("syncnesto_mcp.server.httpx.AsyncClient", backend_client)
    with TestClient(
        create_server(settings.mcp_issuer_url, settings.mcp_resource_url),
        base_url="http://127.0.0.1:8765",
    ) as local:
        headers = {
            "Authorization": "Bearer " + tokens["access_token"],
            "Accept": "application/json, text/event-stream",
        }
        init = local.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        assert init.status_code == 200, init.text
        headers["MCP-Protocol-Version"] = init.json()["result"]["protocolVersion"]
        catalog = local.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        assert catalog.status_code == 200, catalog.text
        assert any(
            tool["name"] == "create_task" for tool in catalog.json()["result"]["tools"]
        )
        result = local.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "create_task",
                    "arguments": {
                        "project_id": project.id,
                        "idempotency_key": uuid4().hex,
                        "data": {"title": "MCPから起票"},
                    },
                },
            },
        )
        assert result.status_code == 200, result.text
        body = result.json()["result"]
        assert not body.get("isError"), body
        assert body["structuredContent"]["title"] == "MCPから起票"


def test_execution_only_role_cannot_connect_even_with_mcp_permission(
    client, db, context
):
    """テスト実行permissionは業務編集permissionの代わりにならない。"""
    from app.models.rbac import Permission, Role, RolePermission

    _, project, member = context
    role = Role(key="executor", name="実行専用", scope="project")
    db.add(role)
    db.flush()
    for permission in db.scalars(
        select(Permission).where(
            Permission.code.in_(
                ["mcp:connect", "project:read", "test_plan:read", "test_case:execute"]
            )
        )
    ):
        db.add(RolePermission(role_id=role.id, permission_id=permission.id))
    member.role_id = role.id
    db.commit()
    response, _, _ = begin(client)
    request_id = parse_qs(urlsplit(response.headers["location"]).query)["request_id"][0]
    root = f"/integrations/mcp/authorization-requests/{request_id}"
    assert client.get(root).json()["projects"] == []
    assert (
        client.post(root + "/approve", json={"project_ids": [project.id]}).status_code
        == 403
    )


@pytest.mark.parametrize(
    "change", ["disabled", "password", "tenant_removed", "project_removed"]
)
def test_identity_and_membership_changes_take_effect_next_call(
    client, db, context, change
):
    """認証情報や所属の変更を接続時のスナップショットで固定しない。"""
    from datetime import UTC, datetime

    from app.models.tenant import TenantMember

    user, project, member = context
    direct, tokens, _ = connect(client, project.id)
    headers = api_headers(direct, tokens["access_token"])
    if change == "disabled":
        user.is_active = False
    elif change == "password":
        user.hashed_password = "changed-password-hash"
    elif change == "tenant_removed":
        db.scalar(
            select(TenantMember).where(TenantMember.user_id == user.id)
        ).status = "inactive"
    else:
        member.deleted_at = datetime.now(UTC)
    db.commit()
    assert direct.get("/integrations/mcp/catalog", headers=headers).status_code in {
        401,
        403,
    }


def test_mcp_migration_round_trip(test_database):
    """隔離テストDBでdowngradeとupgradeを往復し既存テーブルを保つ。"""
    from alembic.config import Config
    from sqlalchemy import inspect

    from alembic import command
    from app.db.session import engine

    assert engine.url.database == "syncnesto_test"
    config = Config("alembic.ini")
    try:
        command.downgrade(config, "48bb3c9773b3")
        assert "mcp_connections" not in inspect(engine).get_table_names()
        assert "tasks" in inspect(engine).get_table_names()
    finally:
        command.upgrade(config, "head")
    assert "mcp_operation_receipts" in inspect(engine).get_table_names()


def test_concurrent_retry_creates_once(client, db, context):
    """同じキーで同時に到着しても書き込みを二重実行しない。"""
    from concurrent.futures import ThreadPoolExecutor

    _, project, _ = context
    direct, tokens, _ = connect(client, project.id)
    headers = api_headers(direct, tokens["access_token"])
    key = uuid4().hex

    def run(_):
        with TestClient(client.app) as separate:
            return operate(
                separate,
                headers,
                "create_task",
                project.id,
                key=key,
                data={"title": "concurrent"},
            )

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(run, range(4)))
    assert all(response.status_code == 200 for response in responses)
    assert len({response.json()["id"] for response in responses}) == 1
    assert len(db.scalars(select(Task).where(Task.title == "concurrent")).all()) == 1


def test_operation_lock_survives_inner_rollback(test_database):
    """内部Repositoryのリトライがsavepointを戻しても再送ロックを失わない。"""
    from sqlalchemy import text

    from app.db.mcp import get_mcp_db
    from app.db.session import engine
    from app.repositories.mcp import McpRepository

    connection_id, key = uuid4(), uuid4().hex
    generator = get_mcp_db()
    session = next(generator)
    try:
        McpRepository().lock_operation(session, connection_id, key)
        session.execute(text("SELECT 1"))
        session.rollback()
        raw = hashlib.sha256(f"{connection_id}:{key}".encode()).digest()[:8]
        with engine.begin() as other:
            assert (
                other.scalar(
                    text("SELECT pg_try_advisory_xact_lock(:key)"),
                    {"key": int.from_bytes(raw, "big", signed=True)},
                )
                is False
            )
    finally:
        generator.close()


def test_cross_tenant_consent_and_targets_are_rejected(
    client, db, context, assign_project_role
):
    """複数組織の権限を持っていても一つの接続へ混ぜない。"""
    from app.models.project import Project
    from app.models.rbac import Role
    from app.models.requirement import RequirementDocument
    from app.models.tenant import Tenant, TenantMember

    user, project, _ = context
    other_tenant = Tenant(name="Other", slug="other")
    db.add(other_tenant)
    db.flush()
    role = db.scalar(
        select(Role).where(Role.key == "tenant_member", Role.scope == "tenant")
    )
    db.add(
        TenantMember(
            tenant_id=other_tenant.id,
            user_id=user.id,
            role_id=role.id,
            display_name=user.name,
        )
    )
    other = Project(
        tenant_id=other_tenant.id, name="Other project", project_code="OTHER"
    )
    db.add(other)
    db.commit()
    assign_project_role(user=user, project=other, role_key="member")
    response, _, _ = begin(client)
    request_id = parse_qs(urlsplit(response.headers["location"]).query)["request_id"][0]
    assert (
        client.post(
            f"/integrations/mcp/authorization-requests/{request_id}/approve",
            json={"project_ids": [project.id, other.id]},
        ).status_code
        == 403
    )
    document = RequirementDocument(
        project_id=other.id, title="Other requirement", document_code="OTHER"
    )
    db.add(document)
    db.commit()
    direct, tokens, _ = connect(client, project.id)
    headers = api_headers(direct, tokens["access_token"])
    assert (
        operate(
            direct,
            headers,
            "get_requirement_target",
            project.id,
            key=False,
            target_type="document",
            target_id=document.id,
        ).status_code
        == 404
    )


def test_consent_requires_csrf_and_revocation_requires_owner(
    client, db, context, create_test_user
):
    """CookieがあってもCSRFなしの同意、別人の取消はできない。"""
    _, project, _ = context
    response, _, _ = begin(client)
    request_id = parse_qs(urlsplit(response.headers["location"]).query)["request_id"][0]
    csrf = client.headers.pop(settings.csrf_header_name)
    assert (
        client.post(
            f"/integrations/mcp/authorization-requests/{request_id}/approve",
            json={"project_ids": [project.id]},
        ).status_code
        == 403
    )
    client.headers[settings.csrf_header_name] = csrf
    connect(client, project.id)
    connection = db.scalar(select(McpConnection))
    other = create_test_user(email="other@example.test")
    authorize_as(client, other)
    assert (
        client.delete(f"/integrations/mcp/connections/{connection.id}").status_code
        == 404
    )


def test_demo_account_cannot_authorize_mcp(client, context, demo_settings, monkeypatch):
    """通常DB用MCPへデモユーザーを接続しない。"""
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "frontend_public_url", "http://testserver")
    client.cookies.clear()
    client.headers.pop(settings.csrf_header_name, None)
    assert client.get("/demo/csrf").status_code == 204
    client.headers.update(
        {"Origin": "http://testserver", "X-CSRF-Token": client.cookies["csrf_token"]}
    )
    response = client.post("/demo/start")
    assert response.status_code == 201, response.text
    request, _, _ = begin(client)
    request_id = parse_qs(urlsplit(request.headers["location"]).query)["request_id"][0]
    assert (
        client.get(f"/integrations/mcp/authorization-requests/{request_id}").status_code
        == 403
    )
    assert client.get("/integrations/mcp/connections").status_code == 403
