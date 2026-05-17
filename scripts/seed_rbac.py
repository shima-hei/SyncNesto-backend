"""RBAC初期データを作成するseedスクリプト。"""

import logging

from app.core.config import settings
from app.core.security import get_password_hash
from app.db.session import session_local
from app.models.user import User
from app.repositories.rbac import RbacRepository
from app.repositories.user import UserRepository


logger = logging.getLogger(__name__)


PERMISSIONS = [
    ("user:read", "ユーザーを閲覧する"),
    ("user:create", "ユーザーを作成する"),
    ("user:update", "ユーザーを更新する"),
    ("user:delete", "ユーザーを削除する"),
    ("project:read", "プロジェクトを閲覧する"),
    ("project:create", "プロジェクトを作成する"),
    ("project:update", "プロジェクトを更新する"),
    ("project:delete", "プロジェクトを削除する"),
    ("project:invite_member", "プロジェクトメンバーを招待する"),
    ("project:remove_member", "プロジェクトメンバーを削除する"),
    ("task:read", "タスクを閲覧する"),
    ("task:create", "タスクを作成する"),
    ("task:update", "タスクを更新する"),
    ("task:delete", "タスクを削除する"),
    ("test_plan:read", "テスト設計書を閲覧する"),
    ("test_plan:create", "テスト設計書を作成する"),
    ("test_plan:update", "テスト設計書を更新する"),
    ("test_plan:delete", "テスト設計書を削除する"),
    ("test_case:read", "テストケースを閲覧する"),
    ("test_case:create", "テストケースを作成する"),
    ("test_case:update", "テストケースを更新する"),
    ("test_case:delete", "テストケースを削除する"),
    ("test_case:execute", "テストケースを実行する"),
    ("document:read", "ドキュメントを閲覧する"),
    ("document:create", "ドキュメントを作成する"),
    ("document:update", "ドキュメントを更新する"),
    ("document:delete", "ドキュメントを削除する"),
]

ROLES = [
    ("system_admin", "システム管理者", "system", "システム全体の管理者"),
    ("project_admin", "プロジェクト管理者", "project", "プロジェクト管理者"),
    ("manager", "マネージャー", "project", "プロジェクト運用担当者"),
    ("member", "メンバー", "project", "通常メンバー"),
    ("viewer", "閲覧者", "project", "閲覧専用ユーザー"),
]

ROLE_PERMISSIONS = {
    "system_admin": [code for code, _ in PERMISSIONS],
    "project_admin": [
        "project:read",
        "project:update",
        "project:delete",
        "project:invite_member",
        "project:remove_member",
        "task:read",
        "task:create",
        "task:update",
        "task:delete",
        "test_plan:read",
        "test_plan:create",
        "test_plan:update",
        "test_plan:delete",
        "test_case:read",
        "test_case:create",
        "test_case:update",
        "test_case:delete",
        "test_case:execute",
        "document:read",
        "document:create",
        "document:update",
        "document:delete",
    ],
    "manager": [
        "project:read",
        "task:read",
        "task:create",
        "task:update",
        "task:delete",
        "test_plan:read",
        "test_plan:create",
        "test_plan:update",
        "test_case:read",
        "test_case:create",
        "test_case:update",
        "test_case:execute",
        "document:read",
        "document:create",
        "document:update",
    ],
    "member": [
        "project:read",
        "task:read",
        "task:create",
        "task:update",
        "test_plan:read",
        "test_plan:create",
        "test_plan:update",
        "test_case:read",
        "test_case:execute",
        "document:read",
        "document:create",
        "document:update",
    ],
    "viewer": [
        "project:read",
        "task:read",
        "test_plan:read",
        "test_case:read",
        "document:read",
    ],
}


def seed_roles_and_permissions(repository: RbacRepository) -> None:
    """ロール、権限、ロール権限を作成する。

    Args:
        repository: RBAC Repository。
    """
    with session_local() as db:
        permissions = {}
        for code, description in PERMISSIONS:
            permission = repository.get_permission_by_code(db, code)
            if permission is None:
                permission = repository.create_permission(
                    db,
                    code=code,
                    description=description,
                )
            permissions[code] = permission

        roles = {}
        for key, name, scope, description in ROLES:
            role = repository.get_role_by_key_scope(db, key=key, scope=scope)
            if role is None:
                role = repository.create_role(
                    db,
                    key=key,
                    name=name,
                    scope=scope,
                    description=description,
                )
            else:
                repository.update_role_display(
                    db,
                    role=role,
                    name=name,
                    description=description,
                )
            roles[key] = role

        for role_name, permission_codes in ROLE_PERMISSIONS.items():
            role = roles[role_name]
            for permission_code in permission_codes:
                repository.add_permission_to_role(
                    db,
                    role=role,
                    permission=permissions[permission_code],
                )

        db.commit()
        logger.info("RBAC roles and permissions seeded")


def seed_initial_admin(repository: RbacRepository) -> None:
    """環境変数から初期system_adminユーザーを作成する。

    Args:
        repository: RBAC Repository。

    Raises:
        RuntimeError: 初期管理者のemailまたはpasswordが未設定の場合。
    """
    if settings.initial_admin_email is None:
        raise RuntimeError("INITIAL_ADMIN_EMAIL is required")

    if settings.initial_admin_password is None:
        raise RuntimeError("INITIAL_ADMIN_PASSWORD is required")

    with session_local() as db:
        user_repository = UserRepository()
        user = user_repository.get_by_email(db, settings.initial_admin_email)
        if user is None:
            user = User(
                email=settings.initial_admin_email,
                name=settings.initial_admin_name,
                hashed_password=get_password_hash(settings.initial_admin_password),
                avatar_key=settings.default_avatar_key,
            )
            db.add(user)
            db.flush()
        elif user.avatar_key is None:
            user.avatar_key = settings.default_avatar_key

        role = repository.get_role_by_key_scope(
            db,
            key="system_admin",
            scope="system",
        )
        if role is None:
            raise RuntimeError("system_admin role is required")

        repository.assign_role_to_user(db, user=user, role=role)
        db.commit()
        logger.info("Initial system_admin ensured: email=%s", user.email)


def seed_rbac() -> None:
    """RBAC初期データと初期管理者ユーザーを作成する。"""
    repository = RbacRepository()
    seed_roles_and_permissions(repository)
    seed_initial_admin(repository)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    seed_rbac()
