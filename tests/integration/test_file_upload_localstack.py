"""起動済みLocalStackで認証APIから実S3への送信・登録を検証する。"""

import os
from base64 import b64decode
from typing import cast
from urllib.parse import urlparse
from uuid import uuid4

import boto3
import httpx
import pytest
from botocore.config import Config

from app.core.config import settings
from app.routers import auth
from app.routers import test_collaboration as collaboration_router
from app.services.storage import S3Client, StorageService
from tests.routers.test_test_collaboration import context as context

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LOCALSTACK_UPLOAD_TESTS") != "1",
    reason="RUN_LOCALSTACK_UPLOAD_TESTS=1 requires a running LocalStack",
)
ORIGIN = "http://localhost:3000"
PNG = b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAwMCAO+aD1sAAAAASUVORK5CYII="
)


@pytest.fixture
def localstack_storage(monkeypatch):
    """テスト専用バケットを作成し、終了時にバージョンも含めて削除する。"""
    endpoint = os.getenv("LOCALSTACK_ENDPOINT_URL", "http://localhost:4566")
    if urlparse(endpoint).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("The integration test only accepts a loopback LocalStack")
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="ap-northeast-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=3,
            read_timeout=30,
            retries={"max_attempts": 1},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )
    bucket = "syncnesto-upload-e2e-" + uuid4().hex
    s3.create_bucket(
        Bucket=bucket,
        CreateBucketConfiguration={"LocationConstraint": "ap-northeast-1"},
    )
    try:
        s3.put_bucket_versioning(
            Bucket=bucket, VersioningConfiguration={"Status": "Enabled"}
        )
        s3.put_bucket_cors(
            Bucket=bucket,
            CORSConfiguration={
                "CORSRules": [
                    {
                        "AllowedOrigins": [ORIGIN],
                        "AllowedMethods": ["PUT"],
                        "AllowedHeaders": ["content-type"],
                    }
                ]
            },
        )
        monkeypatch.setattr(settings, "aws_s3_bucket_name", bucket)
        monkeypatch.setattr(settings, "file_upload_mode", "presigned")
        storage = StorageService(s3_client=cast(S3Client, s3))
        monkeypatch.setattr(auth, "storage_service", storage)
        monkeypatch.setattr(collaboration_router.evidence_service, "storage", storage)
        yield s3, bucket
    finally:
        for page in s3.get_paginator("list_object_versions").paginate(Bucket=bucket):
            objects = [
                {"Key": obj["Key"], "VersionId": obj["VersionId"]}
                for obj in page.get("Versions", []) + page.get("DeleteMarkers", [])
            ]
            if objects:
                s3.delete_objects(Bucket=bucket, Delete={"Objects": objects})
        s3.delete_bucket(Bucket=bucket)


def login(client, user):
    """実ログインAPIからCookieとCSRF tokenを取得する。"""
    client.cookies.clear()
    response = client.post(
        "/auth/login", json={"email": user.email, "password": "password123"}
    )
    assert response.status_code == 200
    assert client.cookies.get(settings.auth_cookie_name)
    client.headers[settings.csrf_header_name] = client.cookies.get(
        settings.csrf_cookie_name
    )


@pytest.fixture
def evidence_url(client, context, localstack_storage):
    """認証APIと既存のテスト実行APIで添付対象を準備する。"""
    user, _, url, _, _ = context
    login(client, user)
    case = client.get(url + "/cases").json()[0]
    result = client.patch(
        url + f"/cases/{case['id']}",
        json={"version": case["version"], "status": "failed"},
    )
    assert result.status_code == 200
    runs_url = url + f"/cases/{case['id']}/executions"
    execution = client.get(runs_url).json()[0]
    return runs_url + f"/{execution['id']}/evidence"


def put_file(plan, content):
    """ブラウザと同じCORS事前確認・PUTを実際のHTTPで行う。"""
    assert plan["mode"] == "presigned"
    with httpx.Client(timeout=30) as connection:
        preflight = connection.options(
            plan["url"],
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == ORIGIN
        uploaded = connection.put(
            plan["url"], content=content, headers={**plan["headers"], "Origin": ORIGIN}
        )
        assert uploaded.status_code == 200


@pytest.mark.parametrize("size_mib", [5, 20])
def test_evidence_localstack_upload_complete_download(
    client, evidence_url, localstack_storage, size_mib
):
    """4.5MB超・上限20MiBの実ファイルを登録し、同じ内容を取得する。"""
    s3, bucket = localstack_storage
    content = b"x" * (size_mib * 1024 * 1024)
    plan_response = client.post(
        evidence_url + "/upload-plan",
        json={
            "filename": "evidence.txt",
            "content_type": "text/plain",
            "byte_size": len(content),
        },
    )
    assert plan_response.status_code == 200
    plan = plan_response.json()
    put_file(plan, content)
    completed = client.post(
        evidence_url + "/upload-complete", json={"upload_token": plan["upload_token"]}
    )
    assert completed.status_code == 201
    assert completed.json()["byte_size"] == len(content)
    evidence_id = completed.json()["id"]
    download = client.get(evidence_url + f"/{evidence_id}/download")
    assert download.status_code == 200
    assert httpx.get(download.json()["url"], timeout=30).content == content
    objects = s3.list_objects_v2(Bucket=bucket).get("Contents", [])
    assert len(objects) == 1 and objects[0]["Key"].startswith("projects/")
    assert (
        client.post(
            evidence_url + "/upload-complete",
            json={"upload_token": plan["upload_token"]},
        ).json()["id"]
        == evidence_id
    )
    assert len(client.get(evidence_url).json()) == 1


def test_evidence_localstack_rejects_invalid_content(
    client, evidence_url, localstack_storage
):
    """S3に実際に保存されても、PNG以外の内容を登録できない。"""
    s3, bucket = localstack_storage
    plan = client.post(
        evidence_url + "/upload-plan",
        json={"filename": "fake.png", "content_type": "image/png", "byte_size": 4},
    ).json()
    put_file(plan, b"fake")
    response = client.post(
        evidence_url + "/upload-complete", json={"upload_token": plan["upload_token"]}
    )
    assert response.status_code == 400
    assert client.get(evidence_url).json() == []
    assert s3.list_objects_v2(Bucket=bucket).get("Contents", []) == []


def test_avatar_localstack_supports_both_upload_modes(
    client, create_test_user, localstack_storage, monkeypatch
):
    """本人のアイコンを直接送信と従来multipartの両方で更新する。"""
    user = create_test_user(email="localstack-avatar@example.com")
    login(client, user)
    plan = client.post(
        "/auth/me/avatar/upload-plan",
        json={
            "filename": "avatar.png",
            "content_type": "image/png",
            "byte_size": len(PNG),
        },
    ).json()
    put_file(plan, PNG)
    result = client.post(
        "/auth/me/avatar/upload-complete", json={"upload_token": plan["upload_token"]}
    )
    assert result.status_code == 200
    assert httpx.get(result.json()["avatar_url"], timeout=30).content == PNG
    monkeypatch.setattr(settings, "file_upload_mode", "server")
    assert (
        client.post(
            "/auth/me/avatar/upload-plan",
            json={
                "filename": "avatar.png",
                "content_type": "image/png",
                "byte_size": len(PNG),
            },
        ).json()["mode"]
        == "server"
    )
    legacy = client.put(
        "/auth/me/avatar", files={"file": ("avatar.png", PNG, "image/png")}
    )
    assert legacy.status_code == 200
    assert httpx.get(legacy.json()["avatar_url"], timeout=30).content == PNG
