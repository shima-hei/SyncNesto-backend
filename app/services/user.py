"""
ユーザー関連のサービス層を定義するモジュール。

ユーザーの作成や取得におけるビジネスロジックを実装し、
Repository層を通じてデータアクセスを行う。
パスワードのハッシュ化や重複チェックなどの処理を担当する。
"""

import logging

from sqlalchemy.orm import Session

from app.core.exceptions import EmailAlreadyRegisteredError, InvalidCredentialsError
from app.core.security import get_password_hash, verify_password
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.user import UserCreate


logger = logging.getLogger(__name__)


class UserService:
    """
    ユーザーに関するビジネスロジックを提供する。

    Repository層を利用してデータアクセスを行い、
    ユーザー作成時のバリデーションなどを担う。
    """

    def __init__(self, repository: UserRepository | None = None) -> None:
        """UserServiceを初期化する。

        Args:
            repository: ユーザーRepository。
        """
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
            logger.warning("Email already registered: email=%s", user_in.email)
            raise EmailAlreadyRegisteredError()

        hashed_password = get_password_hash(user_in.password)
        user = self.repository.create(db, user_in, hashed_password)
        logger.info("User created: id=%s email=%s", user.id, user.email)
        return user

    def authenticate_user(self, db: Session, email: str, password: str) -> User:
        """ユーザーを認証する。

        Args:
            db: DBセッション。
            email: 認証に使用するメールアドレス。
            password: 認証に使用するパスワード。

        Returns:
            認証されたユーザー。

        Raises:
            InvalidCredentialsError: emailまたはpasswordが正しくない場合。
        """
        user = self.repository.get_by_email(db, email)
        if user is None:
            logger.warning("Invalid login attempt: email=%s", email)
            raise InvalidCredentialsError()

        if not verify_password(password, user.hashed_password):
            logger.warning("Invalid login attempt: email=%s", email)
            raise InvalidCredentialsError()

        logger.info("User authenticated: id=%s email=%s", user.id, user.email)
        return user
