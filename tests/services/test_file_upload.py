"""送信許可の用途・利用者・期限と保存内容を検証する。"""

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import get_file_upload_mode, settings
from app.core.exceptions import BadRequestError
from app.schemas.file_upload import FileUploadRequest
from app.services.file_upload import FileUploadService
from app.services.storage import StorageService
from tests.fakes.storage import MemoryS3Client


@pytest.fixture
def uploads(monkeypatch):
    """署名付き送信を利用する。"""
    monkeypatch.setattr(settings, "file_upload_mode", "presigned")
    return FileUploadService(StorageService(MemoryS3Client()))


def request():
    """検証用のファイル情報。"""
    return FileUploadRequest(filename="a.txt", content_type="text/plain", byte_size=4)


@pytest.mark.parametrize("mode", ["server", "presigned"])
def test_mode_configuration(monkeypatch, mode):
    """環境変数の値を解釈する。"""
    monkeypatch.setenv("FILE_UPLOAD_MODE", mode)
    assert get_file_upload_mode() == mode


def test_invalid_mode_fails_at_startup(monkeypatch):
    """設定間違いを黙って別方式にしない。"""
    monkeypatch.setenv("FILE_UPLOAD_MODE", "unknown")
    with pytest.raises(RuntimeError):
        get_file_upload_mode()


def test_server_plan_has_no_upload_permission(uploads, monkeypatch):
    """従来方式では署名やトークンを発行しない。"""
    monkeypatch.setattr(settings, "file_upload_mode", "server")
    plan = uploads.plan(request(), user_id=1, scope="avatar")
    assert plan.mode == "server"
    assert plan.url is None and plan.upload_token is None


@pytest.mark.parametrize("user_id,scope", [(2, "avatar"), (1, "evidence")])
def test_upload_permission_is_bound_to_owner_and_purpose(uploads, user_id, scope):
    """他人・別用途の完了要求を拒否する。"""
    plan = uploads.plan(request(), user_id=1, scope="avatar")
    with pytest.raises(BadRequestError):
        uploads.verify(plan.upload_token, user_id=user_id, scope=scope)


def test_expired_or_tampered_permission_is_rejected(uploads):
    """署名改変と期限切れを拒否する。"""
    plan = uploads.plan(request(), user_id=1, scope="avatar")
    payload = jwt.decode(plan.upload_token, options={"verify_signature": False})
    payload["exp"] = datetime.now(UTC) - timedelta(seconds=1)
    expired = jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)
    for token in [expired, plan.upload_token + "x"]:
        with pytest.raises(BadRequestError):
            uploads.verify(token, user_id=1, scope="avatar")


def test_upload_permission_cannot_be_used_for_login(uploads):
    """認証JWTとアップロード許可を混同しない。"""
    from app.core.security import decode_access_token

    plan = uploads.plan(request(), user_id=1, scope="avatar")
    with pytest.raises(jwt.InvalidAudienceError):
        decode_access_token(plan.upload_token)


@pytest.mark.parametrize(
    "content,content_type",
    [
        (b"wrong length", "text/plain"),
        (b"test", "application/json"),
    ],
)
def test_uploaded_object_is_checked_against_signed_metadata(content, content_type):
    """申告と異なる保存サイズ・形式を拒否する。"""
    s3 = MemoryS3Client()
    s3.objects["pending"] = (content, content_type)
    with pytest.raises(BadRequestError):
        StorageService(s3).read_uploaded_object(
            key="pending", content_type="text/plain", byte_size=4
        )


def test_presigned_url_signs_content_type_and_length(uploads):
    """URLの署名に対象ファイルの形式・容量を含める。"""
    plan = uploads.plan(request(), user_id=1, scope="avatar")
    upload_id, data, key = uploads.verify(plan.upload_token, user_id=1, scope="avatar")
    assert key == f"pending-uploads/1/{upload_id}"
    assert data.byte_size == 4
    assert uploads.storage.s3_client.presigned_params["ContentLength"] == 4
    assert plan.headers == {"Content-Type": "text/plain"}
