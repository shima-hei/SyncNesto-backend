"""Preserve formal requirement approvals and project review policy.

Revision ID: b2ec62eb2846
Revises: 91c33f2b4be0
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b2ec62eb2846"
down_revision = "91c33f2b4be0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """既存記録を残し、案件の方針と新しい承認の証跡を追加する。"""
    op.add_column(
        "projects",
        sa.Column(
            "requirement_review_required",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
    )
    op.add_column(
        "requirement_open_issues",
        sa.Column(
            "blocks_approval", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
    )
    op.add_column(
        "requirement_workflow_requests",
        sa.Column("approval_basis", postgresql.JSONB(none_as_null=True)),
    )
    op.add_column(
        "requirement_workflow_participants", sa.Column("operation_key", sa.Uuid())
    )
    op.create_check_constraint(
        "ck_requirement_workflow_approval_basis",
        "requirement_workflow_requests",
        "(kind = 'formal_approval') = (approval_basis IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_requirement_workflow_approval_decision",
        "requirement_workflow_participants",
        "(status IN ('approved', 'returned')) = (operation_key IS NOT NULL) "
        "AND (operation_key IS NULL OR responded_at IS NOT NULL) "
        "AND (status <> 'returned' OR coalesce(length(btrim(response_note)), 0) > 0)",
    )
    op.execute("""
        CREATE FUNCTION guard_requirement_formal_approval() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.kind <> 'formal_approval' AND
             (TG_OP = 'DELETE' OR NEW.kind <> 'formal_approval') THEN
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
          END IF;
          IF TG_OP = 'DELETE' OR OLD.status <> 'requested' OR
             NEW.version <> OLD.version + 1 OR
             (NEW.status = 'requested') <> (NEW.finalized_at IS NULL) OR
             to_jsonb(OLD) - ARRAY['status', 'version', 'finalized_at'] <>
             to_jsonb(NEW) - ARRAY['status', 'version', 'finalized_at'] THEN
            RAISE EXCEPTION 'formal approval evidence cannot be rewritten';
          END IF;
          RETURN NEW;
        END; $$;

        CREATE TRIGGER requirement_formal_approval_guard
          BEFORE UPDATE OR DELETE ON requirement_workflow_requests
          FOR EACH ROW EXECUTE FUNCTION guard_requirement_formal_approval();

        CREATE FUNCTION guard_requirement_formal_decision() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM requirement_workflow_requests
                         WHERE id = OLD.request_id AND kind = 'formal_approval') THEN
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            IF NEW.operation_key IS NULL THEN RETURN NEW; END IF;
          ELSIF TG_OP = 'UPDATE' AND OLD.status = 'pending'
                AND NEW.status IN ('approved', 'returned')
                AND NEW.request_id = OLD.request_id AND NEW.user_id = OLD.user_id
                AND EXISTS (SELECT 1 FROM requirement_workflow_requests
                            WHERE id = OLD.request_id AND status = 'requested') THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'formal approval decision cannot be rewritten';
        END; $$;

        CREATE TRIGGER requirement_formal_decision_guard
          BEFORE UPDATE OR DELETE ON requirement_workflow_participants
          FOR EACH ROW EXECUTE FUNCTION guard_requirement_formal_decision();
    """)


def downgrade() -> None:
    """正式承認の証跡が生成される前だけ取り消せる。"""
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM requirement_workflow_requests WHERE kind = 'formal_approval') THEN
            RAISE EXCEPTION 'formal approval evidence exists';
          END IF;
        END; $$;
        DROP TRIGGER requirement_formal_decision_guard ON requirement_workflow_participants;
        DROP FUNCTION guard_requirement_formal_decision();
        DROP TRIGGER requirement_formal_approval_guard ON requirement_workflow_requests;
        DROP FUNCTION guard_requirement_formal_approval();
    """)
    op.drop_constraint(
        "ck_requirement_workflow_approval_decision",
        "requirement_workflow_participants",
        type_="check",
    )
    op.drop_constraint(
        "ck_requirement_workflow_approval_basis",
        "requirement_workflow_requests",
        type_="check",
    )
    op.drop_column("requirement_workflow_participants", "operation_key")
    op.drop_column("requirement_workflow_requests", "approval_basis")
    op.drop_column("requirement_open_issues", "blocks_approval")
    op.drop_column("projects", "requirement_review_required")
