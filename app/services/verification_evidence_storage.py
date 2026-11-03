"""既存Storageの予約済みキーへ証拠を保全し、旧LOCAL版の読取を保持する。"""

import hashlib
import io
import math
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, quote, urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings
from app.core.exceptions import BadRequestError, SharedFileUnavailableError
from app.services.document_attachment import DocumentAttachmentService
from app.services.storage import StorageService
from app.services.test_evidence import TestEvidenceService


class VersionedEvidenceStorage:
    """毎回の業務認可はService側。任意bucket/keyを外部入力から受けない。"""

    def __init__(self, source=None, destination=None):
        endpoint = urlsplit(settings.shared_file_delivery_endpoint_url)
        bucket = settings.verification_evidence_bucket_name
        if (
            settings.is_public_environment
            or endpoint.scheme != "http"
            or endpoint.hostname not in {"localhost", "127.0.0.1"}
            or endpoint.port in {None, 4566}
            or endpoint.path not in {"", "/"}
            or endpoint.query
            or endpoint.fragment
            or endpoint.username
            or endpoint.password
            or not bucket
            or bucket
            in {settings.aws_s3_bucket_name, settings.shared_file_delivery_bucket_name}
            or not settings.shared_file_delivery_access_key_id
            or not settings.shared_file_delivery_secret_access_key
        ):
            raise SharedFileUnavailableError()
        self.bucket = bucket
        self.source = source or StorageService(request_timeout_seconds=5)
        self.destination = destination or boto3.client(
            "s3",
            endpoint_url=settings.shared_file_delivery_endpoint_url,
            region_name=settings.aws_region,
            aws_access_key_id=settings.shared_file_delivery_access_key_id,
            aws_secret_access_key=settings.shared_file_delivery_secret_access_key,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=2,
                read_timeout=5,
                retries={"total_max_attempts": 1},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    @staticmethod
    def _deadline(deadline):
        if time.monotonic() >= deadline:
            raise SharedFileUnavailableError()

    def copy(self, file, key):
        """一度だけPUT。VersionId欠落・不一致は採用しない。"""
        deadline = time.monotonic() + 25
        try:
            if (
                self.destination.get_bucket_versioning(Bucket=self.bucket).get("Status")
                != "Enabled"
            ):
                raise SharedFileUnavailableError()
            result = self.source.s3_client.get_object(
                Bucket=self.source.bucket_name, Key=file["storage_key"]
            )
            body = result["Body"]
            try:
                if (
                    result["ContentLength"] != file["byte_size"]
                    or result.get("ContentType") != file["content_type"]
                ):
                    raise SharedFileUnavailableError()
                with io.BytesIO() as buffer:
                    total = 0
                    while chunk := body.read(65536):
                        total += len(chunk)
                        if total > file["byte_size"]:
                            raise SharedFileUnavailableError()
                        self._deadline(deadline)
                        buffer.write(chunk)
                    content = buffer.getvalue()
            finally:
                body.close()
            if len(content) != file["byte_size"]:
                raise SharedFileUnavailableError()
            if file["kind"] == "test_evidence":
                TestEvidenceService(storage=self.source)._validate_file(
                    file["filename"], file["content_type"], content
                )
            else:
                DocumentAttachmentService(storage=self.source).validate_content(
                    file["filename"], file["content_type"], content
                )
            sha = hashlib.sha256(content).hexdigest()
            self._deadline(deadline)
            result = self.destination.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content,
                ContentType="application/octet-stream",
                CacheControl="private, no-store",
                Metadata={"sha256": sha},
                IfNoneMatch="*",
            )
            version = result.get("VersionId")
            if not version or version == "null":
                raise SharedFileUnavailableError()
            del content
            self._deadline(deadline)
            self.verify(key, version, sha, file["byte_size"], deadline=deadline)
            return dict(
                destination_version=version, sha256=sha, byte_size=file["byte_size"]
            )
        except (BotoCoreError, ClientError, OSError, BadRequestError):
            raise SharedFileUnavailableError() from None

    def verify(self, key, version, sha, size, *, deadline=None):
        try:
            result = self.destination.get_object(
                Bucket=self.bucket, Key=key, VersionId=version
            )
            body = result["Body"]
            total, hasher = 0, hashlib.sha256()
            try:
                if (
                    result.get("VersionId") != version
                    or result["ContentLength"] != size
                ):
                    raise SharedFileUnavailableError()
                while chunk := body.read(65536):
                    total += len(chunk)
                    if total > size:
                        raise SharedFileUnavailableError()
                    hasher.update(chunk)
                    if deadline:
                        self._deadline(deadline)
            finally:
                body.close()
            if total != size or hasher.hexdigest() != sha:
                raise SharedFileUnavailableError()
        except (BotoCoreError, ClientError, OSError):
            raise SharedFileUnavailableError() from None

    def cleanup(self, key):
        """試行キーの全版を件数制限内で回収。後続再走査を妨げない。"""
        result = self.destination.list_object_versions(
            Bucket=self.bucket, Prefix=key, MaxKeys=100
        )
        versions = [
            v
            for kind in ("Versions", "DeleteMarkers")
            for v in result.get(kind, [])
            if v["Key"] == key
        ]
        for version in versions:
            self.destination.delete_object(
                Bucket=self.bucket, Key=key, VersionId=version["VersionId"]
            )
        return not result.get("IsTruncated", False)

    def sign(self, key, version, filename, deadline, db_now):
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
            url = self.destination.generate_presigned_url(
                "get_object",
                Params=self.signed_parameters(key, version, filename),
                ExpiresIn=ttl,
            )
            query = parse_qs(urlsplit(url).query)
            signed = datetime.strptime(
                query["X-Amz-Date"][0], "%Y%m%dT%H%M%SZ"
            ).replace(tzinfo=UTC)
            expiry = signed + timedelta(seconds=int(query["X-Amz-Expires"][0]))
            if abs((signed - now).total_seconds()) > 2:
                raise SharedFileUnavailableError()
            if expiry > deadline:
                ttl -= math.ceil((expiry - deadline).total_seconds())
                continue
            if expiry <= max(db_now, datetime.now(UTC)):
                raise SharedFileUnavailableError()
            return dict(url=url, expires_in=ttl, expires_at=expiry)
        raise SharedFileUnavailableError()

    def signed_parameters(self, key, version, filename):
        return {
            "Bucket": self.bucket,
            "Key": key,
            "VersionId": version,
            "ResponseCacheControl": "private, no-store",
            "ResponseContentType": "application/octet-stream",
            "ResponseContentDisposition": "attachment; filename*=UTF-8''"
            + quote(filename, safe=""),
        }


