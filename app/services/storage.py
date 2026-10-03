"""S3ストレージ操作を提供するモジュール。"""

from typing import Any, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core import error_messages
from app.core.config import settings
from app.core.exceptions import BadRequestError

ALLOWED_IMAGE_CONTENT_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
MAX_IMAGE_BYTES = 2 * 1024 * 1024


class S3Client(Protocol):
    """StorageServiceで利用するS3クライアントのProtocol。"""

    def put_object(self, **kwargs: object) -> object:
        """S3へオブジェクトを保存する。"""
        ...

    def delete_object(self, **kwargs: object) -> object:
        """S3からオブジェクトを削除する。"""
        ...

    def get_object(self, **kwargs: object) -> dict[str, Any]:
        """保存されたファイルとメタデータを取得する。"""
        ...

    def generate_presigned_url(
        self,
        ClientMethod: str,
        Params: dict[str, Any],
        ExpiresIn: int,
    ) -> str:
        """署名付きURLを生成する。"""
        ...


class StorageService:
    """S3を利用したファイル保存と署名付きURL生成を提供する。"""

    def __init__(self, s3_client: S3Client | None = None) -> None:
        """StorageServiceを初期化する。

        Args:
            s3_client: boto3 S3クライアント。
        """
        s3_client_kwargs: dict[str, object] = {
            "region_name": settings.aws_region,
            "endpoint_url": settings.aws_s3_endpoint_url,
            "config": Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        }
        if settings.aws_access_key_id and settings.aws_secret_access_key:
            s3_client_kwargs["aws_access_key_id"] = settings.aws_access_key_id
            s3_client_kwargs["aws_secret_access_key"] = settings.aws_secret_access_key

        self.s3_client = s3_client or boto3.client("s3", **s3_client_kwargs)

    def upload_user_avatar(
        self,
        *,
        user_id: int,
        content: bytes,
        content_type: str | None,
    ) -> str:
        """ユーザーアイコン画像をS3へアップロードする。

        Args:
            user_id: ユーザーID。
            content: 画像バイナリ。
            content_type: アップロードファイルのContent-Type。

        Returns:
            S3オブジェクトキー。

        Raises:
            BadRequestError: 画像形式またはサイズが不正な場合。
        """
        extension = self._get_image_extension(content_type)
        self._validate_image_size(content)
        avatar_key = f"users/{user_id}.{extension}"
        self.s3_client.put_object(
            Bucket=settings.aws_s3_bucket_name,
            Key=avatar_key,
            Body=content,
            ContentType=content_type,
        )
        return avatar_key

    def generate_presigned_url(self, avatar_key: str | None) -> str | None:
        """S3オブジェクトキーから署名付きURLを生成する。

        Args:
            avatar_key: S3オブジェクトキー。

        Returns:
            署名付きURL。キーがない場合はNone。
        """
        if avatar_key is None:
            return None

        return self.s3_client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": settings.aws_s3_bucket_name,
                "Key": avatar_key,
            },
            ExpiresIn=settings.aws_s3_presigned_url_expires_seconds,
        )

    def delete_object(self, key: str) -> None:
        """S3オブジェクトを削除する。

        Args:
            key: 削除対象のS3オブジェクトキー。
        """
        self.s3_client.delete_object(
            Bucket=settings.aws_s3_bucket_name,
            Key=key,
        )

    def upload_private_object(
        self, *, key: str, content: bytes, content_type: str
    ) -> None:
        """検証済みの用途固有ファイルを非公開S3オブジェクトとして保存する。"""
        self.s3_client.put_object(
            Bucket=settings.aws_s3_bucket_name,
            Key=key,
            Body=content,
            ContentType=content_type,
        )

    def private_object_url(self, key: str) -> str:
        """権限検証後に呼び出す短期有効な非公開オブジェクトURL。"""
        return self.s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.aws_s3_bucket_name, "Key": key},
            ExpiresIn=settings.aws_s3_presigned_url_expires_seconds,
        )

    def presigned_upload_url(
        self, *, key: str, content_type: str, byte_size: int, expires_in: int
    ) -> str:
        """ファイルの形式・容量を署名に含めた一時保存用PUT URLを作る。"""
        return self.s3_client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": settings.aws_s3_bucket_name,
                "Key": key,
                "ContentType": content_type,
                "ContentLength": byte_size,
            },
            ExpiresIn=expires_in,
        )

    def read_uploaded_object(
        self, *, key: str, content_type: str, byte_size: int
    ) -> bytes:
        """許可された容量まで読み込み、実際のメタデータを検証する。"""
        try:
            result = self.s3_client.get_object(
                Bucket=settings.aws_s3_bucket_name, Key=key
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"NoSuchKey", "404"}:
                raise BadRequestError(error_messages.FILE_UPLOAD_MISSING) from exc
            raise
        body = result["Body"]
        try:
            if (
                result["ContentLength"] != byte_size
                or result.get("ContentType") != content_type
            ):
                raise BadRequestError(error_messages.FILE_UPLOAD_MISMATCH)
            content = body.read(byte_size + 1)
            if len(content) != byte_size:
                raise BadRequestError(error_messages.FILE_UPLOAD_MISMATCH)
            return content
        finally:
            body.close()

    def _get_image_extension(self, content_type: str | None) -> str:
        """Content-Typeに対応する画像拡張子を取得する。"""
        if content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
            raise BadRequestError(error_messages.UNSUPPORTED_IMAGE_CONTENT_TYPE)

        return ALLOWED_IMAGE_CONTENT_TYPES[content_type]

    def _validate_image_size(self, content: bytes) -> None:
        """画像サイズが上限以内であることを確認する。"""
        if not content:
            raise BadRequestError(error_messages.IMAGE_FILE_REQUIRED)

        if len(content) > MAX_IMAGE_BYTES:
            raise BadRequestError(error_messages.IMAGE_FILE_TOO_LARGE)
