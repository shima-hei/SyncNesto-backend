"""
ユーザー関連のサービス層を定義するモジュール。

ユーザーの作成や取得におけるビジネスロジックを実装し、
Repository層を通じてデータアクセスを行う。
パスワードのハッシュ化や重複チェックなどの処理を担当する。
"""

import logging

from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.config import settings
from app.core.exceptions import (
    BadRequestError,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    NotFoundError,
)
from app.core.security import get_password_hash, verify_password
from app.models.rbac import Role
from app.models.user import User
from app.repositories.rbac import RbacRepository
from app.repositories.user import UserRepository
from app.schemas.user import UserCreate, UserProfileUpdate, UserRead, UserUpdate
from app.services.audit_log import AuditEventType, AuditLogService
from app.services.conflict import raise_if_version_conflict
from app.services.login_attempt import LoginAttemptService
from app.services.session import SessionService
from app.services.storage import StorageService

logger = logging.getLogger(__name__)


class UserService:
    """
    ユーザーに関するビジネスロジックを提供する。

    Repository層を利用してデータアクセスを行い、
    ユーザー作成時のバリデーションなどを担う。
    """

    def __init__(
        self,
        repository: UserRepository | None = None,
        rbac_repository: RbacRepository | None = None,
        login_attempt_service: LoginAttemptService | None = None,
        session_service: SessionService | None = None,
        audit_log_service: AuditLogService | None = None,
    ) -> None:
        """UserServiceを初期化する。

        Args:
            repository: ユーザーRepository。
            rbac_repository: RBAC Repository。
            login_attempt_service: ログイン試行回数サービス。
            session_service: 認証セッションサービス。
            audit_log_service: 監査ログサービス。
        """
        self.repository = repository or UserRepository()
        self.rbac_repository = rbac_repository or RbacRepository()
        self.login_attempt_service = login_attempt_service or LoginAttemptService()
        self.session_service = session_service or SessionService()
        self.audit_log_service = audit_log_service or AuditLogService()

    def _resolve_system_roles(self, db: Session, role_keys: list[str]) -> list[Role]:
        """システムロールkey一覧からロール一覧を取得する。

        Args:
            db: DBセッション。
            role_keys: システムロールkey一覧。

        Returns:
            システムロール一覧。

        Raises:
            BadRequestError: 存在しないシステムロールkeyが指定された場合。
        """
        roles = []
        for role_key in dict.fromkeys(role_keys):
            role = self.rbac_repository.get_role_by_key_scope(
                db,
                key=role_key,
                scope="system",
            )
            if role is None:
                raise BadRequestError(
                    error_messages.INVALID_SYSTEM_ROLE_KEY.format(role_key=role_key)
                )
            roles.append(role)

        return roles

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
        roles = self._resolve_system_roles(db, user_in.system_role_keys)
        self.rbac_repository.replace_system_roles_for_user(db, user=user, roles=roles)
        db.commit()
        db.refresh(user)
        self.audit_log_service.record(
            db,
            event_type=AuditEventType.USER_CREATED,
            actor_user_id=actor_id,
            target_user_id=user.id,
            resource_type="user",
            resource_id=user.id,
            metadata={"email": user.email},
        )
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

    def list_users_by_ids(self, db: Session, user_ids: list[int]) -> list[User]:
        """指定ID一覧のユーザーを取得する。

        Args:
            db: DBセッション。
            user_ids: ユーザーID一覧。

        Returns:
            ユーザー一覧。
        """
        return self.repository.list_by_ids(db, user_ids)

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

    def list_system_roles_by_user_ids(
        self,
        db: Session,
        user_ids: list[int],
    ) -> dict[int, list[Role]]:
        """複数ユーザーのシステムロール一覧を取得する。

        Args:
            db: DBセッション。
            user_ids: ユーザーID一覧。

        Returns:
            ユーザーIDをkey、システムロール一覧をvalueにした辞書。
        """
        return self.rbac_repository.list_system_roles_by_user_ids(db, user_ids)

    def list_system_roles_by_user(self, db: Session, user_id: int) -> list[Role]:
        """ユーザーのシステムロール一覧を取得する。

        Args:
            db: DBセッション。
            user_id: ユーザーID。

        Returns:
            システムロール一覧。
        """
        return self.rbac_repository.list_system_roles_by_user(db, user_id)

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
            raise NotFoundError(error_messages.USER_NOT_FOUND)

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
        before_role_keys = [
            role.key
            for role in self.rbac_repository.list_system_roles_by_user(db, user.id)
        ]
        raise_if_version_conflict(
            current_version=user.version,
            requested_version=user_in.version,
            current=UserRead.model_validate(user).model_dump(),
        )

        if user_in.email is not None and user_in.email != user.email:
            existing_user = self.repository.get_by_email(db, user_in.email)
            if existing_user is not None:
                raise EmailAlreadyRegisteredError()

        hashed_password = None
        if user_in.password is not None:
            hashed_password = get_password_hash(user_in.password)

        user = self.repository.update(
            db,
            user=user,
            user_in=user_in,
            hashed_password=hashed_password,
            actor_id=actor_id,
        )
        should_revoke_sessions = "system_role_keys" in user_in.model_fields_set
        if should_revoke_sessions:
            roles = self._resolve_system_roles(db, user_in.system_role_keys or [])
            self.rbac_repository.replace_system_roles_for_user(
                db,
                user=user,
                roles=roles,
            )

        db.commit()
        db.refresh(user)
        if should_revoke_sessions:
            after_role_keys = [
                role.key
                for role in self.rbac_repository.list_system_roles_by_user(db, user.id)
            ]
            self.audit_log_service.record(
                db,
                event_type=AuditEventType.USER_SYSTEM_ROLES_CHANGED,
                actor_user_id=actor_id,
                target_user_id=user.id,
                resource_type="user",
                resource_id=user.id,
                metadata={
                    "before_role_keys": before_role_keys,
                    "after_role_keys": after_role_keys,
                },
            )
            self.session_service.revoke_user_sessions(
                db,
                user_id=user.id,
                actor_user_id=actor_id,
            )
        else:
            self.audit_log_service.record(
                db,
                event_type=AuditEventType.USER_UPDATED,
                actor_user_id=actor_id,
                target_user_id=user.id,
                resource_type="user",
                resource_id=user.id,
                metadata={"updated_fields": sorted(user_in.model_fields_set)},
            )

        return user

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
        raise_if_version_conflict(
            current_version=current_user.version,
            requested_version=user_in.version,
            current=UserRead.model_validate(current_user).model_dump(),
        )

        hashed_password = None
        if user_in.password is not None:
            hashed_password = get_password_hash(user_in.password)

        user = self.repository.update_profile(
            db,
            user=current_user,
            user_in=user_in,
            hashed_password=hashed_password,
            actor_id=current_user.id,
        )
        db.commit()
        db.refresh(user)
        return user

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
        user = self.repository.update_avatar_key(
            db,
            user=current_user,
            avatar_key=avatar_key,
            actor_id=current_user.id,
        )
        db.commit()
        db.refresh(user)
        return user

    def delete_avatar(
        self,
        db: Session,
        *,
        current_user: User,
        storage_service: StorageService,
    ) -> User:
        """本人のユーザーアイコンを削除してデフォルト画像に戻す。

        DB更新を先に確定し、その後にユーザー固有のS3オブジェクト削除を試みる。
        S3削除に失敗しても、DB上のデフォルト画像への復帰は維持する。

        Args:
            db: DBセッション。
            current_user: 認証済みユーザー。
            storage_service: ストレージサービス。

        Returns:
            更新されたユーザー。
        """
        previous_avatar_key = current_user.avatar_key
        if previous_avatar_key == settings.default_avatar_key:
            return current_user

        user = self.repository.update_avatar_key(
            db,
            user=current_user,
            avatar_key=settings.default_avatar_key,
            actor_id=current_user.id,
        )
        db.commit()
        db.refresh(user)

        if previous_avatar_key is not None:
            try:
                storage_service.delete_object(previous_avatar_key)
            except Exception:
                logger.exception(
                    "Failed to delete user avatar from S3: user_id=%s key=%s",
                    user.id,
                    previous_avatar_key,
                )

        return user

    def delete_user(
        self,
        db: Session,
        user_id: int,
        actor_id: int | None = None,
    ) -> None:
        """ユーザーを論理削除する。

        Args:
            db: DBセッション。
            user_id: 削除対象ユーザーID。
            actor_id: 操作ユーザーID。

        Raises:
            NotFoundError: ユーザーが存在しない場合。
        """
        user = self.get_user(db, user_id)
        self.repository.soft_delete(db, user)
        db.commit()
        self.audit_log_service.record(
            db,
            event_type=AuditEventType.USER_DELETED,
            actor_user_id=actor_id,
            target_user_id=user.id,
            resource_type="user",
            resource_id=user.id,
            metadata={"email": user.email},
        )

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
        normalized_email = self.login_attempt_service.normalize_email(email)
        if self.login_attempt_service.is_locked(db, normalized_email):
            self.audit_log_service.record(
                db,
                event_type=AuditEventType.AUTH_LOGIN_FAILURE,
                metadata={"email": normalized_email, "reason": "locked"},
            )
            logger.warning("Locked login attempt: email=%s", normalized_email)
            raise InvalidCredentialsError()

        user = self.repository.get_by_email(db, email)
        if user is None:
            self.login_attempt_service.record_failure(db, normalized_email)
            self.audit_log_service.record(
                db,
                event_type=AuditEventType.AUTH_LOGIN_FAILURE,
                metadata={"email": normalized_email, "reason": "unknown_email"},
            )
            logger.warning("Invalid login attempt: email=%s", normalized_email)
            raise InvalidCredentialsError()

        if not user.is_active:
            self.login_attempt_service.record_failure(db, normalized_email)
            self.audit_log_service.record(
                db,
                event_type=AuditEventType.AUTH_LOGIN_FAILURE,
                target_user_id=user.id,
                resource_type="user",
                resource_id=user.id,
                metadata={"email": normalized_email, "reason": "inactive_user"},
            )
            logger.warning(
                "Inactive user login attempt: id=%s email=%s",
                user.id,
                normalized_email,
            )
            raise InvalidCredentialsError()

        if not verify_password(password, user.hashed_password):
            self.login_attempt_service.record_failure(db, normalized_email)
            self.audit_log_service.record(
                db,
                event_type=AuditEventType.AUTH_LOGIN_FAILURE,
                target_user_id=user.id,
                resource_type="user",
                resource_id=user.id,
                metadata={"email": normalized_email, "reason": "wrong_password"},
            )
            logger.warning("Invalid login attempt: email=%s", normalized_email)
            raise InvalidCredentialsError()

        self.login_attempt_service.reset(db, normalized_email)
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
        user = self.repository.update_last_login_at(db, user)
        db.commit()
        db.refresh(user)
        return user
