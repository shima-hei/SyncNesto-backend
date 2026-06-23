"""要件定義に関するSQLAlchemyモデルを定義するモジュール。"""

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
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


class RequirementSection(Base):
    """要件定義書内の章・節を管理するモデル。"""

    __tablename__ = "requirement_sections"
    __table_args__ = {
        "comment": db_comment(
            "要件定義セクション",
            "要件定義書内の章・節を管理するテーブル",
        ),
    }

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment(
            "要件定義セクションID",
            "要件定義セクションを一意に識別するID",
        ),
    )
    document_id: Mapped[int] = mapped_column(
        ForeignKey("requirement_documents.id"),
        index=True,
        comment=db_comment("要件定義書ID", "所属する要件定義書ID"),
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment=db_comment("タイトル", "セクションのタイトル"),
    )
    section_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment=db_comment(
            "セクション種別",
            "overview/scope/businessなどのセクション種別",
        ),
    )
    content: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=db_comment("本文", "セクションの本文"),
    )
    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment=db_comment("表示順", "要件定義書内での表示順"),
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="draft",
        comment=db_comment("ステータス", "セクションの状態"),
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
        comment=db_comment("作成者ID", "このセクションを作成したユーザーID"),
    )
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("更新者ID", "このセクションを最後に更新したユーザーID"),
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
    section_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirement_sections.id"),
        index=True,
        nullable=True,
        comment=db_comment("要件定義セクションID", "所属するセクションID"),
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


class RequirementOpenIssue(Base):
    """要件定義の未決事項を管理するモデル。"""

    __tablename__ = "requirement_open_issues"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "issue_code",
            name="uq_requirement_open_issues_document_issue_code",
        ),
        {
            "comment": db_comment(
                "要件定義未決事項",
                "要件定義書内の未決事項を管理するテーブル",
            ),
        },
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment("未決事項ID", "未決事項を一意に識別するID"),
    )
    document_id: Mapped[int] = mapped_column(
        ForeignKey("requirement_documents.id"),
        index=True,
        comment=db_comment("要件定義書ID", "所属する要件定義書ID"),
    )
    related_requirement_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirements.id"),
        nullable=True,
        comment=db_comment("関連要件ID", "関連または昇格先の要件ID"),
    )
    issue_code: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment=db_comment("未決事項コード", "要件定義書内で一意な未決事項コード"),
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment=db_comment("タイトル", "未決事項のタイトル"),
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=db_comment("説明", "未決事項の本文または説明"),
    )
    impact_scope: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=db_comment("影響範囲", "未決事項が影響する範囲"),
    )
    assignee_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("担当者ID", "未決事項の担当ユーザーID"),
    )
    due_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment=db_comment("期限日", "未決事項の対応期限日"),
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="open",
        comment=db_comment("ステータス", "未決事項の状態"),
    )
    resolution: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=db_comment("解決内容", "未決事項の解決内容"),
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
        comment=db_comment("作成者ID", "この未決事項を作成したユーザーID"),
    )
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("更新者ID", "この未決事項を最後に更新したユーザーID"),
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


class RequirementChangeLog(Base):
    """要件定義の横断変更履歴を管理するモデル。"""

    __tablename__ = "requirement_change_logs"
    __table_args__ = {
        "comment": db_comment(
            "要件定義変更履歴",
            "要件定義に関する横断的な変更履歴を管理するテーブル",
        ),
    }

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment("要件定義変更履歴ID", "変更履歴を一意に識別するID"),
    )
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirement_documents.id"),
        nullable=True,
        index=True,
        comment=db_comment("要件定義書ID", "関連する要件定義書ID"),
    )
    target_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment=db_comment("対象種別", "変更対象の種別"),
    )
    target_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
        comment=db_comment("対象ID", "変更対象のID"),
    )
    action: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment=db_comment("操作", "created/updated/deletedなどの操作種別"),
    )
    field_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment=db_comment("項目名", "変更された項目名"),
    )
    old_value: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment=db_comment("変更前値", "変更前の値JSON"),
    )
    new_value: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment=db_comment("変更後値", "変更後の値JSON"),
    )
    reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=db_comment("変更理由", "変更した理由"),
    )
    changed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("変更者ID", "変更したユーザーID"),
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment=db_comment("変更日時", "変更が記録された日時"),
    )


