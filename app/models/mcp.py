"""ユーザーが許可したMCP接続と一回限りの資格情報。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class McpConnection(Base):
    """一人・一組織に限定したOAuth委任。"""

    __tablename__ = "mcp_connections"
    __table_args__ = {"comment": db_comment("MCP接続", "本人が許可した業務操作の範囲")}

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    client_id: Mapped[str] = mapped_column(String(100))
    resource: Mapped[str] = mapped_column(String(500))
    project_ids: Mapped[list[int]] = mapped_column(JSONB)
    scopes: Mapped[list[str]] = mapped_column(JSONB)
    credential_fingerprint: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class McpAuthorizationRequest(Base):
    """ログイン・同意・PKCE交換を結ぶ短命の要求。"""

    __tablename__ = "mcp_authorization_requests"
    __table_args__ = {
        "comment": db_comment("MCP認可要求", "一回限りの認可コードとPKCE")
    }

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    client_id: Mapped[str] = mapped_column(String(100))
    redirect_uri: Mapped[str] = mapped_column(String(500))
    resource: Mapped[str] = mapped_column(String(500))
    scopes: Mapped[list[str]] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(500))
    code_challenge: Mapped[str] = mapped_column(String(128))
    code_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    connection_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("mcp_connections.id", ondelete="CASCADE")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class McpCredential(Base):
    """平文を保存しない短命accessとローテーションするrefresh。"""

    __tablename__ = "mcp_credentials"
    __table_args__ = {
        "comment": db_comment(
            "MCP資格情報", "用途・期限・使用済み状態を検証するハッシュ"
        )
    }

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("mcp_connections.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(20))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class McpOperationReceipt(Base):
    """業務変更と同一トランザクションで保存する再試行結果。"""

    __tablename__ = "mcp_operation_receipts"
    __table_args__ = (
        UniqueConstraint("connection_id", "operation_key", name="uq_mcp_operation_key"),
        {"comment": db_comment("MCP操作結果", "重複作成を防ぐ本人接続ごとの記録")},
    )

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("mcp_connections.id", ondelete="CASCADE"), index=True
    )
    operation_key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
