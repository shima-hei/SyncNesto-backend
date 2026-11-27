"""要件定義書の固定版に対するレビュー依頼・正式承認と担当者。"""

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class RequirementWorkflowRequest(Base):
    """確認した版・範囲・発行者を維持する依頼。"""

    __tablename__ = "requirement_workflow_requests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["release_id", "target_node_id"],
            ["artifact_release_nodes.release_id", "artifact_release_nodes.id"],
            name="fk_requirement_workflow_target",
        ),
        UniqueConstraint(
            "project_id",
            "requester_id",
            "request_key",
            name="uq_requirement_workflow_request_key",
        ),
        UniqueConstraint(
            "project_id",
            "document_id",
            "display_number",
            name="uq_requirement_workflow_display_number",
        ),
        CheckConstraint(
            "display_number > 0", name="ck_requirement_workflow_display_number"
        ),
        CheckConstraint(
            "kind IN ('review', 'formal_approval')",
            name="ck_requirement_workflow_kind",
        ),
        CheckConstraint(
            "scope_kind IN ('requirement_document', 'section', 'requirement') "
            "AND (kind <> 'formal_approval' OR scope_kind = 'requirement_document')",
            name="ck_requirement_workflow_scope",
        ),
        CheckConstraint(
            "(kind = 'review' AND status IN "
            "('requested', 'completed', 'changes_requested', 'cancelled')) "
            "OR (kind = 'formal_approval' AND status IN "
            "('requested', 'approved', 'returned', 'cancelled'))",
            name="ck_requirement_workflow_status",
        ),
        CheckConstraint(
            "(kind = 'formal_approval') = (approval_basis IS NOT NULL)",
            name="ck_requirement_workflow_approval_basis",
        ),
        Index("ix_requirement_workflow_project", "project_id", "created_at", "id"),
        Index("ix_requirement_workflow_document", "project_id", "document_id"),
        Index("ix_requirement_workflow_target", "release_id", "target_node_id"),
        Index("ix_requirement_workflow_requester", "requester_id"),
        Index(
            "uq_requirement_workflow_pending_approval",
            "project_id",
            "document_id",
            unique=True,
            postgresql_where=text("kind = 'formal_approval' AND status = 'requested'"),
        ),
        {"comment": db_comment("要件定義の依頼", "レビューまたは文書全体の正式承認")},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    document_id: Mapped[int]
    display_number: Mapped[int]
    release_id: Mapped[UUID]
    target_node_id: Mapped[UUID]
    scope_kind: Mapped[str] = mapped_column(String(30))
    scope_id: Mapped[int]
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(24), default="requested")
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    request_key: Mapped[UUID]
    request_digest: Mapped[str] = mapped_column(String(64))
    content_digest: Mapped[str] = mapped_column(String(64))
    approval_basis: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    note: Mapped[str | None] = mapped_column(Text)
    due_on: Mapped[date | None]
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RequirementWorkflowParticipant(Base):
    """依頼で指定された本人と、その本人が記録した回答。"""

    __tablename__ = "requirement_workflow_participants"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'confirmed', 'changes_requested', "
            "'approved', 'returned')",
            name="ck_requirement_workflow_participant_status",
        ),
        CheckConstraint(
            "(status IN ('approved', 'returned')) = (operation_key IS NOT NULL) "
            "AND (operation_key IS NULL OR responded_at IS NOT NULL) "
            "AND (status <> 'returned' OR "
            "coalesce(length(btrim(response_note)), 0) > 0)",
            name="ck_requirement_workflow_approval_decision",
        ),
        Index("ix_requirement_workflow_participant_user", "user_id", "request_id"),
        {"comment": db_comment("要件定義の依頼担当者", "担当者ごとの回答と判断日時")},
    )

    request_id: Mapped[UUID] = mapped_column(
        ForeignKey("requirement_workflow_requests.id"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    response_note: Mapped[str | None] = mapped_column(Text)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    operation_key: Mapped[UUID | None]


class RequirementWorkflowGuestBinding(Base):
    """担当者の依頼対象を、公開版と共有許可の世代へ固定する。"""

    __tablename__ = "requirement_workflow_guest_bindings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["request_id", "user_id"],
            [
                "requirement_workflow_participants.request_id",
                "requirement_workflow_participants.user_id",
            ],
        ),
        ForeignKeyConstraint(
            ["project_id", "publication_id"],
            ["artifact_publications.project_id", "artifact_publications.id"],
        ),
        ForeignKeyConstraint(
            ["project_id", "grant_id"],
            [
                "artifact_publication_grants.project_id",
                "artifact_publication_grants.id",
            ],
        ),
        ForeignKeyConstraint(
            ["project_id", "participation_id"],
            [
                "project_guest_participations.project_id",
                "project_guest_participations.id",
            ],
        ),
        ForeignKeyConstraint(
            ["publication_id", "target_node_id"],
            [
                "artifact_publication_nodes.publication_id",
                "artifact_publication_nodes.id",
            ],
        ),
        Index("ix_requirement_workflow_guest_inbox", "user_id", "request_id"),
        Index("ix_requirement_workflow_guest_grant", "grant_id"),
    )
    request_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int]
    publication_id: Mapped[UUID]
    target_node_id: Mapped[UUID]
    grant_id: Mapped[UUID]
    participation_id: Mapped[UUID]


