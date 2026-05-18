"""StorageServiceのテスト。"""

import pytest

from app.core.config import settings
from app.core.exceptions import BadRequestError
from app.services.storage import StorageService


class FakeS3Client:
    """テスト用S3クライアント。"""

    def __init__(self) -> None:
        """FakeS3Clientを初期化する。"""
        self.put_object_kwargs: dict[str, object] | None = None
        self.delete_object_kwargs: dict[str, object] | None = None

    def put_object(self, **kwargs: object) -> object:
        """put_objectの呼び出し内容を保持する。"""
        self.put_object_kwargs = kwargs
        return {}

    def delete_object(self, **kwargs: object) -> object:
        """delete_objectの呼び出し内容を保持する。"""
        self.delete_object_kwargs = kwargs
        return {}

    def generate_presigned_url(
        self,
        ClientMethod: str,
        Params: dict[str, str],
        ExpiresIn: int,
    ) -> str:
        """固定の署名付きURLを返す。"""
        return f"https://example.com/{Params['Key']}?expires={ExpiresIn}"


def test_upload_user_avatar_uploads_image_to_user_key() -> None:
    """ユーザーIDとContent-TypeからS3 keyを作成してアップロードする。"""
    s3_client = FakeS3Client()
    storage_service = StorageService(s3_client=s3_client)

    avatar_key = storage_service.upload_user_avatar(
        user_id=1,
        content=b"image-bytes",
        content_type="image/png",
    )

    assert avatar_key == "users/1.png"
    assert s3_client.put_object_kwargs is not None
    assert s3_client.put_object_kwargs["Key"] == "users/1.png"
    assert s3_client.put_object_kwargs["Body"] == b"image-bytes"
    assert s3_client.put_object_kwargs["ContentType"] == "image/png"


def test_upload_user_avatar_rejects_unsupported_content_type() -> None:
    """許可されていないContent-Typeを拒否する。"""
    storage_service = StorageService(s3_client=FakeS3Client())

    with pytest.raises(BadRequestError):
        storage_service.upload_user_avatar(
            user_id=1,
            content=b"image-bytes",
            content_type="image/gif",
        )


def test_upload_user_avatar_rejects_empty_file() -> None:
    """空ファイルを拒否する。"""
    storage_service = StorageService(s3_client=FakeS3Client())

    with pytest.raises(BadRequestError):
        storage_service.upload_user_avatar(
            user_id=1,
            content=b"",
            content_type="image/png",
        )


def test_generate_presigned_url_returns_none_without_avatar_key() -> None:
    """avatar_keyがない場合はNoneを返す。"""
    storage_service = StorageService(s3_client=FakeS3Client())

    assert storage_service.generate_presigned_url(None) is None


def test_delete_object_deletes_s3_object() -> None:
    """指定keyのS3オブジェクトを削除する。"""
    s3_client = FakeS3Client()
    storage_service = StorageService(s3_client=s3_client)

    storage_service.delete_object("users/1.png")

    assert s3_client.delete_object_kwargs == {
        "Bucket": settings.aws_s3_bucket_name,
        "Key": "users/1.png",
    }


def test_storage_service_uses_configured_aws_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """設定されたAWS認証情報をS3クライアントへ渡すことを確認する。"""
    captured_kwargs: dict[str, object] = {}

    def fake_boto3_client(service_name: str, **kwargs: object) -> FakeS3Client:
        captured_kwargs["service_name"] = service_name
        captured_kwargs.update(kwargs)
        return FakeS3Client()

    monkeypatch.setattr("app.services.storage.boto3.client", fake_boto3_client)
    monkeypatch.setattr(settings, "aws_region", "ap-northeast-1")
    monkeypatch.setattr(settings, "aws_s3_endpoint_url", "http://localhost:4566")
    monkeypatch.setattr(settings, "aws_access_key_id", "test")
    monkeypatch.setattr(settings, "aws_secret_access_key", "test")

    StorageService()

    assert captured_kwargs["service_name"] == "s3"
    assert captured_kwargs["region_name"] == "ap-northeast-1"
    assert captured_kwargs["endpoint_url"] == "http://localhost:4566"
    assert captured_kwargs["aws_access_key_id"] == "test"
    assert captured_kwargs["aws_secret_access_key"] == "test"
