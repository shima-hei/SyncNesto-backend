"""認証セッションRepositoryを定義するモジュール。"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.session import UserSession
from app.models.user import User


class UserSessionRepository:
    """UserSessionテーブルへのデータアクセス処理を提供する。"""

    def create(self, db: Session, user: User) -> UserSession:
        """ユーザーのログインセッションを作成する。

        Args:
            db: DBセッション。
            user: セッションを作成するユーザー。

        Returns:
            作成されたセッション。
        """
        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=settings.session_idle_timeout_minutes)
        absolute_expires_at = now + timedelta(
            minutes=settings.session_absolute_timeout_minutes
        )
        if expires_at > absolute_expires_at:
            expires_at = absolute_expires_at

        user_session = UserSession(
            user_id=user.id,
            started_at=now,
            last_seen_at=now,
            expires_at=expires_at,
            absolute_expires_at=absolute_expires_at,
        )
        db.add(user_session)
        db.commit()
        db.refresh(user_session)
        return user_session

    def get_by_id(self, db: Session, session_id: UUID) -> UserSession | None:
        """セッションIDに一致するセッションを取得する。

        Args:
            db: DBセッション。
            session_id: セッションID。

        Returns:
            一致するセッション。存在しない場合はNone。
        """
        return db.query(UserSession).filter(UserSession.id == session_id).first()

    def extend(self, db: Session, user_session: UserSession) -> UserSession:
        """セッションのアイドル有効期限を延長する。

        Args:
            db: DBセッション。
            user_session: 延長対象セッション。

        Returns:
            延長されたセッション。
        """
        now = datetime.now(UTC)
        next_expires_at = now + timedelta(
            minutes=settings.session_idle_timeout_minutes
        )
        if next_expires_at > user_session.absolute_expires_at:
            next_expires_at = user_session.absolute_expires_at

        user_session.last_seen_at = now
        user_session.expires_at = next_expires_at
        db.commit()
        db.refresh(user_session)
        return user_session

    def touch(self, db: Session, user_session: UserSession) -> UserSession:
        """セッションの最終アクセス日時を更新する。

        Args:
            db: DBセッション。
            user_session: 更新対象セッション。

        Returns:
            更新されたセッション。
        """
        user_session.last_seen_at = datetime.now(UTC)
        db.commit()
        db.refresh(user_session)
        return user_session

    def revoke(
        self,
        db: Session,
        user_session: UserSession,
        reason: str,
    ) -> UserSession:
        """セッションを失効させる。

        Args:
            db: DBセッション。
            user_session: 失効対象セッション。
            reason: 失効理由。

        Returns:
            失効されたセッション。
        """
        if user_session.revoked_at is None:
            user_session.revoked_at = datetime.now(UTC)
            user_session.revoked_reason = reason
            db.commit()
            db.refresh(user_session)

        return user_session

    def revoke_all_by_user_id(
        self,
        db: Session,
        *,
        user_id: int,
        reason: str,
    ) -> int:
        """ユーザーに紐づく有効セッションをすべて失効する。

        Args:
            db: DBセッション。
            user_id: セッションを失効する対象ユーザーID。
            reason: 失効理由。

        Returns:
            失効したセッション件数。
        """
        now = datetime.now(UTC)
        sessions = (
            db.query(UserSession)
            .filter(
                UserSession.user_id == user_id,
                UserSession.revoked_at.is_(None),
            )
            .all()
        )
        for user_session in sessions:
            user_session.revoked_at = now
            user_session.revoked_reason = reason

        if sessions:
            db.commit()

        return len(sessions)