class RequirementReviewConfirmation(Base):
    """担当者が、依頼時点の個別要件を確認した記録。"""

    __tablename__ = "requirement_review_confirmations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["request_id", "user_id"],
            [
                "requirement_workflow_participants.request_id",
                "requirement_workflow_participants.user_id",
            ],
            name="fk_requirement_review_confirmation_participant",
        ),
        Index("ix_requirement_review_confirmation_node", "node_id"),
        {
            "comment": db_comment(
                "個別要件のレビュー確認", "依頼と固定要件に対する担当者の確認"
            )
        },
    )

    request_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(primary_key=True)
    node_id: Mapped[UUID] = mapped_column(
        ForeignKey("artifact_release_nodes.id"), primary_key=True
    )
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RequirementReviewThread(Base):
    """固定内容の箇所を指すコメントまたは対応必須の修正依頼。"""

    __tablename__ = "requirement_review_threads"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('comment', 'correction') "
            "AND (kind <> 'comment' OR status IN ('open', 'resolved'))",
            name="ck_requirement_review_thread_kind",
        ),
        CheckConstraint(
            "status IN ('open', 'in_progress', 'awaiting_confirmation', 'resolved')",
            name="ck_requirement_review_thread_status",
        ),
        CheckConstraint(
            "(field IS NULL AND quote IS NULL AND quote_start IS NULL) OR "
            "(field IS NOT NULL AND quote IS NOT NULL "
            "AND quote_start IS NOT NULL AND quote_start >= 0)",
            name="ck_requirement_review_thread_anchor",
        ),
        Index("ix_requirement_review_thread_request", "request_id", "created_at", "id"),
        Index("ix_requirement_review_thread_node", "node_id"),
        Index("ix_requirement_review_thread_author", "author_id"),
        Index("ix_requirement_review_thread_assignee", "assignee_id"),
        {"comment": db_comment("要件定義のレビュー指摘", "固定版の箇所と対応状況")},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    request_id: Mapped[UUID] = mapped_column(
        ForeignKey("requirement_workflow_requests.id")
    )
    node_id: Mapped[UUID] = mapped_column(ForeignKey("artifact_release_nodes.id"))
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(24), default="open")
    field: Mapped[str | None] = mapped_column(String(40))
    quote: Mapped[str | None] = mapped_column(Text)
    quote_start: Mapped[int | None]
    body: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RequirementReviewThreadEvent(Base):
    """修正の返答・再指摘・解決の操作者と理由を保持する。"""

    __tablename__ = "requirement_review_thread_events"
    __table_args__ = (
        CheckConstraint(
            "action IN ('reply', 'start', 'respond', 'reopen', "
            "'resolve', 'force_resolve')",
            name="ck_requirement_review_thread_event_action",
        ),
        Index("ix_requirement_review_event_thread", "thread_id", "id"),
        Index("ix_requirement_review_event_actor", "actor_id"),
        {
            "comment": db_comment(
                "レビュー指摘の履歴", "返信・状態変更の操作者・理由・時刻"
            )
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[UUID] = mapped_column(ForeignKey("requirement_review_threads.id"))
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(20))
    body: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
