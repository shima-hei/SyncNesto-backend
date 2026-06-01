"""要件定義に関するSQLAlchemyモデルを定義するモジュール。"""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class RequirementDocument(Base):
    """要件定義書全体を管理するモデル。"""

    __tablename__ = "requirement_documents"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "document_code",
            name="uq_requirement_documents_project_document_code",
        ),
        {
            "comment": db_comment("要件定義書", "要件定義書全体を管理するテーブル"),
        },
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment("要件定義書ID", "要件定義書を一意に識別するID"),
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"),
        index=True,
        comment=db_comment("プロジェクトID", "要件定義書が属するプロジェクトID"),
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment=db_comment("タイトル", "要件定義書のタイトル"),
    )
    document_code: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment=db_comment("文書コード", "プロジェクト内で一意な要件定義書コード"),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        comment=db_comment("バージョン", "楽観的排他制御に使用するバージョン番号"),
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="draft",
        comment=db_comment("ステータス", "要件定義書の状態"),
    )
    purpose: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=db_comment("目的", "要件定義書の目的"),
    )
    target_system_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment=db_comment("対象システム名", "要件定義の対象システム名"),
    )
    client_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment=db_comment("クライアント名", "発注者または利用部門名"),
    )
    vendor_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment=db_comment("ベンダー名", "開発または提供ベンダー名"),
    )
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("作成担当者ID", "要件定義書の作成担当ユーザーID"),
    )
    reviewer_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("レビュー担当者ID", "要件定義書のレビュー担当ユーザーID"),
    )
    approver_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("承認者ID", "要件定義書の承認ユーザーID"),
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=db_comment("承認日時", "要件定義書が承認された日時"),
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("作成者ID", "この要件定義書を作成したユーザーID"),
    )
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("更新者ID", "この要件定義書を最後に更新したユーザーID"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment=db_comment("作成日時", "レコードが作成された日時"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        comment=db_comment("更新日時", "レコードが最後に更新された日時"),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=db_comment("削除日時", "論理削除された日時"),
    )


class Requirement(Base):
    """要件を管理するモデル。"""

    __tablename__ = "requirements"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "requirement_code",
            name="uq_requirements_document_requirement_code",
        ),
        {
            "comment": db_comment(
                "要件",
                "要件定義を構成する個別要件を管理するテーブル",
            ),
        },
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment("要件ID", "要件を一意に識別するID"),
    )
    document_id: Mapped[int] = mapped_column(
        ForeignKey("requirement_documents.id"),
        index=True,
        comment=db_comment("要件定義書ID", "所属する要件定義書ID"),
    )
    requirement_code: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment=db_comment("要件コード", "要件定義書内で一意な要件識別子"),
    )
    requirement_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment=db_comment("要件種別", "business/functionalなどの要件種別"),
    )
    category: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment=db_comment("カテゴリ", "要件の分類"),
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment=db_comment("タイトル", "要件のタイトル"),
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=db_comment("説明", "要件の本文または説明"),
    )
    rationale: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=db_comment("根拠", "要件が必要な理由や背景"),
    )
    acceptance_criteria: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=db_comment("受入条件", "要件を満たしたと判断する条件"),
    )
    priority: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="must",
        comment=db_comment("優先度", "must/should/could/won'tの優先度"),
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="draft",
        comment=db_comment("ステータス", "要件の状態"),
    )
    source: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment=db_comment("発生元", "要件の発生元や参照元"),
    )
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("担当者ID", "要件の担当ユーザーID"),
    )
    approved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("承認者ID", "要件を承認したユーザーID"),
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=db_comment("承認日時", "要件が承認された日時"),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        comment=db_comment("バージョン", "楽観的排他制御に使用するバージョン番号"),
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("作成者ID", "この要件を作成したユーザーID"),
    )
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("更新者ID", "この要件を最後に更新したユーザーID"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment=db_comment("作成日時", "レコードが作成された日時"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        comment=db_comment("更新日時", "レコードが最後に更新された日時"),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=db_comment("削除日時", "論理削除された日時"),
    )


class RequirementRevision(Base):
    """要件の変更履歴を管理するモデル。"""

    __tablename__ = "requirement_revisions"
    __table_args__ = {
        "comment": db_comment("要件改訂履歴", "要件の変更履歴を管理するテーブル"),
    }

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment("要件改訂履歴ID", "要件改訂履歴を一意に識別するID"),
    )
    requirement_id: Mapped[int] = mapped_column(
        ForeignKey("requirements.id"),
        index=True,
        comment=db_comment("要件ID", "変更対象の要件ID"),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment=db_comment("バージョン", "変更後の要件バージョン"),
    )
    changed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("変更者ID", "要件を変更したユーザーID"),
    )
    change_summary: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment=db_comment("変更概要", "変更内容の概要"),
    )
    before_value: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment=db_comment("変更前値", "変更前の要件情報JSON"),
    )
    after_value: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment=db_comment("変更後値", "変更後の要件情報JSON"),
    )
    reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=db_comment("変更理由", "変更した理由"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment=db_comment("作成日時", "レコードが作成された日時"),
    )


