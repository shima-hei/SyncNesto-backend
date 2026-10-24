"""固定要件への確認表。正本の本文や実行結果は変更しない。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class RequirementVerificationPlan(Base):
    """固定対象と現在の確認表版を参照する。本文は履歴側だけに保存する。"""

    __tablename__ = "requirement_verification_plans"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "created_by",
            "request_id",
            name="uq_verification_plan_request",
        ),
        ForeignKeyConstraint(
            ["release_id", "target_node_id"],
            ["artifact_release_nodes.release_id", "artifact_release_nodes.id"],
            name="fk_verification_target",
        ),
        ForeignKeyConstraint(
            ["id", "current_version"],
            [
                "requirement_verification_plan_revisions.plan_id",
                "requirement_verification_plan_revisions.version",
            ],
            name="fk_verification_current",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        CheckConstraint(
            "current_version > 0 AND source_requirement_id > 0",
            name="ck_verification_plan_version",
        ),
        CheckConstraint(
            "subject_digest ~ '^[a-f0-9]{64}$' AND request_digest ~ '^[a-f0-9]{64}$'",
            name="ck_verification_plan_digest",
        ),
        Index(
            "ix_verification_plan_source",
            "project_id",
            "source_requirement_id",
            "created_at",
            "id",
        ),
        {"comment": db_comment("要件検証確認表", "不変な固定要件と確認表の現在版")},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    source_requirement_id: Mapped[int]
    release_id: Mapped[UUID] = mapped_column(ForeignKey("artifact_releases.id"))
    target_node_id: Mapped[UUID]
    subject_digest: Mapped[str] = mapped_column(String(64))
    current_version: Mapped[int] = mapped_column(default=1)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    request_id: Mapped[UUID]
    request_digest: Mapped[str] = mapped_column(String(64))


class RequirementVerificationRevision(Base):
    """同じ確認表の変更を追記だけで保存する。"""

    __tablename__ = "requirement_verification_plan_revisions"
    __table_args__ = (
        UniqueConstraint("plan_id", "version", name="uq_verification_revision_version"),
        UniqueConstraint(
            "plan_id", "operation_id", name="uq_verification_revision_operation"
        ),
        CheckConstraint(
            "version > 0 AND schema_version = 1",
            name="ck_verification_revision_version",
        ),
        CheckConstraint(
            "(jsonb_typeof(checklist) = 'object' "
            "AND jsonb_typeof(checklist->'rows') = 'array' "
            "AND jsonb_array_length(checklist->'rows') <= 100 "
            "AND octet_length(checklist::text) <= 262144) IS TRUE",
            name="ck_verification_revision_size",
        ),
        CheckConstraint(
            "checklist_digest ~ '^[a-f0-9]{64}$' "
            "AND operation_digest ~ '^[a-f0-9]{64}$'",
            name="ck_verification_revision_digest",
        ),
        {
            "comment": db_comment(
                "要件検証確認表の版", "保存者と確認表を保持する追記専用の履歴"
            )
        },
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    plan_id: Mapped[UUID] = mapped_column(
        ForeignKey("requirement_verification_plans.id")
    )
    version: Mapped[int]
    schema_version: Mapped[int] = mapped_column(default=1)
    checklist: Mapped[dict] = mapped_column(JSONB)
    checklist_digest: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    operation_id: Mapped[UUID]
    operation_digest: Mapped[str] = mapped_column(String(64))
