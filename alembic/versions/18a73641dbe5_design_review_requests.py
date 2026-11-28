"""複数設計の固定版をまとめたレビュー・承認依頼を保存する。"""

import sqlalchemy as sa

from alembic import op

revision = "18a73641dbe5"
down_revision = "714f7bb23d31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """既存の設計・承認記録を保持し、依頼用の表を追加する。"""
    op.create_table(
        "design_review_requests",
        sa.Column("project_id", sa.Integer(), primary_key=True),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column(
            "requester_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "reviewer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("decision_note", sa.Text()),
        sa.Column("decided_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "kind IN ('review', 'approval')", name="ck_design_review_kind"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'reviewed', 'approved', 'changes_requested', 'cancelled')",
            name="ck_design_review_status",
        ),
    )
    for actor in ("reviewer", "requester"):
        op.create_index(
            f"ix_design_review_{actor}",
            "design_review_requests",
            ["project_id", f"{actor}_id", "status"],
        )
    op.create_table(
        "design_review_request_targets",
        sa.Column("project_id", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.Uuid(), primary_key=True),
        sa.Column("specification_id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "request_id"],
            ["design_review_requests.project_id", "design_review_requests.id"],
            name="fk_design_review_target_request",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("version >= 1", name="ck_design_review_target_version"),
    )
    op.create_index(
        "ix_design_review_target_specification",
        "design_review_request_targets",
        ["project_id", "specification_id"],
    )


def downgrade() -> None:
    """設計書と既存版を保ち、追加した依頼の表を取り除く。"""
    op.drop_table("design_review_request_targets")
    op.drop_table("design_review_requests")
