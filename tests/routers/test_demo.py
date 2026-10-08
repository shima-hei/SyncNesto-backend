"""実際の認証Cookie・PostgreSQLでデモ境界と破棄を検証する。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import settings
from app.db.session import session_local
from app.models.demo import DemoSession, DemoUpload
from app.models.project import Project
from app.models.session import UserSession
from app.models.task import Task
from app.models.tenant import Tenant
from app.models.user import User
from app.services.demo import DemoService
from app.services.storage import StorageService
from tests.fakes.storage import MemoryS3Client


@pytest.fixture
def demo_client(client, monkeypatch, demo_settings):
    """起動設定は既存test環境、機能設定だけをデモにする。"""
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "frontend_public_url", "http://testserver")
    monkeypatch.setattr(settings, "demo_cron_secret", "c" * 48)
    return client


def start(client: TestClient) -> dict:
    """匿名CSRFを取得してから開始する。"""
    assert client.get("/demo/csrf").status_code == 204
    client.headers.update(
        {"Origin": "http://testserver", "X-CSRF-Token": client.cookies["csrf_token"]}
    )
    response = client.post("/demo/start")
    assert response.status_code == 201, response.text
    client.headers["X-CSRF-Token"] = client.cookies["csrf_token"]
    return response.json()


def test_demo_is_disabled_in_normal_environment(client, db):
    """通常環境では匿名発行も回収操作も公開しない。"""
    assert client.get("/demo/csrf").status_code == 404
    assert client.get("/internal/demo/cleanup").status_code == 404
    assert db.scalar(select(func.count()).select_from(DemoSession)) == 0


def test_disabling_demo_mode_rejects_existing_demo_cookie(demo_client, monkeypatch):
    """フラグを無効にした後も通常セッションとしてデモを継続させない。"""
    start(demo_client)
    monkeypatch.setattr(settings, "demo_mode", False)
    assert demo_client.get("/auth/me").status_code == 401
    assert demo_client.get("/demo/csrf").status_code == 404


def test_demo_password_reset_never_sends_mail(demo_client, monkeypatch):
    """メール抑止も環境名ではなくフラグで切り替える。"""

    def unexpected_mail(*_args):
        raise AssertionError("Demo must not send password reset mail")

    monkeypatch.setattr(
        "app.routers.account_actions.service.email_service.ensure_available",
        unexpected_mail,
    )
    start(demo_client)
    response = demo_client.post(
        "/auth/password-reset/request", json={"email": "real@example.com"}
    )
    assert response.status_code == 202
    assert response.json()["message"] == "デモのためメールは送信しません"


def test_anonymous_start_requires_csrf_and_exact_origin(demo_client, db):
    """匿名開始・クロスサイト開始で資源を発行しない。"""
    assert (
        demo_client.post(
            "/demo/start", headers={"Origin": "http://testserver"}
        ).status_code
        == 403
    )
    demo_client.get("/demo/csrf")
    token = demo_client.cookies["csrf_token"]
    assert (
        demo_client.post(
            "/demo/start",
            headers={"X-CSRF-Token": token, "Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    assert (
        demo_client.post("/demo/start", headers={"X-CSRF-Token": token}).status_code
        == 403
    )
    assert db.scalar(select(func.count()).select_from(DemoSession)) == 0


def test_demo_has_business_and_tenant_admin_but_no_system_access(demo_client, db):
    """既存の権限経路で業務と組織管理を体験できる。"""
    status = start(demo_client)
    me = demo_client.get("/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["demo"]["id"] == status["id"]
    assert me.json()["system_roles"] == []
    assert demo_client.get("/tenants/current/members").status_code == 200
    assert demo_client.get("/tenants/management").status_code == 403
    assert demo_client.get("/users").status_code == 403
    project = db.scalar(select(Project).where(Project.tenant_id == status["tenant_id"]))
    assert project is not None
    response = demo_client.post(
        f"/projects/{project.id}/tasks", json={"title": "自分のタスク"}
    )
    assert response.status_code == 201, response.text
    assert (
        demo_client.get(f"/projects/{project.id}/requirement-documents").status_code
        == 200
    )
    assert demo_client.get(f"/projects/{project.id}/test-designs").status_code == 200
    assert demo_client.post("/demo/start").status_code == 409


def test_visitors_cannot_read_reference_or_modify_each_other(
    demo_client, db, create_test_user
):
    """ID・ヘッダー・Identity完全一致の経路でも境界を越えない。"""
    real = create_test_user(email="real@example.com")
    a = start(demo_client)
    with TestClient(demo_client.app) as other:
        b = start(other)
        assert a["id"] != b["id"] and a["tenant_id"] != b["tenant_id"]
        project = db.scalar(select(Project).where(Project.tenant_id == b["tenant_id"]))
        task = db.scalar(select(Task).where(Task.project_id == project.id))
        assert demo_client.get(f"/tasks/{task.id}").status_code == 404
        assert (
            demo_client.patch(
                f"/tasks/{task.id}", json={"title": "侵入", "version": task.version}
            ).status_code
            == 404
        )
        assert (
            demo_client.get(
                "/tenants/current", headers={"X-Tenant-ID": str(b["tenant_id"])}
            ).status_code
            == 403
        )
        response = demo_client.post(
            "/tenants/current/members",
            json={"email": real.email, "role_key": "tenant_member"},
        )
        assert response.status_code == 404, response.text
        assert demo_client.post(
            f"/projects/{project.id}/tasks", json={"title": "侵入"}
        ).status_code in {403, 404}


def test_new_demo_members_are_synthetic_and_are_removed(
    demo_client, db, create_test_user
):
    """既存アドレスを検索・変更せず、追加した一時Identityも回収する。"""
    real = create_test_user(email="real@example.com", name="本来の名前")
    original_hash = real.hashed_password
    status = start(demo_client)
    response = demo_client.post(
        "/tenants/current/users",
        json={
            "email": real.email,
            "display_name": "デモのメンバー",
            "role_key": "tenant_member",
        },
    )
    assert response.status_code == 201, response.text
    member = response.json()["member"]
    assert member["email"] != real.email
    assert member["email"].endswith("@demo.syncnesto.example.com")
    other = TestClient(demo_client.app)
    assert (
        other.post(
            "/auth/login",
            json={
                "email": member["email"],
                "password": response.json()["initial_password"],
            },
        ).status_code
        == 401
    )
    reset = demo_client.post(
        f"/tenants/current/members/{member['user_id']}/password-reset"
    )
    assert reset.status_code == 202, reset.text
    assert demo_client.post("/auth/logout").status_code == 204
    db.expire_all()
    assert db.get(User, member["user_id"]) is None
    assert db.get(Tenant, status["tenant_id"]) is None
    db.refresh(real)
    assert (
        real.email == "real@example.com"
        and real.name == "本来の名前"
        and real.hashed_password == original_hash
    )


def test_logout_revokes_old_cookie_and_deletes_only_owned_data(
    demo_client, db, create_test_user
):
    """盗まれた古いCookieでも再開できず、通常組織は残る。"""
    real = create_test_user(email="real@example.com")
    status = start(demo_client)
    old_cookie = demo_client.cookies["access_token"]
    old_user = demo_client.get("/auth/me").json()["id"]
    assert demo_client.post("/auth/logout").status_code == 204
    db.expire_all()
    assert db.get(User, old_user) is None
    assert db.get(User, real.id) is not None
    assert db.get(Tenant, 1) is not None
    demo = db.get(DemoSession, UUID(status["id"]))
    assert demo.status == "cleaned" and demo.tenant_id is None
    demo_client.cookies.set("access_token", old_cookie)
    assert demo_client.get("/auth/me").status_code == 401


@pytest.mark.parametrize("kind", ["idle", "absolute", "jwt"])
def test_expired_session_cannot_read_or_write_and_cron_cleans(demo_client, db, kind):
    """利用画面を開きっぱなしでも期限切れの組織を回収する。"""
    status = start(demo_client)
    demo = db.get(DemoSession, UUID(status["id"]))
    session = db.get(UserSession, demo.session_id)
    before = datetime.now(UTC) - timedelta(minutes=1)
    if kind == "idle":
        session.expires_at = demo.expires_at = before
    elif kind == "absolute":
        session.absolute_expires_at = demo.absolute_expires_at = before
        demo.expires_at = before
    else:
        from app.core.security import create_access_token

        user = db.get(User, demo.user_id)
        demo_client.cookies.clear()
        demo_client.cookies.set(
            "access_token",
            create_access_token(
                subject=user.email, session_id=session.id, expires_at=before, demo=True
            ),
        )
    db.commit()
    assert demo_client.get("/auth/me").status_code == 401
    db.expire_all()
    assert db.get(DemoSession, demo.id).status == "cleaned"
    assert DemoService().cleanup(demo.id)
    db.expire_all()
    assert db.get(Tenant, status["tenant_id"]) is None


def test_background_status_does_not_extend_idle_but_business_activity_does(
    demo_client, db
):
    """監視ポーリングで無操作期限を延長しない。"""
    status = start(demo_client)
    demo = db.get(DemoSession, UUID(status["id"]))
    session = db.get(UserSession, demo.session_id)
    expires = datetime.now(UTC) + timedelta(minutes=1)
    demo.expires_at = session.expires_at = expires
    db.commit()
    assert demo_client.get("/demo/status").status_code == 200
    assert demo_client.get("/auth/me").status_code == 200
    db.refresh(demo)
    assert demo.expires_at == expires
    assert demo_client.get("/tenants/current").status_code == 200
    db.refresh(demo)
    assert demo.expires_at > expires
    assert demo.expires_at <= demo.absolute_expires_at


def test_reset_invalidates_old_cookie_and_provides_new_workspace(demo_client, db):
    """同じブラウザのCookieだけを新しい一時環境へ置き換える。"""
    first = start(demo_client)
    old_cookie = demo_client.cookies["access_token"]
    response = demo_client.post("/demo/reset")
    assert response.status_code == 201, response.text
    assert response.json()["id"] != first["id"]
    db.expire_all()
    assert db.get(Tenant, first["tenant_id"]) is None
    assert demo_client.get("/auth/me").status_code == 200
    with TestClient(demo_client.app) as old:
        old.cookies.set("access_token", old_cookie)
        assert old.get("/auth/me").status_code == 401


def test_ip_and_global_limits_are_atomic(demo_client, db):
    """同時発行を含め、回収待ちも10件の共有上限に含める。"""

    def issue(i):
        with session_local() as session:
            try:
                return DemoService().start(session, f"192.0.2.{i}")[0]
            except Exception as exc:
                return exc

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(issue, range(12)))
    assert sum(not isinstance(result, Exception) for result in results) == 10
    assert db.scalar(select(func.count()).select_from(DemoSession)) == 10


def test_same_ip_cannot_issue_four_times(demo_client, db):
    from app.core.exceptions import DemoLimitError

    for _ in range(3):
        DemoService().start(db, "192.0.2.1")
    with pytest.raises(DemoLimitError):
        DemoService().start(db, "192.0.2.1")


def test_upload_reservation_and_delayed_put_are_recovered(demo_client, db, monkeypatch):
    """登録失敗・失効後の遅延PUTも最終sweepで回収し、他のファイルは残す。"""
    from app.routers import auth

    memory = MemoryS3Client()
    storage = StorageService(s3_client=memory)
    monkeypatch.setattr(auth, "storage_service", storage)
    monkeypatch.setattr(settings, "file_upload_mode", "presigned")
    status = start(demo_client)
    demo_id = UUID(status["id"])
    response = demo_client.post(
        "/auth/me/avatar/upload-plan",
        json={"filename": "sample.png", "content_type": "image/png", "byte_size": 10},
    )
    assert response.status_code == 200, response.text
    key = memory.presigned_params["Key"]
    assert key.startswith(f"demo/{demo_id}/")
    memory.objects[key] = (b"1234567890", "image/png")
    memory.objects["users/real.png"] = (b"real", "image/png")
    DemoService().revoke(db, demo_id, "logout")
    assert not DemoService().cleanup(demo_id, storage)
    assert key not in memory.objects and "users/real.png" in memory.objects
    memory.objects[key] = (b"late", "image/png")
    db.expire_all()
    upload = db.scalar(select(DemoUpload).where(DemoUpload.demo_id == demo_id))
    upload.expires_at = datetime.now(UTC) - timedelta(minutes=2)
    db.commit()
    assert DemoService().cleanup(demo_id, storage)
    assert key not in memory.objects and "users/real.png" in memory.objects


def test_failed_cleanup_keeps_revocation_and_retries(demo_client, db, monkeypatch):
    """ファイル削除障害を再ログイン可能な状態へ戻さない。"""
    status = start(demo_client)
    demo_id = UUID(status["id"])
    service = DemoService()
    monkeypatch.setattr(
        service.repository,
        "delete_business_data",
        lambda *args: (_ for _ in ()).throw(RuntimeError("database failure")),
    )
    service.revoke(db, demo_id, "logout")
    assert not service.cleanup(demo_id)
    assert demo_client.get("/auth/me").status_code == 401
    db.expire_all()
    demo = db.get(DemoSession, demo_id)
    assert demo.status == "cleanup_pending" and demo.cleanup_error == "cleanup_failed"
    assert DemoService().cleanup(demo_id)


def test_cron_is_separate_from_cookie_and_bff_authorization(demo_client):
    """公開入口を知っていても回収権限は得られない。"""
    assert demo_client.get("/internal/demo/cleanup").status_code == 403
    response = demo_client.get(
        "/internal/demo/cleanup",
        headers={"Authorization": "Bearer " + settings.demo_cron_secret},
    )
    assert response.status_code == 200, response.text


def test_demo_profile_update_retains_lifetime_metadata(demo_client):
    """本人更新でUIが通常モードへ戻ったり、下書きを永続化したりしない。"""
    status = start(demo_client)
    me = demo_client.get("/auth/me").json()
    response = demo_client.patch(
        "/auth/me", json={"name": "体験中", "version": me["version"]}
    )
    assert response.status_code == 200, response.text
    assert response.json()["demo"]["id"] == status["id"]


def test_project_user_row_and_file_budgets_cannot_be_bypassed(demo_client, db):
    """小さい個別更新や大きい一括保存にも同じ累積上限を適用する。"""
    from app.core.exceptions import DemoLimitError

    status = start(demo_client)
    for i in range(2):
        response = demo_client.post(
            "/projects", json={"project_code": f"EXTRA-{i}", "name": "追加案件"}
        )
        assert response.status_code == 201, response.text
    assert (
        demo_client.post(
            "/projects", json={"project_code": "OVER", "name": "上限超過"}
        ).status_code
        == 429
    )
    for i in range(9):
        response = demo_client.post(
            "/tenants/current/users",
            json={
                "email": f"fake{i}@example.com",
                "display_name": f"メンバー{i}",
                "role_key": "tenant_member",
            },
        )
        assert response.status_code == 201, response.text
    assert (
        demo_client.post(
            "/tenants/current/users",
            json={
                "email": "more@example.com",
                "display_name": "11人目",
                "role_key": "tenant_member",
            },
        ).status_code
        == 429
    )
    demo = db.get(DemoSession, UUID(status["id"]))
    DemoService().bind(db, db.get(UserSession, demo.session_id))
    project = db.scalar(select(Project).where(Project.tenant_id == status["tenant_id"]))
    db.add_all(
        Task(project_id=project.id, task_code=f"BULK-{i}", title="一括")
        for i in range(500)
    )
    with pytest.raises(DemoLimitError):
        db.commit()
    db.rollback()
    assert db.scalar(select(func.count()).select_from(Task)) == 1
    for i in range(4):
        DemoService().reserve_upload(
            db,
            f"demo/{demo.id}/{i}",
            5 * 1024 * 1024,
            datetime.now(UTC) + timedelta(minutes=10),
        )
    with pytest.raises(DemoLimitError):
        DemoService().reserve_upload(
            db, f"demo/{demo.id}/extra", 1, datetime.now(UTC) + timedelta(minutes=10)
        )


def test_storage_failure_preserves_pending_cleanup_and_retries(demo_client, db):
    """S3が失敗してもsessionを復活させず、再実行で同じprefixを回収する。"""

    class BrokenS3(MemoryS3Client):
        def delete_object(self, **kwargs):
            raise RuntimeError("storage unavailable")

    status = start(demo_client)
    demo_id = UUID(status["id"])
    demo = db.get(DemoSession, demo_id)
    DemoService().bind(db, db.get(UserSession, demo.session_id))
    DemoService().reserve_upload(
        db, f"demo/{demo_id}/reserved", 1, datetime.now(UTC) - timedelta(minutes=2)
    )
    broken = BrokenS3()
    broken.objects[f"demo/{demo_id}/reserved"] = (b"x", "text/plain")
    DemoService().revoke(db, demo_id, "logout")
    assert not DemoService().cleanup(demo_id, StorageService(s3_client=broken))
    assert demo_client.get("/auth/me").status_code == 401
    memory = MemoryS3Client()
    memory.objects.update(broken.objects)
    assert DemoService().cleanup(demo_id, StorageService(s3_client=memory))
    assert not memory.objects
