"""
ユーザー関連のサービス層を定義するモジュール。

ユーザーの作成や取得におけるビジネスロジックを実装し、
Repository層を通じてデータアクセスを行う。
パスワードのハッシュ化や重複チェックなどの処理を担当する。
"""

import logging

from sqlalchemy.orm import Session

from app.core.exceptions import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    NotFoundError,
    VersionConflictError,
)
from app.core.security import get_password_hash, verify_password
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.user import UserCreate, UserProfileUpdate, UserRead, UserUpdate
from app.services.storage import StorageService


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

    def create_user(
        self,
        db: Session,
        user_in: UserCreate,
        actor_id: int | None = None,
    ) -> User:
        """ユーザーを作成する。

        Args:
            db: DBセッション。
            user_in: ユーザー作成リクエストの入力値。
            actor_id: 作成者ユーザーID。

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
        user = self.repository.create(db, user_in, hashed_password, actor_id=actor_id)
        logger.info("User created: id=%s email=%s", user.id, user.email)
        return user

    def list_users(self, db: Session) -> list[User]:
        """ユーザー一覧を取得する。

        Args:
            db: DBセッション。

        Returns:
            ユーザー一覧。
        """
        return self.repository.list(db)

    def list_users_paginated(
        self,
        db: Session,
        *,
        page: int,
        page_size: int,
        q: str | None = None,
        is_active: bool | None = None,
    ) -> tuple[list[User], int]:
        """ユーザー一覧をページング付きで取得する。

        Args:
            db: DBセッション。
            page: ページ番号。
            page_size: 1ページあたりの件数。
            q: 検索キーワード。
            is_active: 有効状態の絞り込み。

        Returns:
            ユーザー一覧と総件数。
        """
        return self.repository.list_paginated(
            db,
            page=page,
            page_size=page_size,
            q=q,
            is_active=is_active,
        )

    def get_user(self, db: Session, user_id: int) -> User:
        """ユーザーを取得する。

        Args:
            db: DBセッション。
            user_id: 取得対象ユーザーID。

        Returns:
            取得されたユーザー。

        Raises:
            NotFoundError: ユーザーが存在しない場合。
        """
        user = self.repository.get_by_id(db, user_id)
        if user is None:
            raise NotFoundError("User not found")

        return user

    def update_user(
        self,
        db: Session,
        user_id: int,
        user_in: UserUpdate,
        actor_id: int | None = None,
    ) -> User:
        """ユーザーを更新する。

        Args:
            db: DBセッション。
            user_id: 更新対象ユーザーID。
            user_in: ユーザー更新リクエストの入力値。
            actor_id: 更新者ユーザーID。

        Returns:
            更新されたユーザー。

        Raises:
            EmailAlreadyRegisteredError: emailが既に登録されている場合。
            NotFoundError: ユーザーが存在しない場合。
            VersionConflictError: リクエストのversionが最新ではない場合。
        """
        user = self.get_user(db, user_id)
        if user.version != user_in.version:
            current = UserRead.model_validate(user).model_dump()
            raise VersionConflictError(current=current)

        if user_in.email is not None and user_in.email != user.email:
            existing_user = self.repository.get_by_email(db, user_in.email)
            if existing_user is not None:
                raise EmailAlreadyRegisteredError()

        hashed_password = None
        if user_in.password is not None:
            hashed_password = get_password_hash(user_in.password)

        return self.repository.update(
            db,
            user=user,
            user_in=user_in,
            hashed_password=hashed_password,
            actor_id=actor_id,
        )

    def update_profile(
        self,
        db: Session,
        *,
        current_user: User,
        user_in: UserProfileUpdate,
    ) -> User:
        """本人プロフィールを更新する。

        Args:
            db: DBセッション。
            current_user: 認証済みユーザー。
            user_in: 本人プロフィール更新リクエストの入力値。

        Returns:
            更新されたユーザー。

        Raises:
            VersionConflictError: リクエストのversionが最新ではない場合。
        """
        if current_user.version != user_in.version:
            current = UserRead.model_validate(current_user).model_dump()
            raise VersionConflictError(current=current)

        hashed_password = None
        if user_in.password is not None:
            hashed_password = get_password_hash(user_in.password)

        return self.repository.update_profile(
            db,
            user=current_user,
            user_in=user_in,
            hashed_password=hashed_password,
            actor_id=current_user.id,
        )

    def update_avatar(
        self,
        db: Session,
        *,
        current_user: User,
        content: bytes,
        content_type: str | None,
        storage_service: StorageService,
    ) -> User:
        """本人のユーザーアイコンを更新する。

        Args:
            db: DBセッション。
            current_user: 認証済みユーザー。
            content: 画像バイナリ。
            content_type: アップロードファイルのContent-Type。
            storage_service: ストレージサービス。

        Returns:
            更新されたユーザー。
        """
        avatar_key = storage_service.upload_user_avatar(
            user_id=current_user.id,
            content=content,
            content_type=content_type,
        )
        return self.repository.update_avatar_key(
            db,
            user=current_user,
            avatar_key=avatar_key,
            actor_id=current_user.id,
        )

    def delete_user(self, db: Session, user_id: int) -> None:
        """ユーザーを論理削除する。

        Args:
            db: DBセッション。
            user_id: 削除対象ユーザーID。

        Raises:
            NotFoundError: ユーザーが存在しない場合。
        """
        user = self.get_user(db, user_id)
        self.repository.soft_delete(db, user)

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

        if not user.is_active:
            logger.warning("Inactive user login attempt: id=%s email=%s", user.id, email)
            raise InvalidCredentialsError()

        if not verify_password(password, user.hashed_password):
            logger.warning("Invalid login attempt: email=%s", email)
            raise InvalidCredentialsError()

        logger.info("User authenticated: id=%s email=%s", user.id, user.email)
        return user

    def update_last_login_at(self, db: Session, user: User) -> User:
        """ユーザーの最終ログイン日時を更新する。

        Args:
            db: DBセッション。
            user: 更新対象ユーザー。

        Returns:
            更新されたユーザー。
        """
        return self.repository.update_last_login_at(db, user)
