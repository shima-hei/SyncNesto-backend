"""Persist individual review confirmations and correction discussions."""

import sqlalchemy as sa
from alembic import op

from app.models.comments import db_comment

revision = "91c33f2b4be0"
down_revision = "7d0a45e13f2b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "requirement_review_confirmations",
        sa.Column("request_id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column(
            "node_id",
            sa.Uuid(),
            sa.ForeignKey("artifact_release_nodes.id"),
            primary_key=True,
        ),
        sa.Column(
            "confirmed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["request_id", "user_id"],
            [
                "requirement_workflow_participants.request_id",
                "requirement_workflow_participants.user_id",
            ],
            name="fk_requirement_review_confirmation_participant",
        ),
        comment=db_comment(
            "個別要件のレビュー確認", "依頼と固定要件に対する担当者の確認"
        ),
    )
    op.create_index(
        "ix_requirement_review_confirmation_node",
        "requirement_review_confirmations",
        ["node_id"],
    )
    op.create_table(
        "requirement_review_threads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "request_id",
            sa.Uuid(),
            sa.ForeignKey("requirement_workflow_requests.id"),
            nullable=False,
        ),
        sa.Column(
            "node_id",
            sa.Uuid(),
            sa.ForeignKey("artifact_release_nodes.id"),
            nullable=False,
        ),
        sa.Column("author_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("assignee_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("field", sa.String(40)),
        sa.Column("quote", sa.Text()),
        sa.Column("quote_start", sa.Integer()),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "kind IN ('comment', 'correction') "
            "AND (kind <> 'comment' OR status IN ('open', 'resolved'))",
            name="ck_requirement_review_thread_kind",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'in_progress', 'awaiting_confirmation', 'resolved')",
            name="ck_requirement_review_thread_status",
        ),
        sa.CheckConstraint(
            "(field IS NULL AND quote IS NULL AND quote_start IS NULL) OR "
            "(field IS NOT NULL AND quote IS NOT NULL AND quote_start IS NOT NULL AND quote_start >= 0)",
            name="ck_requirement_review_thread_anchor",
        ),
        comment=db_comment("要件定義のレビュー指摘", "固定版の箇所と対応状況"),
    )
    for name, columns in (
        ("request", ["request_id", "created_at", "id"]),
        ("node", ["node_id"]),
        ("author", ["author_id"]),
        ("assignee", ["assignee_id"]),
    ):
        op.create_index(
            f"ix_requirement_review_thread_{name}",
            "requirement_review_threads",
            columns,
        )
    op.create_table(
        "requirement_review_thread_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "thread_id",
            sa.Uuid(),
            sa.ForeignKey("requirement_review_threads.id"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("body", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "action IN ('reply', 'start', 'respond', 'reopen', 'resolve', 'force_resolve')",
            name="ck_requirement_review_thread_event_action",
        ),
        comment=db_comment("レビュー指摘の履歴", "返信・状態変更の操作者・理由・時刻"),
    )
    op.create_index(
        "ix_requirement_review_event_thread",
        "requirement_review_thread_events",
        ["thread_id", "id"],
    )
    op.create_index(
        "ix_requirement_review_event_actor",
        "requirement_review_thread_events",
        ["actor_id"],
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("""
        SELECT EXISTS (SELECT 1 FROM requirement_review_confirmations)
            OR EXISTS (SELECT 1 FROM requirement_review_threads)
    """)
    ):
        raise RuntimeError("Requirement review results are in use")
    op.drop_table("requirement_review_thread_events")
    op.drop_table("requirement_review_threads")
    op.drop_table("requirement_review_confirmations")
