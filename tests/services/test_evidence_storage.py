"""既存S3互換Storageでの追記・完全性と、旧版の読取互換を検証する。"""

import hashlib
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import settings
from app.core.exceptions import SharedFileUnavailableError
from app.services.verification_evidence_storage import EvidenceStorage

pytestmark = pytest.mark.no_db
KEY = "verification-evidence-v2/reserved-unique-key"
SHA = hashlib.sha256(b"proof").hexdigest()
FILE = dict(
    kind="document_attachment",
    filename="proof.txt",
    content_type="text/plain",
    byte_size=5,
    storage_key="private/source.txt",
)


@pytest.fixture
def preserved(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    objects, bodies = {}, []
    client = Mock()

    def get_object(**values):
        key = values["Key"]
        if key not in objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        body = BytesIO(objects[key])
        bodies.append(body)
        return {"Body": body, "ContentLength": len(objects[key])}

    def put_object(**values):
        assert "VersionId" not in values and "IfNoneMatch" not in values
        assert values["Bucket"] == "existing-private"
        objects[values["Key"]] = values["Body"]
        return {}

    client.get_object.side_effect = get_object
    client.put_object.side_effect = put_object
    source = SimpleNamespace(
        bucket_name="existing-private",
        s3_client=client,
        read_uploaded_object=Mock(return_value=b"proof"),
    )
    return EvidenceStorage(source=source), objects, bodies


def test_append_only_copy_and_same_length_tamper_are_rejected(preserved):
    storage, objects, bodies = preserved
    result = storage.copy(FILE, KEY)
    assert result == dict(destination_version="sha256:" + SHA, sha256=SHA, byte_size=5)
    with pytest.raises(SharedFileUnavailableError):
        storage.copy(FILE, KEY)
    assert storage.destination.put_object.call_count == 1
    objects[KEY] = b"other"
    with pytest.raises(SharedFileUnavailableError):
        storage.head(KEY, result["destination_version"], SHA, 5)
    assert all(body.closed for body in bodies)


def test_sign_uses_existing_client_without_native_version(preserved):
    storage, _, _ = preserved
    signer = boto3.client(
        "s3",
        endpoint_url="https://storage.example.test/s3",
        region_name="ap-northeast-1",
        aws_access_key_id="dummy",
        aws_secret_access_key="dummy-secret",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    storage.destination.generate_presigned_url.side_effect = (
        signer.generate_presigned_url
    )
    now = datetime.now(UTC)
    signed = storage.sign(
        KEY, "sha256:" + SHA, "証拠.txt", now + timedelta(seconds=30), now
    )
    assert 0 < signed["expires_in"] <= 30
    parameters = storage.destination.generate_presigned_url.call_args.kwargs["Params"]
    assert parameters["Key"] == KEY and "VersionId" not in parameters
    assert parameters["ResponseCacheControl"] == "private, no-store"
    assert "private/source" not in signed["url"]


def test_existing_native_version_reader_is_preserved(monkeypatch):
    for name, value in dict(
        app_env="test",
        shared_file_delivery_endpoint_url="http://127.0.0.1:18334",
        verification_evidence_bucket_name="old-private-evidence",
        shared_file_delivery_bucket_name="old-delivery",
        shared_file_delivery_access_key_id="dummy",
        shared_file_delivery_secret_access_key="dummy-secret",
    ).items():
        monkeypatch.setattr(settings, name, value)
    destination = Mock()
    body = BytesIO(b"proof")
    destination.get_object.return_value = dict(
        Body=body, ContentLength=5, VersionId="old-v1"
    )
    storage = EvidenceStorage(
        source=SimpleNamespace(bucket_name="existing"), destination=destination
    )
    storage.verify("verification-evidence-v1/old", "old-v1", SHA, 5)
    destination.get_object.assert_called_once_with(
        Bucket="old-private-evidence",
        Key="verification-evidence-v1/old",
        VersionId="old-v1",
    )
    assert body.closed
