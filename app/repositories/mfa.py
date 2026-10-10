"""追加認証の所有者・期限・並行使用をDBで管理する。"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.mcp import McpConnection
from app.models.mfa import MfaFactor, MfaFlow, MfaRecoveryCode, MfaRecoveryRequest
from app.models.session import UserSession
from app.models.user import User


class MfaRepository:
    """認証のtransactionは呼出し元Serviceで確定する。"""

    def user(self, db: Session, user_id: int) -> User | None:
        """Identityをロックし、要素変更と認証を直列化する。"""
        return (
            db.query(User)
            .filter(User.id == user_id)
            .populate_existing()
            .with_for_update()
            .first()
        )

    def flow(self, db: Session, token_hash: str) -> MfaFlow | None:
        """Cookieのハッシュに対応する限定状態を取得する。"""
        return db.query(MfaFlow).filter(MfaFlow.token_hash == token_hash).first()

    def factors(self, db: Session, user_id: int) -> list[MfaFactor]:
        """本人の有効な認証手段だけを返す。"""
        return (
            db.query(MfaFactor)
            .filter(MfaFactor.user_id == user_id, MfaFactor.revoked_at.is_(None))
            .order_by(MfaFactor.verified_at)
            .all()
        )

    def recovery_code(
        self, db: Session, user_id: int, digest: str
    ) -> MfaRecoveryCode | None:
        """本人の未使用コードを取得する。呼出し元はIdentityをロックする。"""
        return (
            db.query(MfaRecoveryCode)
            .filter(
                MfaRecoveryCode.user_id == user_id,
                MfaRecoveryCode.code_hash == digest,
                MfaRecoveryCode.used_at.is_(None),
            )
            .first()
        )

    def recovery_request(self, db: Session, digest: str) -> MfaRecoveryRequest | None:
        """ハッシュに対応する復旧申請を取得する。"""
        return (
            db.query(MfaRecoveryRequest)
            .filter(MfaRecoveryRequest.token_hash == digest)
            .first()
        )

    def session(self, db: Session, session_id: UUID) -> UserSession | None:
        """通常セッションを取得する。"""
        return db.get(UserSession, session_id)

    def invalidate(self, db: Session, user: User, *, factors: bool = False) -> None:
        """Identity全体の旧認証を失効する。組織の所属は変更しない。"""
        now = datetime.now(UTC)
        user.mfa_security_version += 1
        db.query(UserSession).filter(
            UserSession.user_id == user.id, UserSession.revoked_at.is_(None)
        ).update({"revoked_at": now, "revoked_reason": "mfa_changed"})
        db.query(McpConnection).filter(
            McpConnection.user_id == user.id, McpConnection.revoked_at.is_(None)
        ).update({"revoked_at": now})
        db.query(MfaFlow).filter(
            MfaFlow.user_id == user.id, MfaFlow.consumed_at.is_(None)
        ).update({"consumed_at": now, "payload": {}})
        if factors:
            db.query(MfaFactor).filter(
                MfaFactor.user_id == user.id, MfaFactor.revoked_at.is_(None)
            ).update({"revoked_at": now, "secret": None})
            db.query(MfaRecoveryCode).filter(
                MfaRecoveryCode.user_id == user.id
            ).delete()

    def replace_codes(self, db: Session, user_id: int, hashes: list[str]) -> None:
        """旧コードを破棄して新しいハッシュだけを追加する。"""
        db.query(MfaRecoveryCode).filter(MfaRecoveryCode.user_id == user_id).delete()
        db.add_all(
            MfaRecoveryCode(user_id=user_id, code_hash=value) for value in hashes
        )

    def credential_exists(self, db: Session, credential_id: str) -> bool:
        """削除済みを含め、同じ認証器の別ユーザー登録を拒否する。"""
        return (
            db.query(MfaFactor).filter(MfaFactor.credential_id == credential_id).first()
            is not None
        )

    def remaining_codes(self, db: Session, user_id: int) -> int:
        """未使用の復旧コードの数だけを返す。"""
        return (
            db.query(MfaRecoveryCode)
            .filter(
                MfaRecoveryCode.user_id == user_id, MfaRecoveryCode.used_at.is_(None)
            )
            .count()
        )

    def cancel(self, db: Session, token_hash: str) -> None:
        """ログアウトで限定状態を失効する。"""
        flow = self.flow(db, token_hash)
        if flow:
            self.user(db, flow.user_id)
            db.refresh(flow)
            flow.consumed_at = datetime.now(UTC)
            flow.payload = {}
            db.commit()

    def prune_flows(self, db: Session, user_id: int) -> None:
        """期限切れ・使用済みの限定状態を次の発行時に破棄する。"""
        now = datetime.now(UTC)
        db.query(MfaFlow).filter(
            MfaFlow.user_id == user_id,
            (MfaFlow.expires_at <= now) | MfaFlow.consumed_at.is_not(None),
        ).delete(synchronize_session=False)
