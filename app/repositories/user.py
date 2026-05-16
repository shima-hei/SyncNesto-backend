"""
ユーザーRepositoryを定義するモジュール。

Userテーブルに対するCRUD操作を提供する。
"""

from sqlalchemy.orm import Session

from app.models.user import User
from app.schemas.user import UserCreate, UserUpdate


class UserRepository:
    """Userテーブルへのデータアクセス処理を提供するRepository。"""

    def create(
        self,
        db: Session,
        user_in: UserCreate,
        hashed_password: str,
    ) -> User:
        """ユーザーを作成する。

        Args:
            db: DBセッション。
            user_in: ユーザー作成リクエストの入力値。
            hashed_password: ハッシュ化されたパスワード。

        Returns:
            作成されたユーザー。
        """
        user = User(
            email=user_in.email,
            name=user_in.name,
            hashed_password=hashed_password,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
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

    def update(
        self,
        db: Session,
        *,
        user: User,
        user_in: UserUpdate,
        hashed_password: str | None = None,
    ) -> User:
        """ユーザーを更新する。

        Args:
            db: DBセッション。
            user: 更新対象ユーザー。
            user_in: ユーザー更新リクエストの入力値。
            hashed_password: ハッシュ化されたパスワード。

        Returns:
            更新されたユーザー。
        """
        if user_in.email is not None:
            user.email = user_in.email
        if user_in.name is not None:
            user.name = user_in.name
        if hashed_password is not None:
            user.hashed_password = hashed_password

        db.commit()
        db.refresh(user)
        return user

    def soft_delete(self, db: Session, user: User) -> User:
        """ユーザーを論理削除する。

        Args:
            db: DBセッション。
            user: 削除対象ユーザー。

        Returns:
            論理削除されたユーザー。
        """
        from datetime import UTC, datetime

        user.deleted_at = datetime.now(UTC)
        db.commit()
        db.refresh(user)
        return user
