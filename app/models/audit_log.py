"""監査ログに関するSQLAlchemyモデルを定義するモジュール。"""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class AuditLog(Base):
    """重要操作の監査ログを管理するモデル。"""

    __tablename__ = "audit_logs"
    __table_args__ = {
        "comment": db_comment("監査ログ", "重要操作の監査証跡を管理するテーブル"),
    }

    id: Mapped[int] = mapped_column(
        primary_key=True,
        comment=db_comment("監査ログID", "監査ログを一意に識別するID"),
    )
    event_type: Mapped[str] = mapped_column(
        String(100),
        index=True,
        comment=db_comment("イベント種別", "監査対象操作を表すイベント種別"),
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
        comment=db_comment("操作ユーザーID", "操作を実行したユーザーID"),
    )
    target_user_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
        comment=db_comment("対象ユーザーID", "操作対象になったユーザーID"),
    )
    project_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
        comment=db_comment("プロジェクトID", "関連するプロジェクトID"),
    )
    resource_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
        comment=db_comment("リソース種別", "操作対象リソースの種別"),
    )
    resource_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
        comment=db_comment("リソースID", "操作対象リソースのID"),
    )
    ip_address: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment=db_comment("IPアドレス", "接続元IPアドレス"),
    )
    user_agent: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
        comment=db_comment("User-Agent", "リクエストのUser-Agent"),
    )
    request_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
        comment=db_comment("リクエストID", "リクエストを識別するID"),
    )
    extra_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
        comment=db_comment("メタデータ", "操作補足情報。秘匿情報は保存しない"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        index=True,
        comment=db_comment("作成日時", "監査ログが作成された日時"),
    )