class RequirementDetail(Base):
    """要件種別ごとの差分項目を管理するモデル。"""

    __tablename__ = "requirement_details"
    __table_args__ = {
        "comment": db_comment(
            "要件詳細",
            "要件種別ごとの差分項目をJSONで管理するテーブル",
        ),
    }

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment("要件詳細ID", "要件詳細を一意に識別するID"),
    )
    requirement_id: Mapped[int] = mapped_column(
        ForeignKey("requirements.id"),
        index=True,
        comment=db_comment("要件ID", "詳細を紐づける要件ID"),
    )
    detail_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment=db_comment("詳細種別", "詳細JSONの種別"),
    )
    detail_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        comment=db_comment("詳細JSON", "要件種別ごとの追加情報JSON"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment=db_comment("作成日時", "レコードが作成された日時"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        comment=db_comment("更新日時", "レコードが最後に更新された日時"),
    )


class RequirementLink(Base):
    """要件と成果物の紐づけを管理するモデル。"""

    __tablename__ = "requirement_links"
    __table_args__ = {
        "comment": db_comment("要件リンク", "要件と成果物の紐づけを管理するテーブル"),
    }

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment("要件リンクID", "要件リンクを一意に識別するID"),
    )
    requirement_id: Mapped[int] = mapped_column(
        ForeignKey("requirements.id"),
        index=True,
        comment=db_comment("要件ID", "紐づけ元の要件ID"),
    )
    linked_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment=db_comment("リンク種別", "screen/api/databaseなどの紐づけ先種別"),
    )
    linked_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment=db_comment("リンク先ID", "紐づけ先リソースの識別子"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment=db_comment("作成日時", "レコードが作成された日時"),
    )


class RequirementReview(Base):
    """要件レビューを管理するモデル。"""

    __tablename__ = "requirement_reviews"
    __table_args__ = {
        "comment": db_comment("要件レビュー", "要件レビュー結果を管理するテーブル"),
    }

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment("要件レビューID", "要件レビューを一意に識別するID"),
    )
    requirement_id: Mapped[int] = mapped_column(
        ForeignKey("requirements.id"),
        index=True,
        comment=db_comment("要件ID", "レビュー対象の要件ID"),
    )
    reviewer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        comment=db_comment("レビュー担当者ID", "レビューを行うユーザーID"),
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="pending",
        comment=db_comment(
            "レビューステータス",
            "pending/approved/rejected/commentedの状態",
        ),
    )
    comment: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=db_comment("コメント", "レビューコメント"),
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=db_comment("レビュー日時", "レビューが行われた日時"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment=db_comment("作成日時", "レコードが作成された日時"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        comment=db_comment("更新日時", "レコードが最後に更新された日時"),
    )


class RequirementComment(Base):
    """要件コメントを管理するモデル。"""

    __tablename__ = "requirement_comments"
    __table_args__ = {
        "comment": db_comment("要件コメント", "要件に対するコメントを管理するテーブル"),
    }

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment("要件コメントID", "要件コメントを一意に識別するID"),
    )
    requirement_id: Mapped[int] = mapped_column(
        ForeignKey("requirements.id"),
        index=True,
        comment=db_comment("要件ID", "コメント対象の要件ID"),
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        comment=db_comment("ユーザーID", "コメントを書いたユーザーID"),
    )
    comment: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=db_comment("コメント", "コメント本文"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment=db_comment("作成日時", "レコードが作成された日時"),
    )
