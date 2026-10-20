"""通知・正本判断・失効・既読の境界を実DBで確認する。"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, update

from app.core.config import settings
from app.db.base import Base
from app.models.fixed_approval import FixedApprovalEvent, FixedApprovalRequest
from app.models.notification import Notification
from app.models.rbac import Permission, Role, RolePermission
from app.models.requirement import Requirement
from app.repositories.fixed_approval_notification import (
    FixedApprovalNotificationRepository,
)
from tests.helpers.auth import authorize_as
from tests.routers.test_artifact_releases import context as context  # noqa: F401
from tests.routers.test_fixed_approvals import approval_context as approval_context  # noqa: F401
from tests.routers.test_fixed_approvals import decide, request
from tests.routers.test_guest_fixed_approvals import (
    approval_path,
    create_request,
    decision,
    guest_login,
)
from tests.routers.test_guest_fixed_approvals import (
    guest_approval_context as guest_approval_context,  # noqa: F401
)
from tests.routers.test_publication_access import access_fixture as access_fixture  # noqa: F401


@pytest.fixture(autouse=True)
def notifications_on(monkeypatch):
    """新処理だけを有効にし、既存MFA設定は保持する。"""
    monkeypatch.setattr(settings, "fixed_approval_notifications_enabled", True)


def count(db, model: type[Base] = Notification):
    """業務状態と独立した保存件数を調べる。"""
    return db.scalar(select(func.count()).select_from(model))


def test_internal_event_recipient_replay_and_current_state(
    client, db, approval_context
):
    """指定本人に一度だけ通知し、結果は申請者へ返す。"""
    ctx = approval_context
    row, body = request(client, ctx)
    assert count(db) == 1
    assert client.get("/notifications").json()["total"] == 0
    assert client.post(ctx["approvals"], json=body).status_code == 200
    assert count(db) == 1
    authorize_as(client, ctx["approver"])
    inbox = client.get("/notifications")
    assert inbox.status_code == 200, inbox.text
    item = inbox.json()["items"][0]
    assert item["target_type"] == "fixed_approval"
    assert item["context"]["approval_id"] == row["id"]
    assert item["snapshot"]["target_title"] == ctx["req"].title
    assert item["target_status"] == "available"
    result, payload = decide(client, ctx, row, decision="reject", comment="修正が必要")
    assert result["status"] == "rejected"
    assert (
        client.post(
            f"{ctx['approvals']}/{row['id']}/decisions", json=payload
        ).status_code
        == 200
    )
    assert count(db) == 2
    assert (
        client.get("/notifications").json()["items"][0]["context"]["current_status"]
        == "rejected"
    )
    authorize_as(client, ctx["user"])
    result_notice = client.get("/notifications").json()["items"][0]
    assert result_notice["context"]["action"] == "rejected"
    assert result_notice["snapshot"]["excerpt"] is None
    assert (
        client.get(f"{ctx['approvals']}/{row['id']}").json()["events"][-1]["reason"]
        == "修正が必要"
    )


def test_internal_visibility_before_counts_and_reads(client, db, approval_context):
    """同梱設計のread消失で件数・個別・全件既読から新通知を除く。"""
    ctx = approval_context
    request(client, ctx)
    authorize_as(client, ctx["approver"])
    item = client.get("/notifications").json()["items"][0]
    role = db.scalar(
        select(Role.id).where(Role.key == "project_admin", Role.scope == "project")
    )
    perm = db.scalar(select(Permission.id).where(Permission.code == "document:read"))
    db.execute(
        delete(RolePermission).where(
            RolePermission.role_id == role, RolePermission.permission_id == perm
        )
    )
    db.commit()
    assert client.get("/notifications").json()["total"] == 0
    assert client.get("/notifications/unread-count").json()["count"] == 0
    assert client.post(f"/notifications/{item['id']}/read").status_code == 404
    assert client.post("/notifications/read-all").json()["updated_count"] == 0
    assert db.scalar(select(Notification.read_at)) is None


def test_shared_notice_is_project_scoped_and_public_only(
    client, db, guest_approval_context
):
    """指定本人へだけ公開用投影を返し、内部ID・依頼文を混ぜない。"""
    ctx = guest_approval_context
    row, body = create_request(client, ctx)
    assert count(db) == 1
    assert client.post(ctx["requests"], json=body).status_code == 200
    guest_login(client, db, ctx, index=1)
    assert client.get("/shared/notifications").json()["total"] == 0
    guest_login(client, db, ctx)
    inbox = client.get("/shared/notifications")
    assert inbox.status_code == 200, inbox.text
    assert inbox.json()["total"] == 1
    notice = inbox.json()["items"][0]
    assert notice["approval_id"] == row["id"] and notice["can_decide"]
    assert UUID(notice["id"])
    assert (
        not {
            "project_id",
            "tenant_id",
            "actor_user_id",
            "request_note",
            "scope_manifest",
            "grant_id",
        }
        & notice.keys()
    )
    assert client.get("/notifications").status_code == 403
    guest_login(client, db, ctx, index=1)
    assert client.post(f"/shared/notifications/{notice['id']}/read").status_code == 404


def test_shared_read_is_idempotent_and_not_a_decision(
    client, db, guest_approval_context
):
    """既読・全件既読を判断や合意済みと混同しない。"""
    ctx = guest_approval_context
    row, _ = create_request(client, ctx)
    guest_login(client, db, ctx)
    notice = client.get("/shared/notifications").json()["items"][0]
    path = f"/shared/notifications/{notice['id']}/read"
    first = client.post(path)
    assert first.status_code == 200, first.text
    assert client.post(path).json()["read_at"] == first.json()["read_at"]
    assert client.get("/shared/notifications/unread-count").json()["count"] == 0
    assert client.post("/shared/notifications/read-all").json()["updated_count"] == 0
    db.expire_all()
    assert db.get(FixedApprovalRequest, UUID(row["id"])).status == "requested"
    assert count(db, FixedApprovalEvent) == 1


@pytest.mark.parametrize("state", ["grant", "source", "mfa", "session", "csrf"])
def test_shared_every_operation_rechecks_access(
    client, db, guest_approval_context, state
):
    """失効後に行・count・既読を取得できず、内部の証跡は残る。"""
    from app.models.session import UserSession

    ctx = guest_approval_context
    row, _ = create_request(client, ctx)
    sid = guest_login(client, db, ctx)
    notice = client.get("/shared/notifications").json()["items"][0]
    if state == "grant":
        authorize_as(client, ctx["user"])
        path = (
            f"/projects/{ctx['project'].id}/artifact-publications/"
            f"{ctx['publication_id']}/grants/{ctx['grant']['id']}/revoke"
        )
        result = client.post(
            path, json=dict(request_id=str(uuid4()), version=1, reason="公開を終了")
        )
        assert result.status_code == 200, result.text
        guest_login(client, db, ctx)
    elif state == "source":
        db.get(Requirement, ctx["req"].id).deleted_at = datetime.now(UTC)
        db.commit()
    elif state == "mfa":
        session = db.get(UserSession, sid)
        session.mfa_verified_at = None
        session.mfa_method = None
        db.commit()
    elif state == "session":
        db.get(UserSession, sid).revoked_at = datetime.now(UTC)
        db.commit()
    else:
        client.headers.pop(settings.csrf_header_name)
    if state in {"grant", "source"}:
        assert client.get("/shared/notifications").json()["total"] == 0
        assert client.get("/shared/notifications/unread-count").json()["count"] == 0
        assert (
            client.post("/shared/notifications/read-all").json()["updated_count"] == 0
        )
        assert (
            client.post(f"/shared/notifications/{notice['id']}/read").status_code == 404
        )
    elif state == "csrf":
        assert client.post("/shared/notifications/read-all").status_code == 403
        assert (
            client.post(f"/shared/notifications/{notice['id']}/read").status_code == 403
        )
    else:
        for method, suffix in [
            ("get", ""),
            ("get", "/unread-count"),
            ("post", "/read-all"),
            ("post", f"/{notice['id']}/read"),
        ]:
            response = getattr(client, method)("/shared/notifications" + suffix)
            assert response.status_code in {401, 403}, response.text
            assert "title" not in response.json() and "count" not in response.json()
    assert count(db) == 1 and count(db, FixedApprovalEvent) == 1


def test_guest_result_notifies_only_requester_and_uses_current_status(
    client, db, guest_approval_context
):
    """外部判断後も依頼時通知は過去イベントとして現在状態を示す。"""
    ctx = guest_approval_context
    row, _ = create_request(client, ctx)
    guest_login(client, db, ctx)
    result, body = decision(client, approval_path(ctx, row))
    assert result.status_code == 200, result.text
    assert (
        client.post(approval_path(ctx, row) + "/decisions", json=body).status_code
        == 200
    )
    assert count(db) == 2
    inbox = client.get("/shared/notifications").json()
    assert inbox["total"] == 1 and inbox["items"][0]["current_status"] == "approved"
    assert inbox["items"][0]["can_decide"] is False
    authorize_as(client, ctx["user"])
    inbox = client.get("/notifications")
    assert inbox.status_code == 200, inbox.text
    assert inbox.json()["total"] == 1
    assert inbox.json()["items"][0]["context"]["action"] == "approved"


def test_cancel_is_generic_for_guest(client, db, guest_approval_context):
    """外部へ取消理由を公開せず、取消した本人への通知も作らない。"""
    ctx = guest_approval_context
    row, _ = create_request(client, ctx)
    cancel = client.post(
        f"/projects/{ctx['project'].id}/fixed-approvals/{row['id']}/cancel",
        json=dict(version=1, operation_id=str(uuid4()), reason="社内だけの取消理由"),
    )
    assert cancel.status_code == 200, cancel.text
    guest_login(client, db, ctx)
    inbox = client.get("/shared/notifications")
    assert inbox.json()["total"] == 2
    assert "社内だけ" not in inbox.text
    assert all(
        n["current_status"] == "cancelled" and not n["can_decide"]
        for n in inbox.json()["items"]
    )


def test_notification_failure_rolls_back_request(
    client, db, approval_context, monkeypatch
):
    """業務成功を返した後で通知だけ消失しない。"""

    def unavailable(*args, **kwargs):
        raise RuntimeError("notification unavailable")

    monkeypatch.setattr(FixedApprovalNotificationRepository, "emit", unavailable)
    with pytest.raises(RuntimeError, match="notification unavailable"):
        request(client, approval_context)
    assert count(db) == 0 and count(db, FixedApprovalRequest) == 0
    assert count(db, FixedApprovalEvent) == 0


def test_flag_stop_and_no_backfill(client, db, approval_context, monkeypatch):
    """停止中は業務を維持し、再開時に過去分へ遡及通知しない。"""
    monkeypatch.setattr(settings, "fixed_approval_notifications_enabled", False)
    ctx = approval_context
    row, body = request(client, ctx)
    assert count(db) == 0
    assert client.get("/shared/notifications").status_code == 404
    assert client.get(f"{ctx['approvals']}/{row['id']}/next-actions").status_code == 404
    assert client.get(f"{ctx['approvals']}/{row['id']}").status_code == 200
    monkeypatch.setattr(settings, "fixed_approval_notifications_enabled", True)
    assert client.post(ctx["approvals"], json=body).status_code == 200
    assert count(db) == 0
    authorize_as(client, ctx["approver"])
    decide(client, ctx, row)
    assert count(db) == 1
    monkeypatch.setattr(settings, "fixed_approval_notifications_enabled", False)
    authorize_as(client, ctx["user"])
    assert client.get("/notifications").json()["total"] == 1


def test_next_actions_verifies_source_and_parent(client, db, approval_context):
    """元の要件の実親を解決し、元削除後は固定履歴だけを残す。"""
    ctx = approval_context
    row, _ = request(client, ctx)
    path = f"{ctx['approvals']}/{row['id']}/next-actions"
    response = client.get(path)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["next_actor"]["kind"] == "approver" and not data["next_actor"]["is_you"]
    source = next(a for a in data["actions"] if a["kind"] == "edit_source")
    assert (
        source["source_id"] == ctx["req"].id and source["document_id"] == ctx["doc"].id
    )
    db.get(Requirement, ctx["req"].id).deleted_at = datetime.now(UTC)
    db.commit()
    after = client.get(path)
    assert after.status_code == 200, after.text
    assert not after.json()["actions"]


def test_tampered_notification_relation_is_not_authority(
    client, db, guest_approval_context
):
    """通知のJSONや対象を他依頼へ変更しても閲覧根拠にならない。"""
    ctx = guest_approval_context
    row, _ = create_request(client, ctx)
    db.execute(
        update(Notification).values(
            target_id=str(uuid4()), context={"approval_id": row["id"]}
        )
    )
    db.commit()
    guest_login(client, db, ctx)
    assert client.get("/shared/notifications").json()["total"] == 0
    assert client.post("/shared/notifications/read-all").json()["updated_count"] == 0


def test_shared_pagination_and_all_read(client, db, guest_approval_context):
    """許可集合の件数とページ・未読/一括既読が一致する。"""
    ctx = guest_approval_context
    for kind in ("requirement", "requirement_document", "specification"):
        create_request(client, ctx, kind)
    guest_login(client, db, ctx)
    first = client.get("/shared/notifications?page_size=2").json()
    second = client.get("/shared/notifications?page_size=2&page=2").json()
    assert first["total"] == second["total"] == 3
    assert len(first["items"]) == 2 and len(second["items"]) == 1
    assert len({n["id"] for n in first["items"] + second["items"]}) == 3
    assert client.post("/shared/notifications/read-all").json()["updated_count"] == 3
    assert client.get("/shared/notifications?unread_only=true").json()["total"] == 0
    assert client.post("/shared/notifications/read-all").json()["updated_count"] == 0


def test_shared_queries_do_not_grow_per_notification(
    client, db, guest_approval_context
):
    """件数が増えても通知ごとの再取得を発生させない。"""
    from sqlalchemy import event

    ctx = guest_approval_context
    create_request(client, ctx)
    guest_login(client, db, ctx)
    queries = []

    def measured(connection, cursor, statement, parameters, context, executemany):
        queries.append(1)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", measured)
    try:
        assert client.get("/shared/notifications").status_code == 200
        one = len(queries)
        authorize_as(client, ctx["user"])
        create_request(client, ctx, "specification")
        create_request(client, ctx, "requirement_document")
        guest_login(client, db, ctx)
        queries.clear()
        assert client.get("/shared/notifications").json()["total"] == 3
        assert len(queries) <= one + 1
    finally:
        event.remove(engine, "before_cursor_execute", measured)


@pytest.mark.parametrize("first", ["notification", "revoke"])
def test_shared_notification_and_revocation_serialize(
    client, db, guest_approval_context, monkeypatch, first
):
    """失効が先なら件数0、閲覧が先ならその後の全新規取得を拒否する。"""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from fastapi.testclient import TestClient

    from app.services.artifact_publication_access import (
        ArtifactPublicationAccessService,
    )
    from app.services.shared_notification import SharedNotificationService

    ctx = guest_approval_context
    create_request(client, ctx)
    guest_client = TestClient(client.app)
    guest_login(guest_client, db, ctx)
    admin_client = TestClient(client.app)
    authorize_as(admin_client, ctx["user"])
    entered, released = Event(), Event()
    if first == "notification":
        read = SharedNotificationService.read

        def hold_read(self, *args, **kwargs):
            result = read(self, *args, **kwargs)
            entered.set()
            assert released.wait(15)
            return result

        monkeypatch.setattr(SharedNotificationService, "read", hold_read)
    else:
        record = ArtifactPublicationAccessService._record

        def hold_revoke(self, *args, **kwargs):
            result = record(self, *args, **kwargs)
            entered.set()
            assert released.wait(15)
            return result

        monkeypatch.setattr(ArtifactPublicationAccessService, "_record", hold_revoke)
    path = (
        f"/projects/{ctx['project'].id}/artifact-publications/"
        f"{ctx['publication_id']}/grants/{ctx['grant']['id']}/revoke"
    )

    def notice():
        return guest_client.get("/shared/notifications")

    def revoke():
        return admin_client.post(
            path, json=dict(version=1, request_id=str(uuid4()), reason="競合確認")
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        leading = pool.submit(notice if first == "notification" else revoke)
        assert entered.wait(15)
        trailing = pool.submit(revoke if first == "notification" else notice)
        released.set()
        a, b = leading.result(timeout=20), trailing.result(timeout=20)
    assert a.status_code == b.status_code == 200, (a.text, b.text)
    observed = a if first == "notification" else b
    assert observed.json()["total"] == (1 if first == "notification" else 0)
    assert guest_client.get("/shared/notifications/unread-count").json()["count"] == 0
    assert (
        guest_client.post("/shared/notifications/read-all").json()["updated_count"] == 0
    )


def test_shared_errors_are_private_and_do_not_echo_input(client):
    """未認証と入力失敗にも公開禁止ヘッダーを付ける。"""
    for method, path in [
        ("get", "/shared/notifications"),
        ("get", "/shared/notifications/unread-count"),
        ("post", "/shared/notifications/read-all"),
        ("post", f"/shared/notifications/{uuid4()}/read"),
    ]:
        response = getattr(client, method)(path)
        assert response.status_code == 401
        assert "no-store" in response.headers["cache-control"]
        assert response.headers["referrer-policy"] == "no-referrer"
