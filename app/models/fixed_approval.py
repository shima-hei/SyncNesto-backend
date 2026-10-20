"""元の可変データと旧承認から独立した、版固定の依頼と判断証跡。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class LegacyApprovalImport(Base):
    """旧承認と実判断者の記録を、推測した対象版で補完せず保存する。"""

    __tablename__ = "legacy_approval_imports"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "legacy_approval_id",
            "source_digest",
            "mode",
            name="uq_legacy_approval_snapshot",
        ),
        UniqueConstraint(
            "project_id",
            "created_by",
            "request_id",
            name="uq_legacy_approval_operation",
        ),
        Index(
            "uq_legacy_approval_switch",
            "project_id",
            "legacy_approval_id",
            unique=True,
            postgresql_where=text("mode='switch'"),
        ),
        CheckConstraint("mode IN ('archive','switch')", name="ck_legacy_approval_mode"),
        {"comment": db_comment("従来承認の取込", "元の判断と証明できない範囲を保持")},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    legacy_approval_id: Mapped[int]
    source: Mapped[dict] = mapped_column(JSONB)
    logs: Mapped[list] = mapped_column(JSONB)
    source_digest: Mapped[str] = mapped_column(String(64))
    evidence: Mapped[dict] = mapped_column(JSONB)
    mode: Mapped[str] = mapped_column(String(20))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    request_id: Mapped[UUID]
    request_digest: Mapped[str] = mapped_column(String(64))


class FixedApprovalRequest(Base):
    """対象と指定先を不変にし、状態だけを一度確定できる依頼。"""

    __tablename__ = "fixed_approval_requests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["release_id", "target_node_id"],
            ["artifact_release_nodes.release_id", "artifact_release_nodes.id"],
            name="fk_fixed_approval_target",
        ),
        UniqueConstraint(
            "project_id",
            "requester_id",
            "request_id",
            name="uq_fixed_approval_operation",
        ),
        CheckConstraint(
            "requester_id <> approver_id", name="ck_fixed_approval_not_self"
        ),
        CheckConstraint(
            "target_kind IN ('requirement','requirement_document',"
            "'specification','test_design')",
            name="ck_fixed_approval_kind",
        ),
        CheckConstraint(
            "status IN ('requested','approved','approved_with_exceptions',"
            "'rejected','cancelled')",
            name="ck_fixed_approval_status",
        ),
        CheckConstraint("version IN (1,2)", name="ck_fixed_approval_version"),
        Index(
            "uq_fixed_approval_pending",
            "project_id",
            "release_id",
            "target_node_id",
            "preservation_id",
            unique=True,
            postgresql_where=text("status='requested'"),
            postgresql_nulls_not_distinct=True,
        ),
        {
            "comment": db_comment(
                "固定版への承認依頼", "個別合意と例外付き正式承認を区別"
            )
        },
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    release_id: Mapped[UUID] = mapped_column(
        ForeignKey("artifact_releases.id"), index=True
    )
    target_node_id: Mapped[UUID]
    target_kind: Mapped[str] = mapped_column(String(30))
    source_id: Mapped[int]
    preservation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("artifact_preservations.id")
    )
    scope_manifest: Mapped[dict] = mapped_column(JSONB)
    scope_digest: Mapped[str] = mapped_column(String(64))
    policy_version: Mapped[str] = mapped_column(String(30), default="fixed-approval-v1")
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    approver_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    request_note: Mapped[str | None]
    status: Mapped[str] = mapped_column(String(30), default="requested")
    version: Mapped[int] = mapped_column(default=1)
    sealed: Mapped[bool] = mapped_column(default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_id: Mapped[UUID]
    request_digest: Mapped[str] = mapped_column(String(64))
    legacy_import_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("legacy_approval_imports.id")
    )


class FixedApprovalEvent(Base):
    """実操作者を持ち、保存後に変更できない申請・終判断イベント。"""

    __tablename__ = "fixed_approval_events"
    __table_args__ = (
        UniqueConstraint("approval_id", "seq", name="uq_fixed_approval_event_seq"),
        UniqueConstraint(
            "approval_id", "operation_id", name="uq_fixed_approval_event_operation"
        ),
        CheckConstraint("seq IN (1,2)", name="ck_fixed_approval_event_seq"),
        {"comment": db_comment("固定版承認イベント", "業務判断を監査操作ログと区別")},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    approval_id: Mapped[UUID] = mapped_column(
        ForeignKey("fixed_approval_requests.id"), index=True
    )
    seq: Mapped[int]
    operation_id: Mapped[UUID]
    operation_digest: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str | None]
    scope_digest: Mapped[str] = mapped_column(String(64))
    observation: Mapped[dict] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FixedApprovalRequirementBasis(Base):
    """文書に含まれる全要件の合意根拠、または例外理由を固定する。"""

    __tablename__ = "fixed_approval_requirement_bases"
    __table_args__ = (
        UniqueConstraint(
            "approval_id", "target_node_id", name="uq_fixed_approval_basis"
        ),
        ForeignKeyConstraint(
            ["release_id", "target_node_id"],
            ["artifact_release_nodes.release_id", "artifact_release_nodes.id"],
            name="fk_fixed_approval_basis_target",
        ),
        CheckConstraint(
            "(basis_kind='agreed' AND agreed_approval_id IS NOT NULL "
            "AND agreed_event_id IS NOT NULL AND reason IS NULL) OR "
            "(basis_kind='exception' AND agreed_approval_id IS NULL "
            "AND agreed_event_id IS NULL AND reason IS NOT NULL "
            "AND length(trim(reason))>0)",
            name="ck_fixed_approval_basis_kind",
        ),
        {
            "comment": db_comment(
                "文書承認の要件根拠", "未合意の例外を隠さず理由とともに固定"
            )
        },
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    approval_id: Mapped[UUID] = mapped_column(
        ForeignKey("fixed_approval_requests.id"), index=True
    )
    release_id: Mapped[UUID]
    target_node_id: Mapped[UUID]
    source_id: Mapped[int]
    subject_digest: Mapped[str] = mapped_column(String(64))
    basis_kind: Mapped[str] = mapped_column(String(20))
    state_at_request: Mapped[str] = mapped_column(String(30))
    agreed_approval_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("fixed_approval_requests.id")
    )
    agreed_event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("fixed_approval_events.id")
    )
    reason: Mapped[str | None]
