"""既存の業務モデルとRBACを使う一時組織の発行・失効・回収。"""

import hashlib
import hmac
import logging
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import (
    DemoLimitError,
    InvalidTokenError,
    NotFoundError,
    TokenExpiredError,
)
from app.core.security import create_access_token, get_password_hash
from app.models.demo import DemoOwnedUser, DemoSession, DemoUpload
from app.models.project import Project, ProjectMember
from app.models.rbac import Role
from app.models.requirement import Requirement, RequirementDocument
from app.models.session import UserSession
from app.models.task import Task
from app.models.tenant import Tenant, TenantMember
from app.models.test_design import TestDesign
from app.models.user import User
from app.repositories.demo import DemoRepository
from app.schemas.demo import DemoCleanupResult, DemoStatus
from app.services.storage import StorageService

logger = logging.getLogger(__name__)
IDLE_MINUTES = 15
ABSOLUTE_MINUTES = 60


class DemoService:
    """通常Identityには影響させず、一時環境の所有範囲だけを扱う。"""

    def __init__(self) -> None:
        """共有の所有経路とDBロックを利用する。"""
        self.repository = DemoRepository()

    def start(self, db: Session, client_ip: str) -> tuple[DemoStatus, str]:
        """既存セッションの上書きはRouterで拒否し、全発行を一度に確定する。"""
        if not settings.demo_mode:
            raise NotFoundError()
        now = datetime.now(UTC)
        self.repository.lock_admission(db)
        digest = hmac.new(
            settings.secret_key.encode(), client_ip.encode(), hashlib.sha256
        ).hexdigest()
        if not self.repository.consume_start(db, digest, now):
            db.rollback()
            raise DemoLimitError()
        # 上限に達した試行も回数に含め、匿名の回収連打を制限する。
        db.commit()
        due = self.repository.due_ids(db, now, limit=2)
        db.rollback()
        for old_id in due:
            self.cleanup(old_id)
        self.repository.lock_admission(db)
        if self.repository.unfinished_count(db) >= 10:
            db.rollback()
            raise DemoLimitError()
        demo_id = uuid4()
        tenant = Tenant(name="体験用ワークスペース", slug=f"demo-{demo_id.hex}")
        user = User(
            email=f"visitor-{demo_id.hex}@demo.syncnesto.example.com",
            name="デモ利用者",
            hashed_password=get_password_hash(secrets.token_urlsafe(32)),
        )
        db.add_all([tenant, user])
        db.flush()
        tenant_role = db.scalar(
            select(Role).where(Role.key == "tenant_owner", Role.scope == "tenant")
        )
        project_role = db.scalar(
            select(Role).where(Role.key == "project_admin", Role.scope == "project")
        )
        if tenant_role is None or project_role is None:
            raise RuntimeError("Demo requires seeded tenant and project roles")
        expires = now + timedelta(minutes=IDLE_MINUTES)
        absolute = now + timedelta(minutes=ABSOLUTE_MINUTES)
        session = UserSession(
            user_id=user.id,
            started_at=now,
            last_seen_at=now,
            expires_at=expires,
            absolute_expires_at=absolute,
        )
        db.add_all(
            [
                session,
                TenantMember(
                    tenant_id=tenant.id,
                    user_id=user.id,
                    role_id=tenant_role.id,
                    display_name=user.name,
                ),
            ]
        )
        project = Project(
            tenant_id=tenant.id,
            project_code="DEMO-001",
            name="サービス改善プロジェクト",
            description="要件・タスク・テストを自由に編集して体験できます。",
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(project)
        db.flush()
        demo = DemoSession(
            id=demo_id,
            tenant_id=tenant.id,
            user_id=user.id,
            session_id=session.id,
            created_at=now,
            expires_at=expires,
            absolute_expires_at=absolute,
            cleanup_after=now,
        )
        db.add(demo)
        db.flush()
        db.add_all(
            [
                DemoOwnedUser(user_id=user.id, demo_id=demo.id),
                ProjectMember(
                    project_id=project.id, user_id=user.id, role_id=project_role.id
                ),
                Task(
                    project_id=project.id,
                    task_code="TASK-001",
                    title="最初のタスクを編集してみる",
                    description="担当者や期限を変更し、コメントを追加できます。",
                    assignee_id=user.id,
                    reporter_id=user.id,
                    created_by=user.id,
                    updated_by=user.id,
                ),
                TestDesign(
                    project_id=project.id,
                    name="ログイン機能のテスト",
                    description="因子・水準を追加し、テストパターンを作成してみましょう。",
                    created_by=user.id,
                    updated_by=user.id,
                ),
            ]
        )
        document = RequirementDocument(
            project_id=project.id,
            document_code="REQDOC-001",
            title="サービス改善の要件",
            purpose="要件からタスク・テストへつなぐ流れを体験する",
            author_id=user.id,
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(document)
        db.flush()
        db.add(
            Requirement(
                document_id=document.id,
                requirement_code="REQ-001",
                requirement_type="functional",
                title="利用者が業務の進捗を把握できる",
                description="要件・タスク・テストの情報を一つのワークスペースで管理する。",
                created_by=user.id,
                updated_by=user.id,
            )
        )
        from app.repositories.document import DocumentRepository

        DocumentRepository().create(
            db,
            project.id,
            "チームの作業ガイド",
            "# チームの作業ガイド\n\n本文を編集し、版履歴を確認してみましょう。\n"
            "要件・タスク・テスト設計を関連付け、PDFや画像を添付できます。\n\n"
            "入力内容はデモ終了時に破棄されます。実際の機密情報は入力しないでください。",
            user.id,
        )
        db.commit()
        return self.status(demo), create_access_token(
            subject=user.email, session_id=session.id, expires_at=expires
        )

    def status(self, demo: DemoSession) -> DemoStatus:
        """利用中に必要な情報だけを返す。"""
        assert demo.tenant_id is not None
        return DemoStatus(
            id=demo.id,
            tenant_id=demo.tenant_id,
            expires_at=demo.expires_at,
            absolute_expires_at=demo.absolute_expires_at,
        )

    def bind(self, db: Session, session: UserSession) -> None:
        """署名・セッション検証後、Identityと組織の制限を結び付ける。"""
        demo = self.repository.by_session(db, session.id)
        if demo is None:
            if db.get(DemoOwnedUser, session.user_id) is not None:
                raise InvalidTokenError()
            return
        if not settings.demo_mode or demo.status != "active":
            raise InvalidTokenError()
        if demo.expires_at <= datetime.now(UTC):
            self.revoke(db, demo.id, "expired")
            raise TokenExpiredError()
        db.info.update(
            demo_id=demo.id, tenant_id=demo.tenant_id, demo_user_id=demo.user_id
        )

    def revoke(self, db: Session, demo_id: UUID, reason: str) -> None:
        """削除成否とは独立して、失効と回収待ちを同じtransactionで確定する。"""
        demo = self.repository.lock(db, demo_id)
        if demo is None or demo.status != "active":
            return
        now = datetime.now(UTC)
        demo.status = "cleanup_pending"
        demo.revoked_at = now
        demo.revoked_reason = reason
        demo.cleanup_after = now
        session = db.get(UserSession, demo.session_id)
        if session is not None:
            session.revoked_at = now
            session.revoked_reason = reason
        # この操作は一時環境の通常書込検証より先に確定する。
        db.info.pop("demo_id", None)
        db.commit()

    def reserve_upload(
        self,
        db: Session,
        key: str,
        byte_size: int,
        expires_at: datetime,
        upload_id: UUID | None = None,
    ) -> None:
        """URL発行やS3書込より先に、容量・件数と終了時刻をDBへ確定する。"""
        demo_id = db.info.get("demo_id")
        if demo_id is None:
            return
        demo = self.repository.lock(db, demo_id)
        if (
            demo is None
            or demo.status != "active"
            or demo.expires_at <= datetime.now(UTC)
        ):
            raise TokenExpiredError()
        count, size = db.execute(
            select(
                func.count(), func.coalesce(func.sum(DemoUpload.byte_size), 0)
            ).where(DemoUpload.demo_id == demo_id)
        ).one()
        existing = db.scalar(select(DemoUpload).where(DemoUpload.key == key))
        reservation = db.info.get("demo_upload_reservation")
        if existing is None and reservation is not None:
            existing = db.get(DemoUpload, reservation)
            if existing is None or existing.demo_id != demo_id:
                raise InvalidTokenError()
            existing.key = key
            db.info.pop("demo_upload_reservation", None)
        if existing is None:
            if (
                byte_size > 5 * 1024 * 1024
                or count >= 10
                or size + byte_size > 20 * 1024 * 1024
            ):
                raise DemoLimitError()
            db.add(
                DemoUpload(
                    id=upload_id or uuid4(),
                    demo_id=demo_id,
                    key=key,
                    byte_size=byte_size,
                    expires_at=expires_at,
                )
            )
        else:
            if size - existing.byte_size + byte_size > 20 * 1024 * 1024:
                raise DemoLimitError()
            existing.byte_size = byte_size
            existing.expires_at = max(existing.expires_at, expires_at)
        db.commit()

    def cleanup(self, demo_id: UUID, storage: StorageService | None = None) -> bool:
        """接続障害を含め、回収失敗で確定済みのログアウトを失敗にしない。"""
        try:
            return self._cleanup(demo_id, storage)
        except Exception:
            logger.warning("Demo cleanup connection unavailable; retry remains pending")
            return False

    def _cleanup(self, demo_id: UUID, storage: StorageService | None = None) -> bool:
        """DB行ロックをleaseとして使い、障害時にはconnection終了で解放する。"""
        from app.db.session import session_local

        with session_local() as db:
            demo = db.scalar(
                select(DemoSession)
                .where(DemoSession.id == demo_id)
                .with_for_update(skip_locked=True)
            )
            if demo is None or demo.status == "cleaned":
                return True
            if demo.status == "active":
                if demo.expires_at > datetime.now(UTC):
                    return False
                self.revoke(db, demo.id, "expired")
                demo = self.repository.lock(db, demo_id)
                assert demo is not None
            try:
                uploads = list(
                    db.scalars(select(DemoUpload).where(DemoUpload.demo_id == demo_id))
                )
                if uploads:
                    (storage or StorageService()).delete_prefix(f"demo/{demo_id}/")
                latest_put = max(
                    (u.expires_at for u in uploads), default=datetime.now(UTC)
                )
                self.repository.delete_business_data(db, demo)
                demo.cleanup_attempts += 1
                demo.cleanup_error = None
                # PUTの有効期間中は上書きによる再出現を最終sweepで回収する。
                if latest_put + timedelta(seconds=60) > datetime.now(UTC) and uploads:
                    demo.cleanup_after = latest_put + timedelta(seconds=60)
                else:
                    db.execute(delete(DemoUpload).where(DemoUpload.demo_id == demo_id))
                    demo.status = "cleaned"
                    demo.cleaned_at = datetime.now(UTC)
                db.commit()
                return demo.status == "cleaned"
            except Exception:
                db.rollback()
                demo = self.repository.lock(db, demo_id)
                if demo is not None:
                    demo.cleanup_attempts += 1
                    demo.cleanup_error = "cleanup_failed"
                    demo.cleanup_after = datetime.now(UTC) + timedelta(
                        minutes=min(60, 2 ** min(demo.cleanup_attempts, 6))
                    )
                    db.commit()
                logger.warning("Demo cleanup requires retry")
                return False

    def sweep(self) -> DemoCleanupResult:
        """公開Cookieを受け付けないCron経由で期限切れと失敗を回収する。"""
        from app.db.session import session_local

        with session_local() as db:
            ids = self.repository.due_ids(db, datetime.now(UTC))
        completed = sum(self.cleanup(demo_id) for demo_id in ids)
        with session_local() as db:
            self.repository.prune_receipts(db)
        return DemoCleanupResult(processed=len(ids), pending=len(ids) - completed)
