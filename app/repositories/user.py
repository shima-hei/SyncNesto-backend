"""
ユーザーRepositoryを定義するモジュール。

Userテーブルに対するCRUD操作を提供する。
"""

from datetime import UTC, datetime

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.user import User
from app.schemas.user import UserCreate, UserProfileUpdate, UserUpdate


class UserRepository:
    """Userテーブルへのデータアクセス処理を提供するRepository。"""

    def create(
        self,
        db: Session,
        user_in: UserCreate,
        hashed_password: str,
        actor_id: int | None = None,
    ) -> User:
        """ユーザーを作成する。

        Args:
            db: DBセッション。
            user_in: ユーザー作成リクエストの入力値。
            hashed_password: ハッシュ化されたパスワード。
            actor_id: 作成者ユーザーID。

        Returns:
            作成されたユーザー。
        """
        user = User(
            email=user_in.email,
            name=user_in.name,
            hashed_password=hashed_password,
            department=user_in.department,
            position=user_in.position,
            avatar_key=settings.default_avatar_key,
            is_active=user_in.is_active,
            created_by=actor_id,
            updated_by=actor_id,
        )
        db.add(user)
        db.flush()
        return user

    def get_by_email(self, db: Session, email: str) -> User | None:
        """emailに一致するユーザーを取得する。

        Args:
            db: DBセッション。
            email: 検索対象のメールアドレス。

        Returns:
            一致するユーザー。存在しない場合はNone。
        """
        return (
            db.query(User)
            .filter(User.email == email, User.deleted_at.is_(None))
            .first()
        )

    def get_by_id(self, db: Session, user_id: int) -> User | None:
        """idに一致するユーザーを取得する。

        Args:
            db: DBセッション。
            user_id: 検索対象のユーザーID。

        Returns:
            一致するユーザー。存在しない場合はNone。
        """
        return (
            db.query(User)
            .filter(User.id == user_id, User.deleted_at.is_(None))
            .first()
        )

    def list(self, db: Session) -> list[User]:
        """削除されていないユーザー一覧を取得する。

        Args:
            db: DBセッション。

        Returns:
            ユーザー一覧。
        """
        return db.query(User).filter(User.deleted_at.is_(None)).order_by(User.id).all()

    def list_by_ids(self, db: Session, user_ids: list[int]) -> list[User]:
        """id一覧に一致する削除されていないユーザー一覧を取得する。

        Args:
            db: DBセッション。
            user_ids: 検索対象ユーザーID一覧。

        Returns:
            一致するユーザー一覧。
        """
        if not user_ids:
            return []

        return (
            db.query(User)
            .filter(User.id.in_(user_ids), User.deleted_at.is_(None))
            .order_by(User.id)
            .all()
        )

    def list_paginated(
        self,
        db: Session,
        *,
        page: int,
        page_size: int,
        q: str | None = None,
        is_active: bool | None = None,
    ) -> tuple[list[User], int]:
        """削除されていないユーザー一覧をページング付きで取得する。

        Args:
            db: DBセッション。
            page: ページ番号。
            page_size: 1ページあたりの件数。
            q: 検索キーワード。
            is_active: 有効状態の絞り込み。

        Returns:
            ユーザー一覧と総件数。
        """
        query = db.query(User).filter(User.deleted_at.is_(None))
        if q:
            like_pattern = f"%{q}%"
            query = query.filter(
                or_(
                    User.email.ilike(like_pattern),
                    User.name.ilike(like_pattern),
                    User.department.ilike(like_pattern),
                    User.position.ilike(like_pattern),
                )
            )
        if is_active is not None:
            query = query.filter(User.is_active == is_active)

        total = query.count()
        users = (
            query.order_by(User.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return users, total

    def update(
        self,
        db: Session,
        *,
        user: User,
        user_in: UserUpdate,
        hashed_password: str | None = None,
        actor_id: int | None = None,
    ) -> User:
        """ユーザーを更新する。

        Args:
            db: DBセッション。
            user: 更新対象ユーザー。
            user_in: ユーザー更新リクエストの入力値。
            hashed_password: ハッシュ化されたパスワード。
            actor_id: 更新者ユーザーID。

        Returns:
            更新されたユーザー。
        """
        if user_in.email is not None:
            user.email = user_in.email
        if user_in.name is not None:
            user.name = user_in.name
        if hashed_password is not None:
            user.hashed_password = hashed_password
        if "department" in user_in.model_fields_set:
            user.department = user_in.department
        if "position" in user_in.model_fields_set:
            user.position = user_in.position
        if user_in.is_active is not None:
            user.is_active = user_in.is_active
        if actor_id is not None:
            user.updated_by = actor_id
        user.version += 1

        db.flush()
        return user

    def update_profile(
        self,
        db: Session,
        *,
        user: User,
        user_in: UserProfileUpdate,
        hashed_password: str | None = None,
        actor_id: int | None = None,
    ) -> User:
        """本人プロフィールを更新する。

        Args:
            db: DBセッション。
            user: 更新対象ユーザー。
            user_in: 本人プロフィール更新リクエストの入力値。
            hashed_password: ハッシュ化されたパスワード。
            actor_id: 更新者ユーザーID。

        Returns:
            更新されたユーザー。
        """
        if user_in.name is not None:
            user.name = user_in.name
        if hashed_password is not None:
            user.hashed_password = hashed_password
        if actor_id is not None:
            user.updated_by = actor_id
        user.version += 1

        db.flush()
        return user

    def update_avatar_key(
        self,
        db: Session,
        *,
        user: User,
        avatar_key: str,
        actor_id: int | None = None,
    ) -> User:
        """ユーザーアイコンのS3キーを更新する。

        Args:
            db: DBセッション。
            user: 更新対象ユーザー。
            avatar_key: S3オブジェクトキー。
            actor_id: 更新者ユーザーID。

        Returns:
            更新されたユーザー。
        """
        user.avatar_key = avatar_key
        if actor_id is not None:
            user.updated_by = actor_id
        user.version += 1

        db.flush()
        return user

    def update_last_login_at(self, db: Session, user: User) -> User:
        """最終ログイン日時を更新する。

        Args:
            db: DBセッション。
            user: 更新対象ユーザー。

        Returns:
            更新されたユーザー。
        """
        user.last_login_at = datetime.now(UTC)
        db.flush()
        return user

    def soft_delete(self, db: Session, user: User) -> User:
        """ユーザーを論理削除する。

        Args:
            db: DBセッション。
            user: 削除対象ユーザー。

        Returns:
            論理削除されたユーザー。
        """
        user.deleted_at = datetime.now(UTC)
        db.flush()
        return user
