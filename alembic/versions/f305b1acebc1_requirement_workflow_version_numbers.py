"""Give requirement workflow versions stable document-local display numbers.

Revision ID: f305b1acebc1
Revises: b2ec62eb2846
"""

import sqlalchemy as sa

from alembic import op

revision = "f305b1acebc1"
down_revision = "b2ec62eb2846"
branch_labels = None
depends_on = None

TABLE = "requirement_workflow_requests"


def upgrade() -> None:
    """既存の承認本文や判断に触れず、表示番号だけを割り当てる。"""
    op.add_column(TABLE, sa.Column("display_number", sa.Integer()))
    op.execute("""
        ALTER TABLE requirement_workflow_requests
          DISABLE TRIGGER requirement_formal_approval_guard;
        WITH numbered AS (
          SELECT id, row_number() OVER (
            PARTITION BY project_id, document_id ORDER BY created_at, id
          ) AS number
          FROM requirement_workflow_requests
        )
        UPDATE requirement_workflow_requests AS requests
          SET display_number = numbered.number
          FROM numbered WHERE requests.id = numbered.id;
        ALTER TABLE requirement_workflow_requests
          ENABLE TRIGGER requirement_formal_approval_guard;
    """)
    op.alter_column(TABLE, "display_number", nullable=False)
    op.create_check_constraint(
        "ck_requirement_workflow_display_number", TABLE, "display_number > 0"
    )
    op.create_unique_constraint(
        "uq_requirement_workflow_display_number",
        TABLE,
        ["project_id", "document_id", "display_number"],
    )


def downgrade() -> None:
    """版番号を発行した記録がある状態では識別子を失わせない。"""
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM requirement_workflow_requests) THEN
            RAISE EXCEPTION 'requirement workflow version numbers exist';
          END IF;
        END; $$;
    """)
    op.drop_constraint("uq_requirement_workflow_display_number", TABLE, type_="unique")
    op.drop_constraint("ck_requirement_workflow_display_number", TABLE, type_="check")
    op.drop_column(TABLE, "display_number")
