"""署名の実期限・時計差・stream保全をネットワークなしで検査する。"""

import hashlib
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import boto3
import pytest
from botocore.config import Config

from app.core.config import settings
from app.core.exceptions import SharedFileUnavailableError
from app.repositories.publication_download import DeliverySource
from app.services.publication_delivery_storage import (
    MAX_BYTES,
    PublicationDeliveryStorage,
)

pytestmark = pytest.mark.no_db


@pytest.fixture
def storage(monkeypatch):
    """client生成時にも通常Storageや外部credentialsへ接続しない。"""
    for name, value in {
        "app_env": "test",
        "shared_file_delivery_endpoint_url": "http://127.0.0.1:18334",
        "shared_file_delivery_bucket_name": "dummy-delivery",
        "shared_file_delivery_access_key_id": "dummy",
        "shared_file_delivery_secret_access_key": "dummy-secret",
    }.items():
        monkeypatch.setattr(settings, name, value)
    destination = boto3.client(
        "s3",
        endpoint_url="http://127.0.0.1:18334",
        aws_access_key_id="dummy",
        aws_secret_access_key="dummy-secret",
        region_name="ap-northeast-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    return PublicationDeliveryStorage(source=SimpleNamespace(), destination=destination)


def source(size=3):
    """実体の対応だけを用意し、元キーを配信用キーへ含めない。"""
    return DeliverySource(
        uuid4(),
        1,
        1,
        "日本語.txt",
        size,
        hashlib.sha256(b"abc").hexdigest(),
        "private/source.bin",
    )


@pytest.mark.parametrize("seconds", [1.5, 59, 60, 61])
def test_actual_signature_never_exceeds_deadline_or_sixty_seconds(storage, seconds):
    now = datetime.now(UTC)
    deadline = now + timedelta(seconds=seconds)
    item = source()
    _, ttl, expiry = storage.sign(item, storage.key(item), deadline, now)
    assert 1 <= ttl <= 60
    assert now < expiry <= min(deadline, now + timedelta(seconds=60))


@pytest.mark.parametrize("seconds", [0, 0.5])
def test_less_than_one_second_is_not_signed(storage, seconds):
    now = datetime.now(UTC)
    with pytest.raises(SharedFileUnavailableError):
        storage.sign(
            source(), "delivery-v1/dummy", now + timedelta(seconds=seconds), now
        )


def test_database_clock_drift_fails_before_signing(storage):
    now = datetime.now(UTC)
    with pytest.raises(SharedFileUnavailableError):
        storage.sign(
            source(),
            "delivery-v1/dummy",
            now + timedelta(minutes=1),
            now - timedelta(seconds=3),
        )


@pytest.mark.parametrize("content", [b"abd", b"abcd", b"ab"])
def test_mismatched_source_always_closes_body(content):
    body = BytesIO(content)
    client = Mock()
    client.get_object.return_value = {"Body": body, "ContentLength": 3}
    with pytest.raises(SharedFileUnavailableError):
        PublicationDeliveryStorage._read(client, "dummy", "dummy", source())
    assert body.closed


@pytest.mark.parametrize("size", [0, MAX_BYTES + 1])
def test_invalid_size_does_not_access_storage(storage, size):
    storage.destination = Mock()
    with pytest.raises(SharedFileUnavailableError):
        storage.ensure_copy(source(size))
    storage.destination.get_object.assert_not_called()
