"""用途・Project・集合digestを束縛する承認履歴cursor。"""

import base64
import hashlib
import hmac
import json

from app.core.config import settings
from app.core.exceptions import FixedApprovalScopeConflictError
from app.services.artifact_release_snapshot import canonical_bytes


def encode_cursor(project_id, purpose, scope_digest, offset):
    """再利用先の集合が異なるcursorを作れないよう署名する。"""
    data = canonical_bytes(
        ["fixed-approval-v1", project_id, purpose, scope_digest, offset]
    )
    signature = hmac.digest(settings.secret_key.encode(), data, hashlib.sha256)
    return base64.urlsafe_b64encode(data + signature).decode().rstrip("=")


def decode_cursor(value, project_id, purpose, scope_digest):
    """権限確認とは別に、改変とページ間の集合変更を拒否する。"""
    if value is None:
        return 0
    try:
        if len(value) > 1000:
            raise ValueError()
        raw = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
        data, signature = raw[:-32], raw[-32:]
        if not hmac.compare_digest(
            signature, hmac.digest(settings.secret_key.encode(), data, hashlib.sha256)
        ):
            raise ValueError()
        version, project, tag, digest, offset = json.loads(data)
        if (
            version != "fixed-approval-v1"
            or project != project_id
            or tag != purpose
            or digest != scope_digest
        ):
            raise ValueError()
        if type(offset) is not int or offset < 0:
            raise ValueError()
        return offset
    except (ValueError, TypeError, UnicodeError):
        raise FixedApprovalScopeConflictError() from None
