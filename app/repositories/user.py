"""
ユーザーRepositoryを定義するモジュール。

Userテーブルに対するCRUD操作を提供する。
"""

from sqlalchemy.orm import Session

from app.models.user import User
from app.schemas.user import UserCreate


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
        return db.query(User).filter(User.email == email).first()
