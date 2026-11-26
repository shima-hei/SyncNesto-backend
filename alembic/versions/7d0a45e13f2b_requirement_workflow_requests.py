"""Track requirement reviews and formal approvals against fixed versions."""

import sqlalchemy as sa
from alembic import op

from app.models.comments import db_comment

revision = "7d0a45e13f2b"
down_revision = "a7173cc00475"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "requirement_workflow_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False
        ),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("release_id", sa.Uuid(), nullable=False),
        sa.Column("target_node_id", sa.Uuid(), nullable=False),
        sa.Column("scope_kind", sa.String(30), nullable=False),
        sa.Column("scope_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column(
            "requester_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("request_key", sa.Uuid(), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("due_on", sa.Date(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["release_id", "target_node_id"],
            ["artifact_release_nodes.release_id", "artifact_release_nodes.id"],
            name="fk_requirement_workflow_target",
        ),
        sa.UniqueConstraint(
            "project_id",
            "requester_id",
            "request_key",
            name="uq_requirement_workflow_request_key",
        ),
        sa.CheckConstraint(
            "kind IN ('review', 'formal_approval')", name="ck_requirement_workflow_kind"
        ),
        sa.CheckConstraint(
            "scope_kind IN ('requirement_document', 'section', 'requirement') "
            "AND (kind <> 'formal_approval' OR scope_kind = 'requirement_document')",
            name="ck_requirement_workflow_scope",
        ),
        sa.CheckConstraint(
            "(kind = 'review' AND status IN "
            "('requested', 'completed', 'changes_requested', 'cancelled')) "
            "OR (kind = 'formal_approval' AND status IN "
            "('requested', 'approved', 'returned', 'cancelled'))",
            name="ck_requirement_workflow_status",
        ),
        comment=db_comment("要件定義の依頼", "レビューまたは文書全体の正式承認"),
    )
    op.create_index(
        "ix_requirement_workflow_project",
        "requirement_workflow_requests",
        ["project_id", "created_at", "id"],
    )
    op.create_index(
        "ix_requirement_workflow_document",
        "requirement_workflow_requests",
        ["project_id", "document_id"],
    )
    op.create_index(
        "ix_requirement_workflow_target",
        "requirement_workflow_requests",
        ["release_id", "target_node_id"],
    )
    op.create_index(
        "ix_requirement_workflow_requester",
        "requirement_workflow_requests",
        ["requester_id"],
    )
    op.create_index(
        "uq_requirement_workflow_pending_approval",
        "requirement_workflow_requests",
        ["project_id", "document_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'formal_approval' AND status = 'requested'"),
    )
    op.create_table(
        "requirement_workflow_participants",
        sa.Column(
            "request_id",
            sa.Uuid(),
            sa.ForeignKey("requirement_workflow_requests.id"),
            primary_key=True,
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("response_note", sa.Text(), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'changes_requested', 'approved', 'returned')",
            name="ck_requirement_workflow_participant_status",
        ),
        comment=db_comment("要件定義の依頼担当者", "担当者ごとの回答と判断日時"),
    )
    op.create_index(
        "ix_requirement_workflow_participant_user",
        "requirement_workflow_participants",
        ["user_id", "request_id"],
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM requirement_workflow_requests)")
    ):
        raise RuntimeError("Requirement workflow requests are in use")
    op.drop_table("requirement_workflow_participants")
    op.drop_table("requirement_workflow_requests")
