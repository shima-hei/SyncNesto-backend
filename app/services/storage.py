"""S3ストレージ操作を提供するモジュール。"""

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from sqlalchemy.orm import Session

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


class PrefixS3Client(S3Client, Protocol):
    """回収処理に必要な一覧API。通常の保存APIと型を分離する。"""

    def list_objects_v2(self, **kwargs: object) -> dict[str, Any]:
        """prefix配下のキーをページ単位で返す。"""
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
        self.demo_db: Session | None = None

    def for_demo(self, db: Session) -> "StorageService":
        """共有インスタンスを書き換えず、request固有の容量管理を付ける。"""
        if not db.info.get("demo_id"):
            return self
        bound = StorageService(s3_client=cast(S3Client, self.s3_client))
        bound.demo_db = db
        return bound

    def object_key(self, key: str) -> str:
        """全ファイルを回収可能な専用prefixへ置く。"""
        if self.demo_db is None:
            return key
        prefix = f"demo/{self.demo_db.info['demo_id']}/"
        return key if key.startswith(prefix) else prefix + key

    def reserve(
        self, key: str, byte_size: int, expires_in: int, upload_id: UUID | None = None
    ) -> None:
        """未完了URLも同じ回収台帳へ登録する。"""
        if self.demo_db is not None:
            from app.services.demo import DemoService

            DemoService().reserve_upload(
                self.demo_db,
                key,
                byte_size,
                datetime.now(UTC) + timedelta(seconds=expires_in),
                upload_id,
            )

    def download_ttl(self, key: str) -> int:
        """デモのURLは最大60秒。認可失効後の残存時間を抑える。"""
        ttl = settings.aws_s3_presigned_url_expires_seconds
        if key.startswith("demo/"):
            ttl = min(ttl, 60)
            if self.demo_db is not None:
                from app.db.demo_scope import lock_active_demo

                remaining = (
                    lock_active_demo(self.demo_db).expires_at - datetime.now(UTC)
                ).total_seconds()
                ttl = min(ttl, max(1, int(remaining)))
        return ttl

    def delete_prefix(self, prefix: str) -> None:
        """予約以外の遅延PUTや登録失敗したファイルもprefix単位で回収する。"""
        if not prefix.startswith("demo/") or len(prefix.split("/")) != 3:
            raise ValueError("Only a single demo prefix may be deleted")
        continuation = None
        while True:
            args: dict[str, Any] = {
                "Bucket": settings.aws_s3_bucket_name,
                "Prefix": prefix,
            }
            if continuation:
                args["ContinuationToken"] = continuation
            result = cast(PrefixS3Client, self.s3_client).list_objects_v2(**args)
            for item in result.get("Contents", []):
                self.delete_object(item["Key"])
            if not result.get("IsTruncated"):
                return
            continuation = result["NextContinuationToken"]

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
        avatar_key = self.object_key(
            f"users/{user_id}.{extension}"
            if self.demo_db is None
            else f"users/{user_id}/{uuid4()}.{extension}"
        )
        self.reserve(avatar_key, len(content), settings.file_upload_url_expires_seconds)
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
            ExpiresIn=self.download_ttl(avatar_key),
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
        self.reserve(key, len(content), settings.file_upload_url_expires_seconds)
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
            ExpiresIn=self.download_ttl(key),
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
