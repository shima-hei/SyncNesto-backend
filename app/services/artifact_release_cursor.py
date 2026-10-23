"""固定版とProjectへ結び付けた、改変検出用の読取cursor。"""

import base64
import hashlib
import hmac
import json
from uuid import UUID

from app.core import error_messages
from app.core.config import settings
from app.core.exceptions import BadRequestError


def encode_cursor(project_id: int, release_id: UUID, after: int) -> str:
    """認可の代わりにはならない署名付きcursorを返す。"""
    data = json.dumps(
        ["artifact-nodes-v1", project_id, str(release_id), after]
    ).encode()
    signature = hmac.digest(settings.secret_key.encode(), data, hashlib.sha256)
    return base64.urlsafe_b64encode(data + signature).decode().rstrip("=")


def decode_cursor(value: str, project_id: int, release_id: UUID) -> int:
    """別の固定版・不正値・改変されたcursorを拒否する。"""
    try:
        if len(value) > 1000:
            raise ValueError()
        raw = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
        data, signature = raw[:-32], raw[-32:]
        expected = hmac.digest(settings.secret_key.encode(), data, hashlib.sha256)
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        prefix, project, release, after = json.loads(data)
        if (
            prefix != "artifact-nodes-v1"
            or project != project_id
            or release != str(release_id)
            or type(after) is not int
            or after < 0
        ):
            raise ValueError()
        return after
    except (ValueError, TypeError, UnicodeError):
        raise BadRequestError(error_messages.ARTIFACT_CURSOR_INVALID) from None
