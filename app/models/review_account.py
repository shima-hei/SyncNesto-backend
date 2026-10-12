"""公開審査専用Identityの期限付き利用許可。通常ユーザーには付与しない。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class ReviewAccountGrant(Base):
    """運用CLIだけが作成する、一人・一つの隔離組織に限定した許可。"""

    __tablename__ = "review_account_grants"
    __table_args__ = {
        "comment": db_comment(
            "審査用利用許可", "通常MFA方針と分離した期限付き審査Identity"
        ),
    }

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
