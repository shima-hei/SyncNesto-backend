"""固定公開版の本人別参加資格・許可・追記の業務履歴。"""

from datetime import date, datetime
from uuid import UUID

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


class GuestProjectProfile(Base):
    """社内案件から独立した、参加ゲスト向けの公開概要。"""

    __tablename__ = "guest_project_profiles"
    __table_args__ = (
        CheckConstraint(
            "version > 0 AND "
            "(starts_on IS NULL OR ends_on IS NULL OR starts_on <= ends_on)",
            name="ck_guest_project_profile_version_and_dates",
        ),
        {"comment": db_comment("ゲスト公開概要", "参加者向けに明示設定した案件情報")},
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    starts_on: Mapped[date | None]
    ends_on: Mapped[date | None]
    contact: Mapped[str | None] = mapped_column(String(255))
    announcement: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int]


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
        UniqueConstraint("publication_id", "id", name="uq_grant_publication_id"),
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
    download_allowed: Mapped[bool | None]
    comment_allowed: Mapped[bool] = mapped_column(default=False, server_default="false")
    decision_only: Mapped[bool] = mapped_column(default=False, server_default="false")
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


class SharedPublicationComment(Base):
    """共有された固定版の会話。社内コメントとは独立したスレッド。"""

    __tablename__ = "shared_publication_comments"
    __table_args__ = (
        UniqueConstraint("publication_id", "id", name="uq_shared_comment_publication"),
        ForeignKeyConstraint(
            ["publication_id", "parent_id"],
            [
                "shared_publication_comments.publication_id",
                "shared_publication_comments.id",
            ],
            name="fk_shared_comment_parent",
        ),
        ForeignKeyConstraint(
            ["publication_id", "node_id"],
            [
                "artifact_publication_nodes.publication_id",
                "artifact_publication_nodes.id",
            ],
            name="fk_shared_comment_node",
        ),
        CheckConstraint(
            "id <> parent_id AND node_id IS NULL OR parent_id IS NULL",
            name="ck_shared_comment_reply_shape",
        ),
        CheckConstraint(
            "anchor IS NULL OR (parent_id IS NULL AND node_id IS NOT NULL "
            "AND jsonb_typeof(anchor) = 'object')",
            name="ck_shared_comment_quote_shape",
        ),
        Index(
            "ix_shared_comment_thread",
            "publication_id",
            "parent_id",
            "created_at",
            "id",
        ),
        {"comment": db_comment("共有コメント", "共有固定版に紐づく本文・返信")},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    publication_id: Mapped[UUID] = mapped_column(ForeignKey("artifact_publications.id"))
    parent_id: Mapped[UUID | None]
    node_id: Mapped[UUID | None]
    anchor: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    author_label: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class SharedCommentFile(Base):
    """非公開の添付下書きと、それを確定した共有コメント。"""

    __tablename__ = "shared_publication_comment_files"
    __table_args__ = (
        ForeignKeyConstraint(
            ["publication_id", "comment_id"],
            [
                "shared_publication_comments.publication_id",
                "shared_publication_comments.id",
            ],
            name="fk_shared_comment_file_comment",
        ),
        ForeignKeyConstraint(
            ["publication_id", "grant_id"],
            [
                "artifact_publication_grants.publication_id",
                "artifact_publication_grants.id",
            ],
            name="fk_shared_comment_file_grant",
        ),
        CheckConstraint(
            "(comment_id IS NULL AND position IS NULL) OR "
            "(comment_id IS NOT NULL AND position IS NOT NULL AND "
            "position BETWEEN 0 AND 3)",
            name="ck_shared_comment_file_position",
        ),
        UniqueConstraint(
            "comment_id", "position", name="uq_shared_comment_file_position"
        ),
        Index(
            "ix_shared_comment_file_drafts",
            "publication_id",
            "uploaded_by",
            "created_at",
            postgresql_where=text("comment_id IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    publication_id: Mapped[UUID] = mapped_column(ForeignKey("artifact_publications.id"))
    comment_id: Mapped[UUID | None]
    position: Mapped[int | None]
    grant_id: Mapped[UUID | None]
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(32))
    byte_size: Mapped[int]
    content_digest: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class SharedCommentMention(Base):
    """固定版での宛先を、ゲストの付与または社内の公開投稿へ結び付ける。"""

    __tablename__ = "shared_publication_comment_mentions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["publication_id", "comment_id"],
            [
                "shared_publication_comments.publication_id",
                "shared_publication_comments.id",
            ],
            name="fk_shared_comment_mention_comment",
        ),
        ForeignKeyConstraint(
            ["publication_id", "grant_id"],
            [
                "artifact_publication_grants.publication_id",
                "artifact_publication_grants.id",
            ],
            name="fk_shared_comment_mention_grant",
        ),
        ForeignKeyConstraint(
            ["publication_id", "public_author_comment_id"],
            [
                "shared_publication_comments.publication_id",
                "shared_publication_comments.id",
            ],
            name="fk_shared_comment_mention_author",
        ),
        CheckConstraint(
            "(grant_id IS NOT NULL AND public_author_comment_id IS NULL "
            "AND recipient_ref_id = grant_id) OR "
            "(grant_id IS NULL AND public_author_comment_id IS NOT NULL "
            "AND recipient_ref_id = public_author_comment_id)",
            name="ck_shared_comment_mention_recipient",
        ),
    )

    publication_id: Mapped[UUID]
    comment_id: Mapped[UUID] = mapped_column(primary_key=True)
    recipient_ref_id: Mapped[UUID] = mapped_column(primary_key=True)
    grant_id: Mapped[UUID | None]
    public_author_comment_id: Mapped[UUID | None]
    occurrences: Mapped[list[dict]] = mapped_column(JSONB)
