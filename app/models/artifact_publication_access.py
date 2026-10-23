"""固定公開版の本人別参加資格・許可・追記の業務履歴。"""

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


class ProjectGuestParticipation(Base):
    """通常の組織・Project所属を増やさず、本人の参加世代を記録する。"""

    __tablename__ = "project_guest_participations"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_guest_participation_project"),
        UniqueConstraint(
            "project_id", "user_id", "generation", name="uq_guest_generation"
        ),
        Index(
            "uq_guest_active_generation",
            "project_id",
            "user_id",
            unique=True,
            postgresql_where=text("status='active'"),
        ),
        CheckConstraint(
            "status IN ('active','revoked') AND generation>0 AND version>0",
            name="ck_guest_state",
        ),
        {"comment": db_comment("外部参加資格", "Project限定・期限付きの本人参加世代")},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    generation: Mapped[int]
    display_label: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="active")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    revocation_reason: Mapped[str | None] = mapped_column(String(1000))


class ArtifactPublicationGrant(Base):
    """同じProjectの公開版と参加世代を結ぶ、復活させない許可。"""

    __tablename__ = "artifact_publication_grants"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_publication_grant_project"),
        ForeignKeyConstraint(
            ["project_id", "publication_id"],
            ["artifact_publications.project_id", "artifact_publications.id"],
            name="fk_grant_publication_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "participation_id"],
            [
                "project_guest_participations.project_id",
                "project_guest_participations.id",
            ],
            name="fk_grant_participation_project",
        ),
        Index(
            "uq_publication_active_grant",
            "publication_id",
            "participation_id",
            unique=True,
            postgresql_where=text("status='active'"),
        ),
        CheckConstraint(
            "status IN ('active','revoked') AND version>0",
            name="ck_publication_grant_state",
        ),
        {"comment": db_comment("公開許可", "固定公開版と本人参加世代への期限付き許可")},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(index=True)
    publication_id: Mapped[UUID] = mapped_column(index=True)
    participation_id: Mapped[UUID] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    revocation_reason: Mapped[str | None] = mapped_column(String(1000))


class ArtifactPublicationEvent(Base):
    """人の操作と自動失効を区別し、同一操作の結果を追記保持する。"""

    __tablename__ = "artifact_publication_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "publication_id"],
            ["artifact_publications.project_id", "artifact_publications.id"],
            name="fk_publication_event_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "participation_id"],
            [
                "project_guest_participations.project_id",
                "project_guest_participations.id",
            ],
            name="fk_participation_event_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "grant_id"],
            [
                "artifact_publication_grants.project_id",
                "artifact_publication_grants.id",
            ],
            name="fk_grant_event_project",
        ),
        Index(
            "uq_publication_operation_request",
            "project_id",
            "actor_id",
            "request_id",
            unique=True,
            postgresql_where=text("is_root"),
        ),
        {
            "comment": db_comment(
                "公開業務履歴", "監査ログとは独立した公開・失効の追記証跡"
            )
        },
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(index=True)
    publication_id: Mapped[UUID | None]
    participation_id: Mapped[UUID | None]
    grant_id: Mapped[UUID | None]
    event_kind: Mapped[str] = mapped_column(String(50))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    actor_kind: Mapped[str] = mapped_column(String(20))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    reason: Mapped[str | None] = mapped_column(String(1000))
    target_state: Mapped[str | None] = mapped_column(String(20))
    target_version: Mapped[int | None]
    public_digest: Mapped[str | None] = mapped_column(String(64))
    operation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "artifact_publication_events.id", deferrable=True, initially="DEFERRED"
        )
    )
    is_root: Mapped[bool] = mapped_column(default=False, server_default="false")
    request_id: Mapped[UUID | None]
    request_digest: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
