"""元Storageとは別の非公開配信先へ、検証済みの派生コピーを作る。"""

import hashlib
import hmac
import math
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, quote, urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings
from app.core.exceptions import SharedFileUnavailableError
from app.repositories.publication_download import DeliverySource
from app.services.storage import StorageService

MAX_BYTES = 20 * 1024 * 1024


class PublicationDeliveryStorage:
    """公開許可を持たない派生データ。認可は呼出元が毎回行う。"""

    def __init__(self, *, source=None, destination=None):
        """用途ごとにclientとcredentialsを分離する。"""
        endpoint = urlsplit(settings.shared_file_delivery_endpoint_url)
        if (
            settings.is_public_environment
            or endpoint.scheme != "http"
            or endpoint.hostname not in {"127.0.0.1", "localhost"}
            or endpoint.port in {None, 4566}
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
            or endpoint.path not in {"", "/"}
            or not settings.shared_file_delivery_bucket_name
            or settings.shared_file_delivery_bucket_name == settings.aws_s3_bucket_name
            or not settings.shared_file_delivery_access_key_id
            or not settings.shared_file_delivery_secret_access_key
        ):
            raise SharedFileUnavailableError()
        self.bucket = settings.shared_file_delivery_bucket_name
        self.source = source or StorageService(request_timeout_seconds=10)
        self.destination = destination or boto3.client(
            "s3",
            endpoint_url=settings.shared_file_delivery_endpoint_url,
            region_name=settings.aws_region,
            aws_access_key_id=settings.shared_file_delivery_access_key_id,
            aws_secret_access_key=settings.shared_file_delivery_secret_access_key,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
                connect_timeout=5,
                read_timeout=10,
                retries={"total_max_attempts": 1},
            ),
        )

    @staticmethod
    def key(source: DeliverySource) -> str:
        """元キー/IDを可逆に含めない、用途別HMACの派生キー。"""
        message = f"publication-delivery-v1:{source.object_id}:{source.content_digest}"
        value = hmac.new(settings.secret_key.encode(), message.encode(), hashlib.sha256)
        return "delivery-v1/" + value.hexdigest()

    @staticmethod
    def _read(client, bucket, key, source: DeliverySource) -> bytes:
        """上限を超えて読まず、全経路でBodyをcloseする。"""
        result = client.get_object(Bucket=bucket, Key=key)
        body = result["Body"]
        try:
            if result["ContentLength"] != source.byte_size:
                raise SharedFileUnavailableError()
            content = body.read(source.byte_size + 1)
            if (
                len(content) != source.byte_size
                or hashlib.sha256(content).hexdigest() != source.content_digest
            ):
                raise SharedFileUnavailableError()
            return content
        finally:
            body.close()

    def ensure_copy(self, source: DeliverySource) -> str:
        """正本の検証後だけ条件PUTし、競合時も採用bytesを検査する。"""
        if not 0 < source.byte_size <= MAX_BYTES:
            raise SharedFileUnavailableError()
        key = self.key(source)
        try:
            try:
                self._read(self.destination, self.bucket, key, source)
                return key
            except ClientError as error:
                if error.response.get("Error", {}).get("Code") not in {
                    "NoSuchKey",
                    "404",
                }:
                    raise
            content = self._read(
                self.source.s3_client,
                self.source.bucket_name,
                source.storage_key,
                source,
            )
            try:
                self.destination.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=content,
                    ContentType="application/octet-stream",
                    CacheControl="private, no-store",
                    Metadata={"sha256": source.content_digest},
                    IfNoneMatch="*",
                )
            except ClientError as error:
                if error.response.get("ResponseMetadata", {}).get(
                    "HTTPStatusCode"
                ) not in {409, 412}:
                    raise
            self._read(self.destination, self.bucket, key, source)
            return key
        except (BotoCoreError, ClientError, OSError):
            raise SharedFileUnavailableError() from None

    def sign(self, source: DeliverySource, key: str, deadline, db_now):
        """実署名期限を検査し、DB/ホストの両時計で60秒以下へ制限する。"""
        now = datetime.now(UTC)
        if abs((now - db_now).total_seconds()) > 2:
            raise SharedFileUnavailableError()
        deadline = min(
            deadline, db_now + timedelta(seconds=60), now + timedelta(seconds=60)
        )
        ttl = min(60, math.floor((deadline - max(db_now, now)).total_seconds()))
        for _ in range(2):
            if ttl < 1:
                raise SharedFileUnavailableError()
            try:
                url = self.destination.generate_presigned_url(
                    "get_object",
                    Params={
                        "Bucket": self.bucket,
                        "Key": key,
                        "ResponseCacheControl": "private, no-store",
                        "ResponseContentDisposition": "attachment; filename*=UTF-8''"
                        + quote(source.filename, safe=""),
                        "ResponseContentType": "application/octet-stream",
                    },
                    ExpiresIn=ttl,
                )
                query = parse_qs(urlsplit(url).query)
                signed = datetime.strptime(
                    query["X-Amz-Date"][0], "%Y%m%dT%H%M%SZ"
                ).replace(tzinfo=UTC)
                expires_at = signed + timedelta(seconds=int(query["X-Amz-Expires"][0]))
            except (BotoCoreError, ClientError, KeyError, ValueError, OverflowError):
                raise SharedFileUnavailableError() from None
            if abs((signed - now).total_seconds()) > 2:
                raise SharedFileUnavailableError()
            if expires_at > deadline:
                ttl -= math.ceil((expires_at - deadline).total_seconds())
                continue
            if expires_at <= max(db_now, datetime.now(UTC)):
                raise SharedFileUnavailableError()
            return url, ttl, expires_at
        raise SharedFileUnavailableError()
