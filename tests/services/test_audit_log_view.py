"""監査の補足情報から本文・秘密・任意文字列を除外する。"""

import pytest

from app.services.audit_log_view import safe_details

pytestmark = pytest.mark.no_db


def test_metadata_only_allows_known_structural_values():
    """既存のUUID対象を表示でき、型だけ似せた本文は返さない。"""
    target = "c407fa89-b2b7-4096-9a42-554d79f00eaf"
    assert safe_details(
        {
            "id": target,
            "updated_fields": ["title", "TOKEN", {"body": "SECRET"}],
            "before_role_key": "SECRET",
            "version": True,
            "count": -1,
            "purged_count": 4,
            "body": "SECRET",
            "url": "signed-SECRET",
        }
    ) == {"id": target, "updated_fields": ["title"], "purged_count": 4}
    assert safe_details({"id": "SECRET", "after_role_key": "<script>"}) == {}
