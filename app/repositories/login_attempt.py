"""ログイン試行回数Repositoryを定義するモジュール。"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.models.login_attempt import LoginAttempt


class LoginAttemptRepository:
    """LoginAttemptテーブルへのデータアクセス処理を提供するRepository。"""

    def get_by_email(self, db: Session, email: str) -> LoginAttempt | None:
        """emailに一致するログイン試行情報を取得する。

        Args:
            db: DBセッション。
            email: 小文字化済みのメールアドレス。

        Returns:
            一致するログイン試行情報。存在しない場合はNone。
        """
        return db.query(LoginAttempt).filter(LoginAttempt.email == email).first()

    def get_or_create(self, db: Session, email: str) -> LoginAttempt:
        """emailに一致するログイン試行情報を取得または作成する。

        Args:
            db: DBセッション。
            email: 小文字化済みのメールアドレス。

        Returns:
            取得または作成したログイン試行情報。
        """
        login_attempt = self.get_by_email(db, email)
        if login_attempt is not None:
            return login_attempt

        login_attempt = LoginAttempt(email=email)
        db.add(login_attempt)
        db.flush()
        return login_attempt

    def record_failure(
        self,
        db: Session,
        *,
        login_attempt: LoginAttempt,
        failed_count: int,
        locked_until: datetime | None,
        failed_at: datetime,
    ) -> LoginAttempt:
        """ログイン失敗情報を保存する。

        Args:
            db: DBセッション。
            login_attempt: 更新対象のログイン試行情報。
            failed_count: 更新後の連続失敗回数。
            locked_until: ロック期限日時。
            failed_at: 失敗日時。

        Returns:
            更新されたログイン試行情報。
        """
        login_attempt.failed_count = failed_count
        login_attempt.locked_until = locked_until
        login_attempt.last_failed_at = failed_at
        db.flush()
        return login_attempt

    def reset(self, db: Session, login_attempt: LoginAttempt) -> LoginAttempt:
        """ログイン試行情報をリセットする。

        Args:
            db: DBセッション。
            login_attempt: リセット対象のログイン試行情報。

        Returns:
            リセットされたログイン試行情報。
        """
        login_attempt.failed_count = 0
        login_attempt.locked_until = None
        login_attempt.last_failed_at = None
        db.flush()
        return login_attempt
