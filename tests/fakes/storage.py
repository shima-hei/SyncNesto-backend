"""StorageServiceのテスト用fake。"""

from io import BytesIO
from typing import Any

from botocore.exceptions import ClientError


class MemoryS3Client:
    """直接送信と確定先を区別できるS3のfake。"""

    def __init__(self) -> None:
        """オブジェクトと署名対象の記録を準備する。"""
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.presigned_params: dict[str, Any] = {}

    def put_object(self, **kwargs: Any) -> object:
        """検証用のファイルを保存する。"""
        self.objects[kwargs["Key"]] = (kwargs["Body"], kwargs["ContentType"])
        return {}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        """ファイルをストリームとして返す。"""
        if kwargs["Key"] not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        content, content_type = self.objects[kwargs["Key"]]
        return {
            "Body": BytesIO(content),
            "ContentLength": len(content),
            "ContentType": content_type,
        }

    def delete_object(self, **kwargs: Any) -> object:
        """一時ファイルを削除する。"""
        self.objects.pop(kwargs["Key"], None)
        return {}

    def generate_presigned_url(
        self,
        ClientMethod: str,
        Params: dict[str, Any],
        ExpiresIn: int,
    ) -> str:
        """署名対象のヘッダーを保持する。"""
        self.presigned_params = Params
        return f"https://storage.example/{Params['Key']}"


class FakeStorageService:
    """テスト用StorageService。"""

    def __init__(self) -> None:
        """FakeStorageServiceを初期化する。"""
        self.deleted_keys: list[str] = []

    def upload_user_avatar(
        self,
        *,
        user_id: int,
        content: bytes,
        content_type: str | None,
    ) -> str:
        """固定のavatar keyを返す。"""
        return f"users/{user_id}.png"

    def generate_presigned_url(self, avatar_key: str | None) -> str | None:
        """固定の署名付きURLを返す。"""
        if avatar_key is None:
            return None

        return f"https://example.com/{avatar_key}?signature=test"

    def delete_object(self, key: str) -> None:
        """削除対象keyを保持する。"""
        self.deleted_keys.append(key)
