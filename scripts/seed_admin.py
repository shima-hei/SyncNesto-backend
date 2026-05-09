"""初期管理者ユーザーを作成するseedスクリプト。"""

import logging

from app.core.config import settings
from app.core.security import get_password_hash
from app.db.session import session_local
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.user import UserCreate


logger = logging.getLogger(__name__)


def seed_initial_admin() -> None:
    """環境変数から初期管理者ユーザーを作成する。

    Raises:
        RuntimeError: 初期管理者のemailまたはpasswordが未設定の場合。
    """
    if settings.initial_admin_email is None:
        raise RuntimeError("INITIAL_ADMIN_EMAIL is required")

    if settings.initial_admin_password is None:
        raise RuntimeError("INITIAL_ADMIN_PASSWORD is required")

    repository = UserRepository()
    user_in = UserCreate(
        email=settings.initial_admin_email,
        name=settings.initial_admin_name,
        password=settings.initial_admin_password,
    )

    with session_local() as db:
        existing_user = repository.get_by_email(db, user_in.email)
        if existing_user is not None:
            if not existing_user.is_admin:
                existing_user.is_admin = True
                db.commit()
                logger.info("Existing user promoted to admin: email=%s", user_in.email)
            else:
                logger.info("Initial admin already exists: email=%s", user_in.email)
            return

        admin_user = User(
            email=user_in.email,
            name=user_in.name,
            hashed_password=get_password_hash(user_in.password),
            is_admin=True,
        )
        db.add(admin_user)
        db.commit()
        logger.info("Initial admin created: email=%s", user_in.email)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    seed_initial_admin()
