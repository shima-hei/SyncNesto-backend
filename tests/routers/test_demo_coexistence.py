"""別々のPostgreSQL DBで通常利用とデモ利用の共存を確認する。"""

import os
import subprocess
import sys
from uuid import UUID, uuid4

import jwt
import pytest
from sqlalchemy import create_engine, func, select, text

from app.core.config import settings
from app.core.security import DEMO_AUDIENCE, create_access_token, decode_access_token
from app.db.session import _demo_factory, demo_session_local, engine
from app.models.demo import DemoSession
from app.models.project import Project
from app.models.user import User
from app.services.demo import DemoService
from tests.routers.test_demo import start


@pytest.fixture
def dedicated_demo(monkeypatch, test_database):
    """既存DBを複製せず、テスト専用Postgres内に空の別DBを作る。"""
    name = "demo_test_" + uuid4().hex
    admin = create_engine(engine.url, isolation_level="AUTOCOMMIT")
    url = engine.url.set(database=name).render_as_string(hide_password=False)
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        env = dict(os.environ, DATABASE_URL=url, DEMO_MODE="false", APP_ENV="test")
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            env=env,
            check=True,
            capture_output=True,
        )
        monkeypatch.setattr(settings, "demo_database_url", url)
        monkeypatch.setattr(settings, "demo_mode", True)
        monkeypatch.setattr(settings, "demo_data_isolated", True)
        monkeypatch.setattr(settings, "demo_secret_key", "d" * 48)
        monkeypatch.setattr(settings, "frontend_public_url", "https://testserver")
        monkeypatch.setattr(settings, "demo_cron_secret", "c" * 48)
        for key, value in {
            "demo_aws_region": "ap-southeast-1",
            "demo_aws_access_key_id": "demo-access",
            "demo_aws_secret_access_key": "demo-secret",
            "demo_aws_s3_bucket_name": "demo-bucket",
            "demo_aws_s3_endpoint_url": "https://demo-storage.example",
        }.items():
            monkeypatch.setattr(settings, key, value)
        from scripts import seed_rbac

        with monkeypatch.context() as seed_patch:
            seed_patch.setattr(seed_rbac, "session_local", demo_session_local)
            seed_rbac.seed_rbac(roles_only=True)
        yield
    finally:
        _demo_factory(url).kw["bind"].dispose()
        _demo_factory.cache_clear()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


