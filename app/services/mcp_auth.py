"""既存IdentityとProject権限を再利用したOAuth委任。"""

import base64
import hashlib
import hmac
import re
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.mcp import (
    CLIENT_ID,
    SCOPES,
    McpOAuthError,
    api_audience,
    api_signing_key,
    digest,
    oauth_issuer,
)
from app.models.mcp import McpAuthorizationRequest, McpConnection, McpCredential
from app.models.user import User
from app.repositories.mcp import McpRepository
from app.schemas.mcp import McpConsentRead, McpProjectChoice
from app.services.audit_log import AuditLogService
from app.services.authorization import AuthorizationService

EDIT_PERMISSIONS = (
    "requirement:create",
    "requirement:update",
    "requirement:comment",
    "test_plan:create",
    "test_plan:update",
    "test_plan:comment",
    "task:create",
    "task:update",
    "task:comment",
)


class McpAuthService:
    """接続を本人・組織・Projectと現在の権限に結び付ける。"""

    def __init__(self) -> None:
        """既存のRBACを利用する。"""
        self.repository = McpRepository()
        self.authorization = AuthorizationService()

    def fingerprint(self, user: User) -> str:
        """認証情報の変更時に既存接続を失効させる。"""
        return digest(
            f"{user.id}:{user.email}:{user.hashed_password}:{user.password_change_required}"
        )

    def validate_browser(self, db: Session, user: User) -> None:
        """デモのCookieから通常DBへの委任を作らない。"""
        if (
            db.info.get("demo_id")
            or db.info.get("data_realm") == "demo"
            or not user.is_active
            or user.password_change_required
        ):
            raise ForbiddenError()

    def eligible(self, db: Session, user: User, project_id: int) -> bool:
        """実行専用を除き、現在のProject権限を確認する。"""
        return self.authorization.has_project_permission(
            db, user=user, project_id=project_id, permission_code="mcp:connect"
        ) and any(
            self.authorization.has_project_permission(
                db, user=user, project_id=project_id, permission_code=permission
            )
            for permission in EDIT_PERMISSIONS
        )

    def start(self, db: Session, params: dict[str, str]) -> str:
        """検証済み要求から同意画面へのURLを作る。"""
        try:
            redirect = urlsplit(params.get("redirect_uri", ""))
            valid = (
                redirect.scheme == "http"
                and redirect.hostname == "127.0.0.1"
                and redirect.port is not None
                and 1 <= redirect.port <= 65535
                and re.fullmatch(r"/callback(?:/[A-Za-z0-9_-]{1,100})?", redirect.path)
                and not (
                    redirect.query
                    or redirect.fragment
                    or redirect.username
                    or redirect.password
                )
            )
        except ValueError:
            valid = False
        scope = params.get("scope", "mcp:work").split()
        if (
            params.get("client_id") != CLIENT_ID
            or params.get("response_type") != "code"
            or not valid
            or params.get("resource") != settings.mcp_resource_url
            or params.get("code_challenge_method") != "S256"
            or not re.fullmatch(r"[A-Za-z0-9_-]{43}", params.get("code_challenge", ""))
            or not 1 <= len(params.get("state", "")) <= 500
            or set(scope) != set(SCOPES)
        ):
            raise McpOAuthError("invalid_request")
        request = McpAuthorizationRequest(
            client_id=CLIENT_ID,
            redirect_uri=params["redirect_uri"],
            resource=params["resource"],
            scopes=SCOPES,
            state=params["state"],
            code_challenge=params["code_challenge"],
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        db.add(request)
        db.commit()
        return (
            settings.frontend_public_url.rstrip("/")
            + "/mcp/authorize?"
            + urlencode({"request_id": str(request.id)})
        )

    def pending(self, db: Session, request_id: UUID, *, lock: bool = False):
        """期限内の未承認要求だけを返す。"""
        request = self.repository.request(db, request_id, lock=lock)
        if (
            request is None
            or request.expires_at <= datetime.now(UTC)
            or request.connection_id is not None
            or request.consumed_at is not None
        ):
            raise NotFoundError()
        return request

    def consent(self, db: Session, user: User, request_id: UUID) -> McpConsentRead:
        """各候補Projectで現在の所属と権限を検証する。"""
        self.validate_browser(db, user)
        request = self.pending(db, request_id)
        rows = self.repository.consent_projects(db, user.id)
        previous, choices = dict(db.info), []
        try:
            for project, tenant in rows:
                db.info["tenant_id"] = tenant.id
                if self.eligible(db, user, project.id):
                    choices.append(
                        McpProjectChoice(
                            id=project.id,
                            name=project.name,
                            tenant_id=tenant.id,
                            tenant_name=tenant.name,
                        )
                    )
        finally:
            db.info.clear()
            db.info.update(previous)
        return McpConsentRead(
            request_id=request.id,
            client_name="Codex（ローカルMCP）",
            scopes=request.scopes,
            projects=choices,
            expires_at=request.expires_at,
        )

    def approve(
        self, db: Session, user: User, request_id: UUID, project_ids: list[int]
    ) -> str:
        """同一組織の選択Projectへ一回限りの認可コードを発行する。"""
        self.validate_browser(db, user)
        request = self.pending(db, request_id, lock=True)
        project_ids = sorted(set(project_ids))
        choices = {
            choice.id: choice for choice in self.consent(db, user, request_id).projects
        }
        if (
            not project_ids
            or any(i not in choices for i in project_ids)
            or len({choices[i].tenant_id for i in project_ids}) != 1
        ):
            raise ForbiddenError()
        connection = McpConnection(
            user_id=user.id,
            tenant_id=choices[project_ids[0]].tenant_id,
            client_id=request.client_id,
            resource=request.resource,
            project_ids=project_ids,
            scopes=request.scopes,
            credential_fingerprint=self.fingerprint(user),
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
        db.add(connection)
        db.flush()
        code = token_urlsafe(32)
        request.connection_id, request.code_hash = connection.id, digest(code)
        request.expires_at = datetime.now(UTC) + timedelta(minutes=2)
        db.commit()
        self.audit(db, connection, "mcp.connected")
        return self.callback(request, {"code": code})

    def callback(self, request: McpAuthorizationRequest, params: dict) -> str:
        """要求時に検証したcallbackだけを返す。"""
        return (
            request.redirect_uri
            + "?"
            + urlencode(
                {
                    **params,
                    "state": request.state,
                    "iss": oauth_issuer(),
                }
            )
        )

    def deny(self, db: Session, user: User, request_id: UUID) -> str:
        """未承認要求を閉じる。"""
        self.validate_browser(db, user)
        request = self.pending(db, request_id, lock=True)
        request.consumed_at = datetime.now(UTC)
        db.commit()
        return self.callback(request, {"error": "access_denied"})

    def valid_connection(self, db: Session, connection_id: UUID, *, lock: bool = False):
        """現在の所属を確認して信頼済みContextを固定する。"""
        connection = self.repository.connection(db, connection_id, lock=lock)
        if (
            connection is None
            or connection.revoked_at
            or connection.expires_at <= datetime.now(UTC)
            or connection.resource != settings.mcp_resource_url
            or connection.client_id != CLIENT_ID
            or connection.scopes != SCOPES
        ):
            raise McpOAuthError("invalid_token", 401)
        user = self.repository.user(db, connection.user_id)
        membership = self.repository.membership(
            db, connection.user_id, connection.tenant_id
        )
        if (
            user is None
            or not user.is_active
            or user.password_change_required
            or membership is None
            or not hmac.compare_digest(
                connection.credential_fingerprint, self.fingerprint(user)
            )
        ):
            raise McpOAuthError("invalid_token", 401)
        db.info.update(
            tenant_id=connection.tenant_id,
            tenant_user_id=user.id,
            data_realm="normal",
            mcp_connection_id=str(connection.id),
        )
        if not any(
            self.eligible(db, user, project_id) for project_id in connection.project_ids
        ):
            raise McpOAuthError("access_denied", 403)
        return connection, user

    def issue(self, db: Session, connection: McpConnection) -> dict:
        """accessは10分、refreshは接続の絶対期限まで。"""
        now = datetime.now(UTC)
        access, refresh = token_urlsafe(48), token_urlsafe(48)
        expiry = min(now + timedelta(minutes=10), connection.expires_at)
        db.add_all(
            [
                McpCredential(
                    connection_id=connection.id,
                    token_hash=digest(access),
                    kind="access",
                    expires_at=expiry,
                ),
                McpCredential(
                    connection_id=connection.id,
                    token_hash=digest(refresh),
                    kind="refresh",
                    expires_at=connection.expires_at,
                ),
            ]
        )
        db.commit()
        return {
            "access_token": access,
            "refresh_token": refresh,
            "token_type": "Bearer",
            "expires_in": max(1, int((expiry - now).total_seconds())),
            "scope": " ".join(connection.scopes),
        }

    def token(self, db: Session, params: dict[str, str]) -> dict:
        """コードまたはrefreshを一回だけ交換する。"""
        if (
            params.get("client_id") != CLIENT_ID
            or params.get("resource") != settings.mcp_resource_url
        ):
            raise McpOAuthError("invalid_request")
        if params.get("grant_type") == "authorization_code":
            request = self.repository.code(db, digest(params.get("code", "")))
            verifier = params.get("code_verifier", "")
            if not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier):
                raise McpOAuthError()
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            if (
                request is None
                or request.connection_id is None
                or request.expires_at <= datetime.now(UTC)
                or request.redirect_uri != params.get("redirect_uri")
                or not hmac.compare_digest(request.code_challenge, challenge)
            ):
                raise McpOAuthError()
            connection, _ = self.valid_connection(db, request.connection_id, lock=True)
            if request.consumed_at is not None:
                connection.revoked_at = datetime.now(UTC)
                db.commit()
                raise McpOAuthError()
            request.consumed_at = datetime.now(UTC)
            return self.issue(db, connection)
        if params.get("grant_type") == "refresh_token":
            credential = self.repository.credential(
                db, digest(params.get("refresh_token", ""))
            )
            if (
                credential is None
                or credential.kind != "refresh"
                or credential.expires_at <= datetime.now(UTC)
            ):
                raise McpOAuthError()
            connection, _ = self.valid_connection(
                db, credential.connection_id, lock=True
            )
            db.refresh(credential)
            if credential.consumed_at is not None:
                connection.revoked_at = datetime.now(UTC)
                db.commit()
                raise McpOAuthError()
            credential.consumed_at = datetime.now(UTC)
            return self.issue(db, connection)
        raise McpOAuthError("unsupported_grant_type")

    def exchange(self, db: Session, token: str) -> dict:
        """MCP用資格情報を連携API専用の1分JWTへ交換する。"""
        credential = self.repository.credential(db, digest(token))
        if (
            credential is None
            or credential.kind != "access"
            or credential.expires_at <= datetime.now(UTC)
        ):
            raise McpOAuthError("invalid_token", 401)
        connection, _ = self.valid_connection(db, credential.connection_id)
        expires = min(datetime.now(UTC) + timedelta(seconds=60), credential.expires_at)
        token = jwt.encode(
            {
                "sub": str(connection.user_id),
                "connection_id": str(connection.id),
                "aud": api_audience(),
                "iss": oauth_issuer(),
                "exp": expires,
                "iat": datetime.now(UTC),
            },
            api_signing_key(),
            algorithm="HS256",
        )
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": max(1, int((expires - datetime.now(UTC)).total_seconds())),
            "expires_at": int(expires.timestamp()),
            "scope": " ".join(connection.scopes),
            "client_id": connection.client_id,
        }

    def authenticate_api(self, db: Session, token: str):
        """通常JWTや別audienceの資格情報を拒否する。"""
        try:
            payload = jwt.decode(
                token,
                api_signing_key(),
                algorithms=["HS256"],
                audience=api_audience(),
                issuer=oauth_issuer(),
                options={
                    "require": ["sub", "connection_id", "aud", "iss", "exp", "iat"],
                    "strict_aud": True,
                },
            )
            connection, user = self.valid_connection(db, UUID(payload["connection_id"]))
            if payload["sub"] != str(user.id):
                raise ValueError()
            return connection, user
        except (jwt.PyJWTError, ValueError, KeyError, TypeError) as exc:
            raise McpOAuthError("invalid_token", 401) from exc

    def revoke(self, db: Session, user: User, connection_id: UUID) -> None:
        """接続した本人だけが委任全体を失効できる。"""
        self.validate_browser(db, user)
        connection = self.repository.connection(db, connection_id, lock=True)
        if connection is None or connection.user_id != user.id:
            raise NotFoundError()
        connection.revoked_at = datetime.now(UTC)
        db.commit()
        self.audit(db, connection, "mcp.disconnected")

    def audit(self, db: Session, connection: McpConnection, event: str) -> None:
        """資格情報を含めず接続の変更を記録する。"""
        self._audit(db, connection, event)

    def revoke_token(self, db: Session, client_id: str, token: str) -> None:
        """OAuth logoutではtokenの存在を応答で漏らさない。"""
        if client_id != CLIENT_ID:
            raise McpOAuthError("invalid_client")
        credential = self.repository.credential(db, digest(token))
        if credential is not None:
            connection = self.repository.connection(
                db, credential.connection_id, lock=True
            )
            if connection is not None:
                connection.revoked_at = datetime.now(UTC)
                db.commit()
                self.audit(db, connection, "mcp.disconnected")

    def _audit(self, db: Session, connection: McpConnection, event: str) -> None:
        """資格情報を含めず接続の変更を記録する。"""
        AuditLogService().record(
            db,
            event_type=event,
            tenant_id=connection.tenant_id,
            actor_user_id=connection.user_id,
            metadata={
                "source": "mcp",
                "connection_id": str(connection.id),
                "project_ids": connection.project_ids,
            },
        )
