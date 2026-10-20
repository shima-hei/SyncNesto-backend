"""Connect fixed approvals to explicit guest publication grants.

Revision ID: a3b7c0e5d981
Revises: f2a6b9d4c870
"""

import re
from pathlib import Path

from alembic import op
import sqlalchemy as sa

revision = "a3b7c0e5d981"
down_revision = "f2a6b9d4c870"
branch_labels = None
depends_on = None

FRAGMENTS = {
    "guard_fixed_approval_request": """
          -- guest-fixed-approval-v1
          IF TG_OP='INSERT' THEN
            IF (NEW.target_kind IN ('specification','test_design') AND NEW.policy_version<>'fixed-approval-guest-v1')
              OR (NEW.policy_version='fixed-approval-guest-v1' AND NEW.legacy_import_id IS NOT NULL) THEN
              RAISE EXCEPTION 'invalid external approval policy' USING ERRCODE='55000';
            END IF;
          ELSIF TG_OP='UPDATE' AND OLD.policy_version='fixed-approval-guest-v1' THEN
            PERFORM publication_assert_lock(NEW.project_id);
          END IF;
""",
    "guard_fixed_approval_basis": """
          -- guest-fixed-approval-v1
          IF NEW.basis_kind='agreed' AND EXISTS(
            SELECT FROM fixed_approval_requests WHERE id=NEW.approval_id AND policy_version='fixed-approval-guest-v1'
          ) AND NOT EXISTS(
            SELECT FROM fixed_approval_guest_bindings WHERE approval_id=NEW.agreed_approval_id
          ) THEN RAISE EXCEPTION 'external agreement required' USING ERRCODE='55000'; END IF;
""",
    "check_fixed_approval_complete": """
          -- guest-fixed-approval-v1
          PERFORM guest_approval_check_complete(NEW.id);
""",
    "guard_fixed_approval_event": """
          -- guest-fixed-approval-v1
          IF NEW.seq=2 AND NEW.action<>'cancelled' AND EXISTS(
            SELECT FROM fixed_approval_requests WHERE id=NEW.approval_id AND policy_version='fixed-approval-guest-v1'
          ) THEN
            IF NOT guest_approval_has_access(NEW.approval_id,NEW.actor_id) OR NOT EXISTS(
              SELECT FROM fixed_approval_guest_bindings b WHERE b.approval_id=NEW.approval_id
                AND b.public_scope_digest=NEW.observation->>'public_scope_digest'
            ) THEN RAISE EXCEPTION 'external decision scope unavailable' USING ERRCODE='55000'; END IF;
          END IF;
""",
}


def _splice(reverse: bool = False) -> None:
    """既存4関数の本体を保ち、新しい外部行だけへの検査を挿入する。"""
    connection = op.get_bind()
    for name, fragment in FRAGMENTS.items():
        definition = connection.execute(sa.text("SELECT pg_get_functiondef(CAST(:name AS regproc))"), {"name": name}).scalar_one()
        if reverse:
            if definition.count(fragment) != 1:
                raise RuntimeError(f"Unexpected guest approval guard: {name}")
            definition = definition.replace(fragment, "", 1)
        else:
            if "guest-fixed-approval-v1" in definition:
                raise RuntimeError(f"Guest approval guard already present: {name}")
            definition, count = re.subn(r"\bBEGIN\b", lambda m: m[0] + fragment, definition, count=1)
            if count != 1:
                raise RuntimeError(f"Unknown approval guard: {name}")
        op.execute(definition)


def upgrade() -> None:
    """既存依頼を更新せず、対応表と外部限定の整合検査を追加する。"""
    op.execute(Path(__file__).with_suffix(".sql").read_text())
    _splice()


def downgrade() -> None:
    """新しい判断証跡があれば破棄せず停止する。"""
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT EXISTS(SELECT FROM fixed_approval_guest_bindings) OR EXISTS(SELECT FROM fixed_approval_requests WHERE target_kind IN ('specification','test_design') OR policy_version='fixed-approval-guest-v1')")).scalar_one():
        raise RuntimeError("Guest approval history must be retained; disable new operations instead")
    _splice(reverse=True)
    op.execute("DROP TABLE fixed_approval_guest_bindings")
    for name in ("guest_approval_binding_guard", "guest_approval_check_complete", "guest_approval_has_access"):
        signatures = {"guest_approval_binding_guard": "()", "guest_approval_check_complete": "(uuid)", "guest_approval_has_access": "(uuid,integer)"}
        op.execute(f"DROP FUNCTION {name}{signatures[name]}")
    op.execute("ALTER TABLE fixed_approval_requests DROP CONSTRAINT ck_fixed_approval_kind, ADD CONSTRAINT ck_fixed_approval_kind CHECK(target_kind IN ('requirement','requirement_document'))")
