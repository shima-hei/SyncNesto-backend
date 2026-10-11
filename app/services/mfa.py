"""パスワード後の限定状態から追加認証を完了する。"""

import base64
import hmac
import io
import json
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pyotp
import qrcode
from cryptography.fernet import InvalidToken
from sqlalchemy.orm import Session
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url, options_to_json
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.core import error_messages
from app.core.config import settings
from app.core.exceptions import (
    EmailUnavailableError,
    ForbiddenError,
    MfaInvalidError,
    MfaUnavailableError,
)
from app.core.mfa import (
    FLOW_SECONDS,
    cipher,
    digest,
    fingerprint,
    require_recent,
    require_verified,
    webauthn_settings,
)
from app.core.security import verify_password
from app.models.mfa import MfaFactor, MfaFlow, MfaRecoveryRequest
from app.models.session import UserSession
from app.models.user import User
from app.repositories.mfa import MfaRepository
from app.schemas.mfa import MfaFactorRead, MfaFlowRead, MfaStatus, MfaTotpSetup
from app.services.audit_log import AuditLogService
from app.services.email import EmailService, OutgoingEmail
from app.services.session import SessionService


class MfaService:
    """本人のロックと一回限りの消費を一つのtransactionで確定する。"""

    def __init__(self) -> None:
        """既存の認証・監査を拡張する。"""
        self.repository = MfaRepository()
        self.audit = AuditLogService()

    def audit_event(
        self,
        db: Session,
        user: User,
        event: str,
        *,
        actor: int | None = None,
        metadata: dict | None = None,
    ) -> None:
        """認証変更と監査を原子的に保存する。"""
        self.audit.record(
            db,
            event_type=f"account.mfa.{event}",
            actor_user_id=actor or user.id,
            target_user_id=user.id,
            metadata=metadata,
            commit=False,
        )

    def start(
        self, db: Session, user: User, purpose: str, *, payload: dict | None = None
    ) -> tuple[str, MfaFlowRead]:
        """パスワードまたは通常セッションから短期Cookieを作る。"""
        token = secrets.token_urlsafe(48)
        self.repository.prune_flows(db, user.id)
        flow = MfaFlow(
            token_hash=digest(token),
            user_id=user.id,
            purpose=purpose,
            security_version=user.mfa_security_version,
            expires_at=datetime.now(UTC) + timedelta(seconds=FLOW_SECONDS),
            payload={**(payload or {}), "fingerprint": fingerprint(user)},
        )
        db.add(flow)
        db.flush()
        result = self.read(db, flow, user)
        db.commit()
        return token, result

    def login(self, db: Session, user: User) -> tuple[str | None, MfaFlowRead]:
        """復旧支援中はパスワードだけで初回登録へ進めない。"""
        locked = self.repository.user(db, user.id)
        assert locked is not None
        if locked.mfa_recovery_pending:
            return None, MfaFlowRead(next_step="mfa_recovery")
        return self.start(
            db, locked, "login" if self.repository.factors(db, locked.id) else "setup"
        )

    def flow(self, db: Session, token: str | None) -> tuple[MfaFlow, User]:
        """目的・期限・現在の認証情報と通常セッションを検証する。"""
        if not token or len(token) > 200:
            raise MfaInvalidError()
        flow = self.repository.flow(db, digest(token))
        if flow is None:
            raise MfaInvalidError()
        user = self.repository.user(db, flow.user_id)
        db.refresh(flow)
        if (
            user is None
            or not user.is_active
            or user.password_change_required
            or flow.consumed_at
            or flow.expires_at <= datetime.now(UTC)
            or flow.attempts >= 5
            or flow.security_version != user.mfa_security_version
            or not hmac.compare_digest(
                flow.payload.get("fingerprint", ""), fingerprint(user)
            )
            or (
                user.mfa_recovery_pending and flow.purpose not in {"recovery", "finish"}
            )
        ):
            raise MfaInvalidError()
        if "session_id" in flow.payload:
            session = self.repository.session(db, UUID(flow.payload["session_id"]))
            if (
                session is None
                or session.user_id != user.id
                or session.revoked_at
                or min(session.expires_at, session.absolute_expires_at)
                <= datetime.now(UTC)
            ):
                raise MfaInvalidError()
            require_verified(user, session)
            if flow.purpose == "manage":
                require_recent(session)
        return flow, user

    def read(self, db: Session, flow: MfaFlow, user: User) -> MfaFlowRead:
        """制限中に組織や個人プロフィールを返さない。"""
        return MfaFlowRead(
            next_step="recovery_codes"
            if flow.purpose == "finish"
            else "mfa_verify"
            if flow.purpose in {"login", "reauth"}
            else "mfa_setup",
            methods=sorted(
                {factor.kind for factor in self.repository.factors(db, user.id)}
            ),
            expires_at=flow.expires_at,
        )

    def fail(self, db: Session, flow: MfaFlow, user: User) -> None:
        """失敗回数をrollbackさせず、秘密を記録せず保存する。"""
        flow.attempts += 1
        if flow.attempts >= 5:
            flow.payload = {}
        self.audit_event(db, user, "verification_failed")
        db.commit()
        raise MfaInvalidError()

    def begin_totp(
        self, db: Session, flow: MfaFlow, user: User, name: str
    ) -> MfaTotpSetup:
        """登録用の秘密は限定状態内だけに暗号化保存する。"""
        self.require_enrollment(db, flow, user)
        secret = pyotp.random_base32()
        flow.payload = {
            "fingerprint": fingerprint(user),
            **{
                k: v
                for k, v in flow.payload.items()
                if k in {"session_id", "recovery_id"}
            },
            "ceremony": "totp_register",
            "name": name,
            "secret": cipher().encrypt(secret.encode()).decode(),
        }
        uri = pyotp.TOTP(secret).provisioning_uri(
            name=user.email, issuer_name="Syncnesto"
        )
        image = qrcode.make(uri)
        buffer = io.BytesIO()
        image.save(buffer, kind="PNG")
        db.commit()
        return MfaTotpSetup(
            provisioning_uri=uri,
            qr_data_url="data:image/png;base64,"
            + base64.b64encode(buffer.getvalue()).decode(),
        )

    def require_enrollment(self, db: Session, flow: MfaFlow, user: User) -> None:
        """初回登録と、直近認証済みの本人による追加だけを許可する。"""
        if (
            flow.purpose not in {"setup", "manage", "recovery"}
            or len(self.repository.factors(db, user.id)) >= 10
        ):
            raise MfaInvalidError()
        if flow.purpose in {"setup", "recovery"} and self.repository.factors(
            db, user.id
        ):
            raise MfaInvalidError()

    def totp_step(self, secret: str, code: str, last_step: int) -> int | None:
        """許容時間差内の未使用コードだけを受け付ける。"""
        if len(code) != 6 or not code.isascii() or not code.isdigit():
            return None
        current = int(datetime.now(UTC).timestamp()) // 30
        totp = pyotp.TOTP(secret)
        for step in (current, current - 1, current + 1):
            if step > last_step and hmac.compare_digest(totp.at(step * 30), code):
                return step
        return None

    def decrypt(self, encrypted: str) -> str:
        """復号失敗を秘密を含まない設定エラーへ変換する。"""
        try:
            return cipher().decrypt(encrypted.encode()).decode()
        except InvalidToken:
            raise MfaUnavailableError() from None

    def verify_code(
        self,
        db: Session,
        flow: MfaFlow,
        user: User,
        code: str,
        *,
        recovery: bool = False,
    ) -> tuple[MfaFlowRead, str | None]:
        """TOTPの初回登録・ログイン・復旧・再認証を用途別に検証する。"""
        if flow.purpose in {"setup", "manage", "recovery"}:
            self.require_enrollment(db, flow, user)
            if recovery or flow.payload.get("ceremony") != "totp_register":
                raise MfaInvalidError()
            encrypted = flow.payload.get("secret", "")
            step = self.totp_step(self.decrypt(encrypted), code, -1)
            if step is None:
                self.fail(db, flow, user)
            factor = MfaFactor(
                user_id=user.id,
                kind="totp",
                name=flow.payload["name"],
                secret=encrypted,
                last_step=step,
                verified_at=datetime.now(UTC),
            )
            db.add(factor)
            return self.enrolled(db, flow, user, "totp")
        if flow.purpose not in {"login", "reauth"}:
            raise MfaInvalidError()
        method = "totp"
        if recovery:
            if flow.purpose == "reauth":
                raise MfaInvalidError()
            item = self.repository.recovery_code(
                db, user.id, digest(code.replace("-", "").strip().upper())
            )
            if item is None:
                self.fail(db, flow, user)
            assert item is not None
            item.used_at = datetime.now(UTC)
            method = "recovery_code"
        else:
            matched = False
            for factor in self.repository.factors(db, user.id):
                if factor.kind != "totp" or not factor.secret:
                    continue
                step = self.totp_step(
                    self.decrypt(factor.secret), code, factor.last_step
                )
                if step is not None:
                    factor.last_step = step
                    factor.last_used_at = datetime.now(UTC)
                    matched = True
                    break
            if not matched:
                self.fail(db, flow, user)
        return self.verified(db, flow, user, method)

    def passkey_options(
        self, db: Session, flow: MfaFlow, user: User, *, name: str | None = None
    ) -> dict:
        """challengeを本人と目的に固定し、後から来た応答と照合する。"""
        rp_id, _ = webauthn_settings()
        challenge = secrets.token_bytes(32)
        factors = self.repository.factors(db, user.id)
        descriptors = [
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(factor.credential_id))
            for factor in factors
            if factor.kind == "passkey" and factor.credential_id
        ]
        if name is not None:
            self.require_enrollment(db, flow, user)
            options = generate_registration_options(
                rp_id=rp_id,
                rp_name="Syncnesto",
                user_name=user.email,
                user_id=user.mfa_user_handle.encode(),
                user_display_name=user.name,
                challenge=challenge,
                exclude_credentials=descriptors,
                authenticator_selection=AuthenticatorSelectionCriteria(
                    resident_key=ResidentKeyRequirement.REQUIRED,
                    user_verification=UserVerificationRequirement.REQUIRED,
                ),
            )
            ceremony = "passkey_register"
        else:
            if flow.purpose not in {"login", "reauth"} or not descriptors:
                raise MfaInvalidError()
            options = generate_authentication_options(
                rp_id=rp_id,
                challenge=challenge,
                allow_credentials=descriptors,
                user_verification=UserVerificationRequirement.REQUIRED,
            )
            ceremony = "passkey_authenticate"
        flow.payload = {
            k: v
            for k, v in flow.payload.items()
            if k in {"fingerprint", "session_id", "recovery_id"}
        }
        flow.payload = {
            **flow.payload,
            "challenge": bytes_to_base64url(challenge),
            "ceremony": ceremony,
            "name": name,
        }
        db.commit()
        return json.loads(options_to_json(options))

    def verify_passkey(
        self, db: Session, flow: MfaFlow, user: User, credential: dict
    ) -> tuple[MfaFlowRead, str | None]:
        """公開鍵の所有者と署名・origin・RP・UVを検証する。"""
        rp_id, origin = webauthn_settings()
        if len(json.dumps(credential)) > 100000 or not flow.payload.get("challenge"):
            self.fail(db, flow, user)
        try:
            challenge = base64url_to_bytes(flow.payload["challenge"])
            if flow.payload.get("ceremony") == "passkey_register":
                self.require_enrollment(db, flow, user)
                registration = verify_registration_response(
                    credential=credential,
                    expected_challenge=challenge,
                    expected_rp_id=rp_id,
                    expected_origin=origin,
                    require_user_verification=True,
                )
                credential_id = bytes_to_base64url(registration.credential_id)
                if self.repository.credential_exists(db, credential_id):
                    raise MfaInvalidError()
                db.add(
                    MfaFactor(
                        user_id=user.id,
                        kind="passkey",
                        name=flow.payload["name"],
                        credential_id=credential_id,
                        public_key=bytes_to_base64url(
                            registration.credential_public_key
                        ),
                        sign_count=registration.sign_count,
                        verified_at=datetime.now(UTC),
                    )
                )
                return self.enrolled(db, flow, user, "passkey")
            if (
                flow.purpose not in {"login", "reauth"}
                or flow.payload.get("ceremony") != "passkey_authenticate"
            ):
                raise MfaInvalidError()
            factor = next(
                (
                    item
                    for item in self.repository.factors(db, user.id)
                    if item.credential_id == credential.get("id")
                ),
                None,
            )
            if factor is None or not factor.public_key:
                raise MfaInvalidError()
            response = credential.get("response")
            if not isinstance(response, dict):
                raise MfaInvalidError()
            handle = response.get("userHandle")
            if handle and not hmac.compare_digest(
                base64url_to_bytes(handle), user.mfa_user_handle.encode()
            ):
                raise MfaInvalidError()
            result = verify_authentication_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=rp_id,
                expected_origin=origin,
                credential_public_key=base64url_to_bytes(factor.public_key),
                credential_current_sign_count=factor.sign_count,
                require_user_verification=True,
            )
            factor.sign_count = result.new_sign_count
            factor.last_used_at = datetime.now(UTC)
            return self.verified(db, flow, user, "passkey")
        except (WebAuthnException, ValueError, TypeError, KeyError, MfaInvalidError):
            self.fail(db, flow, user)
        raise MfaInvalidError()

    def codes(self, db: Session, user: User) -> list[str]:
        """128bitのコード10個を作り、ハッシュだけを保存する。"""
        values = [secrets.token_hex(16).upper() for _ in range(10)]
        self.repository.replace_codes(db, user.id, [digest(code) for code in values])
        return ["-".join(code[i : i + 8] for i in range(0, 32, 8)) for code in values]

    def enrolled(
        self, db: Session, flow: MfaFlow, user: User, method: str
    ) -> tuple[MfaFlowRead, str | None]:
        """初回だけ復旧コードの保存画面を挟む。通常セッションはまだ発行しない。"""
        self.audit_event(db, user, "factor_added", metadata={"method": method})
        if flow.purpose == "manage":
            flow.consumed_at = datetime.now(UTC)
            flow.payload = {}
            db.commit()
            self.notify(db, user, "追加の認証手段が登録されました。")
            return MfaFlowRead(next_step="authenticated"), None
        flow.purpose = "finish"
        flow.payload = {
            "fingerprint": fingerprint(user),
            "method": method,
            **{k: v for k, v in flow.payload.items() if k == "session_id"},
        }
        codes = self.codes(db, user)
        db.commit()
        self.notify(db, user, "追加認証の登録が確認されました。")
        return MfaFlowRead(
            next_step="recovery_codes", recovery_codes=codes, expires_at=flow.expires_at
        ), None

    def verified(
        self, db: Session, flow: MfaFlow, user: User, method: str
    ) -> tuple[MfaFlowRead, str | None]:
        """限定状態の消費と通常セッションの発行を同時に確定する。"""
        if flow.purpose == "reauth":
            session = self.repository.session(db, UUID(flow.payload["session_id"]))
            assert session is not None
            session.mfa_reauthenticated_at = datetime.now(UTC)
            session.mfa_method = method
            flow.consumed_at = datetime.now(UTC)
            flow.payload = {}
            self.audit_event(db, user, "reauthenticated")
            db.commit()
            return MfaFlowRead(next_step="authenticated"), None
        if flow.purpose == "finish":
            user.mfa_recovery_pending = False
        elif flow.purpose != "login":
            raise MfaInvalidError()
        flow.consumed_at = datetime.now(UTC)
        flow.payload = {}
        session, token = SessionService().create_session_token(
            db, user, mfa_method=method, commit=False
        )
        user.last_login_at = datetime.now(UTC)
        self.audit.record(
            db,
            event_type="auth.login.success",
            actor_user_id=user.id,
            target_user_id=user.id,
            metadata={"mfa_method": method, "session_id": str(session.id)},
            commit=False,
        )
        if method == "recovery_code":
            self.audit_event(db, user, "recovery_code_used")
        db.commit()
        if method == "recovery_code":
            self.notify(
                db,
                user,
                "復旧コードがログインに使用されました。予備の認証手段を確認してください。",
            )
        return MfaFlowRead(next_step="authenticated"), token

    def finish(
        self, db: Session, flow: MfaFlow, user: User
    ) -> tuple[MfaFlowRead, str | None]:
        """確認済み要素と復旧コードの保存確認後に利用を開始する。"""
        if flow.purpose != "finish" or not self.repository.factors(db, user.id):
            raise MfaInvalidError()
        return self.verified(db, flow, user, flow.payload["method"])

    def status(self, db: Session, user: User, session: UserSession) -> MfaStatus:
        """秘密を含めず、本人の認証状態だけを返す。"""
        recent = bool(
            session.mfa_reauthenticated_at
            and (datetime.now(UTC) - session.mfa_reauthenticated_at).total_seconds()
            <= 300
        )
        return MfaStatus(
            factors=[
                MfaFactorRead.model_validate(factor, from_attributes=True)
                for factor in self.repository.factors(db, user.id)
            ],
            recovery_codes_remaining=self.repository.remaining_codes(db, user.id),
            recent=recent,
        )

    def remove(
        self, db: Session, user: User, factor_id: UUID, session: UserSession
    ) -> None:
        """最後の認証手段を保護し、旧認証を全組織で失効する。"""
        locked = self.repository.user(db, user.id)
        if locked is None:
            raise MfaInvalidError()
        db.refresh(session)
        require_verified(locked, session)
        require_recent(session)
        if session.revoked_at:
            raise MfaInvalidError()
        user = locked
        factors = self.repository.factors(db, user.id)
        factor = next((item for item in factors if item.id == factor_id), None)
        if factor is None or len(factors) <= 1:
            raise ForbiddenError(error_messages.MFA_LAST_FACTOR)
        factor.revoked_at = datetime.now(UTC)
        factor.secret = None
        self.repository.invalidate(db, user)
        self.audit_event(db, user, "factor_removed", metadata={"method": factor.kind})
        db.commit()
        self.notify(
            db, user, "認証手段が削除され、既存のログインとCodex接続が失効しました。"
        )

    def recover(
        self, db: Session, token: str, password: str
    ) -> tuple[str, MfaFlowRead]:
        """運営者の承認済み要求とパスワードで一度だけ再登録へ進む。"""
        item = self.repository.recovery_request(db, digest(token))
        if item is None:
            raise MfaInvalidError()
        user = self.repository.user(db, item.user_id)
        db.refresh(item)
        if (
            user is None
            or not user.is_active
            or not user.mfa_recovery_pending
            or item.consumed_at
            or item.expires_at <= datetime.now(UTC)
            or item.security_version != user.mfa_security_version
            or not verify_password(password, user.hashed_password)
        ):
            raise MfaInvalidError()
        item.consumed_at = datetime.now(UTC)
        return self.start(db, user, "recovery", payload={"recovery_id": str(item.id)})

    def rename(
        self, db: Session, user: User, session: UserSession, factor_id: UUID, name: str
    ) -> None:
        """直近認証済み本人が自分の認証器の表示名だけを変更する。"""
        locked = self.repository.user(db, user.id)
        if locked is None:
            raise MfaInvalidError()
        db.refresh(session)
        require_verified(locked, session)
        require_recent(session)
        if session.revoked_at:
            raise MfaInvalidError()
        factor = next(
            (
                item
                for item in self.repository.factors(db, user.id)
                if item.id == factor_id
            ),
            None,
        )
        if factor is None:
            raise ForbiddenError()
        factor.name = name
        self.audit_event(
            db, locked, "factor_renamed", metadata={"factor_id": str(factor_id)}
        )
        db.commit()

    def approve_recovery(
        self, db: Session, actor: User, user_id: int, method: str, reason: str
    ) -> None:
        """MFA済み運営者の対面等の本人確認後に再登録要求を送る。"""
        if actor.id == user_id:
            raise ForbiddenError()
        user = self.repository.user(db, user_id)
        if user is None or not user.is_active or user.password_change_required:
            raise MfaInvalidError()
        email = EmailService()
        email.ensure_available()
        self.repository.invalidate(db, user, factors=True)
        user.mfa_recovery_pending = True
        token = secrets.token_urlsafe(48)
        item = MfaRecoveryRequest(
            user_id=user.id,
            actor_user_id=actor.id,
            verification_method=method,
            reason=reason,
            token_hash=digest(token),
            security_version=user.mfa_security_version,
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
        db.add(item)
        db.flush()
        self.audit_event(
            db,
            user,
            "recovery_approved",
            actor=actor.id,
            metadata={
                "verification_method": method,
                "reason": reason,
                "request_id": str(item.id),
            },
        )
        link = f"{settings.frontend_public_url.rstrip('/')}/mfa/recover#token={token}"
        email.send(
            OutgoingEmail(
                to=user.email,
                subject="Syncnesto: 追加認証の再登録",
                text=f"本人確認後に追加認証の再登録が許可されました。既存のログイン・Codex接続は失効します。30分以内にパスワードを確認して再登録してください。\n{link}\n心当たりがない場合は運営者へご連絡ください。",
                html="",
                idempotency_key=str(item.id),
            )
        )
        db.commit()

    def begin_management(
        self,
        db: Session,
        user: User,
        session: UserSession,
        *,
        reauthenticate: bool = False,
        password: str | None = None,
    ) -> tuple[str, MfaFlowRead]:
        """本人の直近認証またはパスワード再確認から目的を固定する。"""
        locked = self.repository.user(db, user.id)
        if locked is None:
            raise MfaInvalidError()
        db.refresh(session)
        require_verified(locked, session)
        if session.revoked_at:
            raise MfaInvalidError()
        if reauthenticate:
            if not password or not verify_password(password, locked.hashed_password):
                raise MfaInvalidError()
        else:
            require_recent(session)
        return self.start(
            db,
            locked,
            "setup"
            if reauthenticate
            and not settings.mfa_required
            and not self.repository.factors(db, locked.id)
            else "reauth"
            if reauthenticate
            else "manage",
            payload={"session_id": str(session.id)},
        )

    def regenerate_codes(
        self, db: Session, user: User, session: UserSession
    ) -> list[str]:
        """直近の追加認証後だけ旧復旧コードを破棄する。"""
        locked = self.repository.user(db, user.id)
        if locked is None:
            raise MfaInvalidError()
        db.refresh(session)
        require_verified(locked, session)
        require_recent(session)
        if session.revoked_at:
            raise MfaInvalidError()
        codes = self.codes(db, locked)
        self.audit_event(db, locked, "recovery_codes_regenerated")
        db.commit()
        self.notify(
            db, locked, "復旧コードが再発行されました。旧コードは使用できません。"
        )
        return codes

    def notify(self, db: Session, user: User, text: str) -> None:
        """通知失敗で認証を巻き戻さず、秘密を出さず運営へ記録する。"""
        try:
            EmailService().send(
                OutgoingEmail(
                    to=user.email,
                    subject="Syncnesto: 追加認証の変更",
                    text=text + "\n心当たりがない場合は運営者へご連絡ください。",
                    html="",
                    idempotency_key=secrets.token_hex(16),
                )
            )
        except EmailUnavailableError:
            self.audit_event(db, user, "notification_failed")
            db.commit()