def test_normal_and_demo_keep_separate_identity_data_storage_and_cleanup(
    dedicated_demo, client, db, create_test_user, monkeypatch
):
    """ID衝突でも通常認証・メール・ファイル・既存セッションを維持する。"""
    monkeypatch.setattr(settings, "frontend_public_url", "http://testserver")
    from fastapi.testclient import TestClient

    from app.routers import account_actions, auth
    from app.services.storage import StorageService
    from tests.fakes.storage import MemoryS3Client

    normal = create_test_user(email="normal@example.com", system_role="system_admin")
    normal_project = Project(tenant_id=1, name="通常の案件", project_code="NORMAL")
    db.add(normal_project)
    db.commit()
    normal_cookie = client.post(
        "/auth/login", json={"email": normal.email, "password": "password123"}
    )
    assert normal_cookie.status_code == 200, normal_cookie.text
    token = client.cookies[settings.auth_cookie_name]
    normal_me = client.get("/auth/me").json()
    assert normal_me["id"] == normal.id and normal_me["demo"] is None

    visitor = TestClient(client.app)
    demo = start(visitor)
    demo_token = visitor.cookies[settings.auth_cookie_name]
    demo_me = visitor.get("/auth/me").json()
    assert demo_me["id"] == normal.id  # 2つのDBで同じ採番になる。
    assert demo_me["demo"]["id"] == demo["id"]
    with demo_session_local() as demo_db:
        demo_user = demo_db.scalar(select(User))
        demo_project = demo_db.scalar(select(Project))
        assert demo_user is not None and demo_project is not None
        assert demo_user.email != normal.email
        assert demo_project.id == normal_project.id
        demo_project_id = demo_project.id
    assert client.get(f"/projects/{normal_project.id}").json()["name"] == "通常の案件"
    # 別タブでCookieだけが変わっても、古い画面から別realmへ更新・logoutしない。
    for browser, stale in ((client, "demo"), (visitor, "normal")):
        blocked = browser.post(
            "/auth/logout",
            headers={
                "X-Syncnesto-Data-Realm": stale,
                "X-CSRF-Token": browser.cookies["csrf_token"],
            },
        )
        assert blocked.status_code == 403
        assert browser.get("/auth/me").status_code == 200
    assert visitor.get(f"/projects/{demo_project_id}").json()["name"] != "通常の案件"
    assert visitor.get("/users").status_code == 403

    # デモCookieを持っていても通常ログインは通常DBだけで認証する。
    normal_from_demo = TestClient(client.app)
    normal_from_demo.cookies.set(settings.auth_cookie_name, demo_token)
    login = normal_from_demo.post(
        "/auth/login", json={"email": normal.email, "password": "password123"}
    )
    assert login.status_code == 200
    assert normal_from_demo.get("/auth/me").json()["demo"] is None

    sent = []
    monkeypatch.setattr(
        account_actions.service.email_service, "ensure_available", lambda: None
    )
    monkeypatch.setattr(account_actions.service, "public_password_reset", sent.append)
    assert (
        client.post(
            "/auth/password-reset/request",
            json={"email": normal.email},
            headers={"X-CSRF-Token": client.cookies["csrf_token"]},
        ).status_code
        == 202
    )
    assert (
        visitor.post(
            "/auth/password-reset/request", json={"email": normal.email}
        ).status_code
        == 202
    )
    assert sent == [normal.email]

    clients = {}

    class RecordingS3(MemoryS3Client):
        def generate_presigned_url(self, ClientMethod, Params, ExpiresIn):
            return f"https://{Params['Bucket']}.example/{Params['Key']}"

    def s3_factory(_service, **kwargs):
        access = kwargs["aws_access_key_id"]
        return clients.setdefault(access, RecordingS3())

    monkeypatch.setattr("app.services.storage.boto3.client", s3_factory)
    normal_storage = StorageService()
    monkeypatch.setattr(auth, "storage_service", normal_storage)
    assert settings.aws_s3_bucket_name in client.get("/auth/me").json()["avatar_url"]
    assert "demo-bucket" in visitor.get("/auth/me").json()["avatar_url"]
    assert (
        clients[settings.aws_access_key_id]
        is not clients[settings.demo_aws_access_key_id]
    )
    clients[settings.aws_access_key_id].objects["users/normal.png"] = (
        b"normal",
        "image/png",
    )

    assert visitor.post("/auth/logout").status_code == 204
    visitor.cookies.set(settings.auth_cookie_name, demo_token)
    assert visitor.get("/auth/me").status_code == 401
    with demo_session_local() as demo_db:
        assert demo_db.scalar(select(func.count()).select_from(User)) == 0
        assert demo_db.scalar(select(func.count()).select_from(Project)) == 0
    db.expire_all()
    assert (
        db.get(User, normal.id) is not None
        and db.get(Project, normal_project.id) is not None
    )
    assert (
        clients[settings.aws_access_key_id].objects["users/normal.png"][0] == b"normal"
    )
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, token)
    assert client.get("/auth/me").status_code == 200
    assert db.scalar(select(func.count()).select_from(DemoSession)) == 0

    # 受付を止めても専用DBの回収を続け、通常ログインを止めない。
    pending = TestClient(client.app)
    pending_demo = start(pending)
    with demo_session_local() as demo_db:
        DemoService().revoke(
            demo_db,
            UUID(pending_demo["id"]),
            "test",
        )
    monkeypatch.setattr(settings, "demo_mode", False)
    assert pending.get("/auth/me").status_code == 401
    assert client.get("/auth/me").status_code == 200
    cleanup = client.get(
        "/internal/demo/cleanup",
        headers={"Authorization": "Bearer " + settings.demo_cron_secret},
    )
    assert cleanup.status_code == 200 and cleanup.json()["pending"] == 0


@pytest.mark.no_db
def test_demo_tokens_require_separate_signature_and_fixed_audience(monkeypatch):
    """未検証claimによるrealm選択と鍵の取り違えを拒否する。"""
    monkeypatch.setattr(settings, "demo_secret_key", "d" * 48)
    normal = create_access_token("normal@example.com", session_id=uuid4())
    demo = create_access_token("demo@example.com", session_id=uuid4(), demo=True)
    assert "aud" not in decode_access_token(normal)
    assert decode_access_token(demo)["aud"] == DEMO_AUDIENCE
    payload = decode_access_token(demo)
    for key, audience in (
        (settings.secret_key, DEMO_AUDIENCE),
        (settings.demo_secret_key, "other"),
        (settings.demo_secret_key, None),
        ("f" * 48, DEMO_AUDIENCE),
    ):
        altered = dict(payload)
        if audience is None:
            altered.pop("aud")
        else:
            altered["aud"] = audience
        token = jwt.encode(altered, key, algorithm=settings.algorithm)
        with pytest.raises(jwt.PyJWTError):
            decode_access_token(token)
