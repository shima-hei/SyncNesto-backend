"""ログイン試行回数制限のビジネスロジックを提供するモジュール。"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.login_attempt import LoginAttempt
from app.repositories.login_attempt import LoginAttemptRepository


class LoginAttemptService:
    """ログイン失敗回数と一時ロック状態を管理する。"""

    def __init__(self, repository: LoginAttemptRepository | None = None) -> None:
        """LoginAttemptServiceを初期化する。

        Args:
            repository: ログイン試行Repository。
        """
        self.repository = repository or LoginAttemptRepository()

    def normalize_email(self, email: str) -> str:
        """ログイン試行管理用にemailを正規化する。

        Args:
            email: 入力されたメールアドレス。

        Returns:
            前後空白を除去し、小文字化したメールアドレス。
        """
        return email.strip().lower()

    def is_locked(self, db: Session, email: str) -> bool:
        """emailがログインロック中か判定する。

        Args:
            db: DBセッション。
            email: 入力されたメールアドレス。

        Returns:
            ロック中の場合はTrue。
        """
        normalized_email = self.normalize_email(email)
        login_attempt = self.repository.get_by_email(db, normalized_email)
        if login_attempt is None or login_attempt.locked_until is None:
            return False

        now = datetime.now(UTC)
        if login_attempt.locked_until > now:
            return True

        self.repository.reset(db, login_attempt)
        db.commit()
        return False

    def record_failure(self, db: Session, email: str) -> LoginAttempt:
        """ログイン失敗を記録する。

        Args:
            db: DBセッション。
            email: 入力されたメールアドレス。

        Returns:
            更新されたログイン試行情報。
        """
        normalized_email = self.normalize_email(email)
        now = datetime.now(UTC)
        login_attempt = self.repository.get_or_create(db, normalized_email)

        failed_count = login_attempt.failed_count
        if (
            login_attempt.locked_until is not None
            and login_attempt.locked_until <= now
        ):
            failed_count = 0

        failed_count += 1
        locked_until = None
        if failed_count >= settings.login_max_failed_attempts:
            locked_until = now + timedelta(minutes=settings.login_lock_minutes)

        login_attempt = self.repository.record_failure(
            db,
            login_attempt=login_attempt,
            failed_count=failed_count,
            locked_until=locked_until,
            failed_at=now,
        )
        db.commit()
        db.refresh(login_attempt)
        return login_attempt

    def reset(self, db: Session, email: str) -> None:
        """ログイン試行情報をリセットする。

        Args:
            db: DBセッション。
            email: 入力されたメールアドレス。
        """
        normalized_email = self.normalize_email(email)
        login_attempt = self.repository.get_by_email(db, normalized_email)
        if login_attempt is None:
            return

        self.repository.reset(db, login_attempt)
        db.commit()