class EvidenceStorage(VersionedEvidenceStorage):
    """新しい証拠は既存Storageへ追記し、旧VersionIdの読取も維持する。"""

    def __init__(self, source=None, destination=None):
        self.source = source or StorageService(request_timeout_seconds=5)
        self.bucket = self.source.bucket_name
        self.destination = destination or self.source.s3_client
        self._destination_override = destination

    def legacy(self):
        return VersionedEvidenceStorage(
            source=self.source, destination=self._destination_override
        )

    @staticmethod
    def require_key(key):
        if not key.startswith("verification-evidence-v2/"):
            raise SharedFileUnavailableError()

    def copy(self, file, key):
        """サーバー予約の一意キーだけへ保存し、既存キーを上書きしない。"""
        self.require_key(key)
        if not 0 < file["byte_size"] <= 20 * 1024 * 1024:
            raise SharedFileUnavailableError()
        deadline = time.monotonic() + 25
        try:
            try:
                existing = self.destination.get_object(Bucket=self.bucket, Key=key)
            except ClientError as error:
                if error.response.get("Error", {}).get("Code") not in {
                    "NoSuchKey",
                    "404",
                    "NotFound",
                }:
                    raise
            else:
                existing["Body"].close()
                raise SharedFileUnavailableError()
            content = self.source.read_uploaded_object(
                key=file["storage_key"],
                content_type=file["content_type"],
                byte_size=file["byte_size"],
            )
            if file["kind"] == "test_evidence":
                TestEvidenceService(storage=self.source)._validate_file(
                    file["filename"], file["content_type"], content
                )
            else:
                DocumentAttachmentService(storage=self.source).validate_content(
                    file["filename"], file["content_type"], content
                )
            sha = hashlib.sha256(content).hexdigest()
            self._deadline(deadline)
            self.destination.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content,
                ContentType="application/octet-stream",
                CacheControl="private, no-store",
                Metadata={"sha256": sha},
            )
            del content
            version = "sha256:" + sha
            self.verify(key, version, sha, file["byte_size"], deadline=deadline)
            return dict(
                destination_version=version, sha256=sha, byte_size=file["byte_size"]
            )
        except (BotoCoreError, ClientError, OSError, BadRequestError):
            raise SharedFileUnavailableError() from None

    def verify(self, key, version, sha, size, *, deadline=None):
        deadline = deadline if deadline is not None else time.monotonic() + 25
        if not version.startswith("sha256:"):
            return self.legacy().verify(key, version, sha, size, deadline=deadline)
        self.require_key(key)
        if version != "sha256:" + sha:
            raise SharedFileUnavailableError()
        try:
            self._deadline(deadline)
            result = self.destination.get_object(Bucket=self.bucket, Key=key)
            body = result["Body"]
            total, hasher = 0, hashlib.sha256()
            try:
                if result["ContentLength"] != size:
                    raise SharedFileUnavailableError()
                while chunk := body.read(65536):
                    total += len(chunk)
                    if total > size:
                        raise SharedFileUnavailableError()
                    hasher.update(chunk)
                    if deadline:
                        self._deadline(deadline)
            finally:
                body.close()
            if total != size or hasher.hexdigest() != sha:
                raise SharedFileUnavailableError()
        except (BotoCoreError, ClientError, OSError):
            raise SharedFileUnavailableError() from None

    def cleanup(self, key):
        if not key.startswith("verification-evidence-v2/"):
            return self.legacy().cleanup(key)
        self.destination.delete_object(Bucket=self.bucket, Key=key)
        return True

    def head(self, key, version, sha, size, *, deadline=None):
        """新方式は実体のハッシュ、旧方式は指定版の存在を確認する。"""
        if not version.startswith("sha256:"):
            legacy = self.legacy()
            return legacy.destination.head_object(
                Bucket=legacy.bucket, Key=key, VersionId=version
            )
        self.verify(key, version, sha, size, deadline=deadline)
        return {"VersionId": version, "ContentLength": size}

    def sign(self, key, version, filename, deadline, db_now):
        if not version.startswith("sha256:"):
            return self.legacy().sign(key, version, filename, deadline, db_now)
        self.require_key(key)
        return super().sign(key, version, filename, deadline, db_now)

    def signed_parameters(self, key, version, filename):
        parameters = super().signed_parameters(key, version, filename)
        parameters.pop("VersionId")
        return parameters
