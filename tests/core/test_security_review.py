"""セキュリティレビューで確認した本番ベースライン差分の再現テスト。"""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.xfail(
    strict=True,
    reason="SEC-001: 本番ではOpenAPI/Swagger UIを無効化または保護する必要がある",
)
def test_openapi_schema_is_not_publicly_exposed(client: TestClient) -> None:
    """OpenAPI schemaが公開されていないことを期待する。"""
    response = client.get("/openapi.json")

    assert response.status_code == 404


@pytest.mark.xfail(
    strict=True,
    reason="SEC-002: 本番ではHostヘッダーを許可リストで検証する必要がある",
)
def test_untrusted_host_header_is_rejected(client: TestClient) -> None:
    """許可されていないHostヘッダーを拒否することを期待する。"""
    response = client.get("/", headers={"host": "attacker.example"})

    assert response.status_code == 400


@pytest.mark.xfail(
    strict=True,
    reason=(
        "SEC-003: セキュリティヘッダーを共通middlewareまたはedgeで付与する必要がある"
    ),
)
def test_security_headers_are_set(client: TestClient) -> None:
    """代表的なセキュリティヘッダーが設定されていることを期待する。"""
    response = client.get("/")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors" in response.headers["content-security-policy"]
    assert response.headers["referrer-policy"]