class RequirementApproval(Base):
    """要件定義対象の承認状態を管理するモデル。"""

    __tablename__ = "requirement_approvals"
    __table_args__ = {
        "comment": db_comment(
            "要件定義承認",
            "要件定義対象の承認申請と承認結果を管理するテーブル",
        ),
    }

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment("承認ID", "承認を一意に識別するID"),
    )
    document_id: Mapped[int] = mapped_column(
        ForeignKey("requirement_documents.id"),
        index=True,
        comment=db_comment("要件定義書ID", "承認対象が属する要件定義書ID"),
    )
    target_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment=db_comment("対象種別", "承認対象の種別"),
    )
    target_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
        comment=db_comment("対象ID", "承認対象のID"),
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="requested",
        comment=db_comment("ステータス", "requested/approved/rejectedの状態"),
    )
    approver_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        comment=db_comment("承認者ID", "承認判断を行うユーザーID"),
    )
    requested_by: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        comment=db_comment("申請者ID", "承認申請を行ったユーザーID"),
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment=db_comment("申請日時", "承認申請が行われた日時"),
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=db_comment("承認日時", "承認された日時"),
    )
    rejected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=db_comment("差し戻し日時", "差し戻しされた日時"),
    )
    comment: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=db_comment("コメント", "承認申請または判断時のコメント"),
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


class RequirementTargetComment(Base):
    """要件定義内の任意対象へのコメントを管理するモデル。"""

    __tablename__ = "requirement_target_comments"
    __table_args__ = {
        "comment": db_comment(
            "要件定義対象コメント",
            "要件定義内の文書・セクション・要件・未決事項へのコメントを管理するテーブル",
        ),
    }

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment("コメントID", "コメントを一意に識別するID"),
    )
    document_id: Mapped[int] = mapped_column(
        ForeignKey("requirement_documents.id"),
        index=True,
        comment=db_comment("要件定義書ID", "コメント対象が属する要件定義書ID"),
    )
    target_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment=db_comment("対象種別", "コメント対象の種別"),
    )
    target_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
        comment=db_comment("対象ID", "コメント対象のID"),
    )
    parent_comment_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirement_target_comments.id"),
        nullable=True,
        comment=db_comment("親コメントID", "返信先のコメントID"),
    )
    body: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=db_comment("本文", "コメント本文"),
    )
    author_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        comment=db_comment("投稿者ID", "コメントを投稿したユーザーID"),
    )
    is_resolved: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment=db_comment("解決済みフラグ", "コメントが解決済みかを示すフラグ"),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        comment=db_comment("バージョン", "楽観的排他制御に使用するバージョン番号"),
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


class RequirementRelation(Base):
    """要件と他リソースの意味付き関連を管理するモデル。"""

    __tablename__ = "requirement_relations"
    __table_args__ = {
        "comment": db_comment(
            "要件関連",
            "要件と他リソースの意味付き関連を管理するテーブル",
        ),
    }

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment("要件関連ID", "要件関連を一意に識別するID"),
    )
    document_id: Mapped[int] = mapped_column(
        ForeignKey("requirement_documents.id"),
        index=True,
        comment=db_comment("要件定義書ID", "関連元要件が属する要件定義書ID"),
    )
    source_requirement_id: Mapped[int] = mapped_column(
        ForeignKey("requirements.id"),
        index=True,
        comment=db_comment("関連元要件ID", "関連元の要件ID"),
    )
    target_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment=db_comment("関連先種別", "関連先リソースの種別"),
    )
    target_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment=db_comment("関連先ID", "関連先リソースのIDまたは外部識別子"),
    )
    relation_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment=db_comment("関連種別", "depends_on/blocks/relates_toなどの関連種別"),
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=db_comment("説明", "関連の補足説明"),
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("作成者ID", "この関連を作成したユーザーID"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment=db_comment("作成日時", "レコードが作成された日時"),
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
