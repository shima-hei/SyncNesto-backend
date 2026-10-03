"""公開APIがDB処理前に不正アクセスを拒否することを確認する。"""

from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.core.ingress import IngressMiddleware
from app.core.middleware import register_middleware
from app.main import create_app


@pytest.fixture
def ingress_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """DBを呼ばない小さなAPIに本番Middlewareを登録する。"""
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "bff_shared_secret", "s" * 48)
    monkeypatch.setattr(settings, "allowed_hosts", ["testserver"])
    monkeypatch.setattr("app.core.ingress.consume_request_budget", lambda *_args: 0)
    app = FastAPI()

    @app.get("/")
    @app.post("/auth/login")
    @app.post("/projects")
    def endpoint() -> dict[str, bool]:
        """Middlewareを通過した場合だけ成功を返す。"""
        return {"reached": True}

    register_middleware(app)
    return TestClient(app)


def test_direct_request_and_host_spoofing_rejected(ingress_client: TestClient) -> None:
    """共有キーなし・不正キー・不正Hostでエンドポイントを呼ばない。"""
    assert ingress_client.get("/").status_code == 200
    assert ingress_client.post("/auth/login").status_code == 403
    assert (
        ingress_client.post(
            "/auth/login", headers={"X-Syncnesto-BFF-Key": "wrong"}
        ).status_code
        == 403
    )
    assert ingress_client.post(
        "/auth/login", headers={"X-Syncnesto-BFF-Key": "s" * 48}
    ).json() == {"reached": True}
    assert ingress_client.get("/", headers={"Host": "evil.example"}).status_code == 400


def test_login_limit_precedes_db_and_recovers(
    ingress_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一IPは10回で止まり、別IPと次の時間枠は独立する。"""
    clock = [100.0]
    monkeypatch.setattr("app.core.ingress.monotonic", lambda: clock[0])
    headers = {"X-Syncnesto-BFF-Key": "s" * 48, "X-Syncnesto-Client-IP": "192.0.2.1"}
    for _ in range(10):
        assert ingress_client.post("/auth/login", headers=headers).status_code == 200
    limited = ingress_client.post("/auth/login", headers=headers)
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "60"
    headers["X-Syncnesto-Client-IP"] = "192.0.2.2"
    assert ingress_client.post("/auth/login", headers=headers).status_code == 200
    headers["X-Syncnesto-Client-IP"] = "192.0.2.1"
    clock[0] += 60
    assert ingress_client.post("/auth/login", headers=headers).status_code == 200


def test_valid_bff_does_not_bypass_csrf(ingress_client: TestClient) -> None:
    """共有キーはユーザーのCookie・CSRF検証を置き換えない。"""
    headers = {
        "X-Syncnesto-BFF-Key": "s" * 48,
        "Cookie": "access_token=jwt; csrf_token=token",
    }
    assert ingress_client.post("/projects", headers=headers).status_code == 403
    headers["X-CSRF-Token"] = "token"
    assert ingress_client.post("/projects", headers=headers).status_code == 200


def test_bounded_rate_limit_cache(
    ingress_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IPを増やしてもカウンターのメモリを無制限に確保しない。"""
    monkeypatch.setattr(IngressMiddleware, "max_buckets", 1)
    headers = {"X-Syncnesto-BFF-Key": "s" * 48, "X-Syncnesto-Client-IP": "192.0.2.1"}
    assert ingress_client.post("/auth/login", headers=headers).status_code == 200
    headers["X-Syncnesto-Client-IP"] = "192.0.2.2"
    assert ingress_client.post("/auth/login", headers=headers).status_code == 429


def test_shared_budget_is_required(
    ingress_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """共有カウンター超過は429、DB障害は迂回せず503にする。"""
    headers = {"X-Syncnesto-BFF-Key": "s" * 48}
    monkeypatch.setattr("app.core.ingress.consume_request_budget", lambda *_args: 15)
    assert ingress_client.post("/auth/login", headers=headers).status_code == 429

    def broken_budget(*_args) -> int:
        """共有ストアの接続障害を再現する。"""
        raise OperationalError("", {}, Exception("database unavailable"))

    monkeypatch.setattr("app.core.ingress.consume_request_budget", broken_budget)
    response = ingress_client.post("/auth/login", headers=headers)
    assert response.status_code == 503
    assert "database unavailable" not in response.text


def production_settings():
    """本番で必須になる設定を揃えたコピーを作る。"""
    return replace(
        settings,
        app_env="production",
        bff_shared_secret="s" * 48,
        secret_key="j" * 48,
        auth_cookie_secure=True,
        csrf_cookie_secure=True,
        allow_authorization_header=False,
        allow_bearer_token_response=False,
        allowed_hosts=["api.example.com"],
        database_url="postgresql://app:password@db.example.com/app?sslmode=verify-full",
        sql_echo=False,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"bff_shared_secret": ""},
        {"secret_key": "change-me-at-least-32-bytes"},
        {"auth_cookie_secure": False},
        {"csrf_cookie_secure": False},
        {"allow_authorization_header": True},
        {"allow_bearer_token_response": True},
        {"allowed_hosts": ["*"]},
        {"allowed_hosts": []},
        {
            "database_url": "postgresql://app:password@db.example.com/app?sslmode=require"
        },
        {"sql_echo": True},
    ],
)
def test_insecure_production_settings_rejected(changes: dict) -> None:
    """危険な設定は本番起動前にエラーになる。"""
    with pytest.raises(RuntimeError):
        replace(production_settings(), **changes).validate_production()


def test_production_docs_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """本番のOpenAPI・Swagger・ReDocルートを無効にする。"""
    secure = production_settings()
    for name in secure.__dataclass_fields__:
        monkeypatch.setattr(settings, name, getattr(secure, name))
    app = create_app()
    assert app.openapi_url is None
    assert app.docs_url is None
    assert app.redoc_url is None
    client = TestClient(app)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert (
            client.get(
                path,
                headers={
                    "Host": "api.example.com",
                    "X-Syncnesto-BFF-Key": "s" * 48,
                },
            ).status_code
            == 404
        )
