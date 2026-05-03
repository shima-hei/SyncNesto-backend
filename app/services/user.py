"""
ユーザー関連のサービス層を定義するモジュール。

ユーザーの作成や取得におけるビジネスロジックを実装し、
Repository層を通じてデータアクセスを行う。
パスワードのハッシュ化や重複チェックなどの処理を担当する。
"""

from sqlalchemy.orm import Session

from app.core.exceptions import EmailAlreadyRegisteredError
from app.core.security import get_password_hash
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.user import UserCreate


class UserService:
    """
    ユーザーに関するビジネスロジックを提供するサービスクラス。

    Repository層を利用してデータアクセスを行い、
    ユーザー作成時のバリデーションやパスワードハッシュ化などの
    アプリケーションロジックを担う。
    """
    def __init__(self, repository: UserRepository | None = None) -> None:
        self.repository = repository or UserRepository()

    def create_user(self, db: Session, user_in: UserCreate) -> User:
        """ユーザーを作成する。

        Args:
            db: DBセッション。
            user_in: ユーザー作成リクエストの入力値。

        Returns:
            作成されたユーザー。

        Raises:
            EmailAlreadyRegisteredError: emailが既に登録されている場合。
        """
        existing_user = self.repository.get_by_email(db, user_in.email)
        if existing_user is not None:
            raise EmailAlreadyRegisteredError()

        hashed_password = get_password_hash(user_in.password)
        return self.repository.create(db, user_in, hashed_password)
