"""ヘルスチェックAPIのテスト。"""

from fastapi.testclient import TestClient


def test_read_root_returns_health_check_message(client: TestClient) -> None:
    """ヘルスチェックAPIが正常レスポンスを返すことを確認する。"""
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"message": "health check OK"}
