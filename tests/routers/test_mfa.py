"""実署名とDBを用い、MFA・通常API・OAuth・復旧の境界を検証する。"""

import hashlib
import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import cbor2
import pyotp
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from webauthn.helpers import bytes_to_base64url as b64

from app.core.config import settings
from app.core.mfa import digest
from app.models.mcp import McpConnection
from app.models.mfa import MfaFactor, MfaFlow, MfaRecoveryCode
from app.models.session import UserSession
from app.services.email import EmailService
from tests.helpers.auth import authorize_as


@pytest.fixture(autouse=True)
def mfa_settings(monkeypatch):
    """通常はMFAを強制し、通知だけをローカルのfakeへ差し替える。"""
    monkeypatch.setattr(settings, "mfa_required", True)
    monkeypatch.setattr(settings, "mfa_encryption_keys", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "webauthn_rp_id", "localhost")
    monkeypatch.setattr(settings, "webauthn_origin", "http://localhost:3000")
    messages = []
    monkeypatch.setattr(EmailService, "ensure_available", lambda self: None)
    monkeypatch.setattr(
        EmailService,
        "send",
        lambda self, message: messages.append(message) or "fake-id",
    )
    return messages


def post(client, path, data=None):
    """CSRF Cookieの現在値を転送する。"""
    csrf = client.cookies.get(settings.csrf_cookie_name)
    return client.post(
        path, json=data, headers={settings.csrf_header_name: csrf} if csrf else {}
    )


def login(client, user):
    """通常Cookieがまだ発行されないことも確認する。"""
    response = post(
        client, "/auth/login", {"email": user.email, "password": "password123"}
    )
    assert response.status_code == 200, response.text
    assert settings.auth_cookie_name not in client.cookies
    assert "access_token" not in response.json()
    return response.json()["next_step"]


def enroll_totp(client, user, *, already_started=False):
    """実際のTOTPを使い、コード保存前後の業務境界も確認する。"""
    if not already_started:
        assert login(client, user) == "mfa_setup"
    response = post(client, "/auth/mfa/totp/setup", {"name": "Main app"})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["qr_data_url"].startswith("data:image/png;base64,")
    secret = parse_qs(urlsplit(response.json()["provisioning_uri"]).query)["secret"][0]
    result = post(client, "/auth/mfa/verify-code", {"code": pyotp.TOTP(secret).now()})
    assert result.status_code == 200, result.text
    assert result.json()["next_step"] == "recovery_codes"
    codes = result.json()["recovery_codes"]
    assert len(codes) == 10
    assert client.get("/auth/me").status_code == 401
    assert post(client, "/auth/mfa/finish").status_code == 200
    assert client.get("/auth/me").status_code == 200
    return secret, codes


def software_passkey(options, *, origin="http://localhost:3000", uv=True):
    """attestation noneの実ECDSA credentialを作る。"""
    private = ec.generate_private_key(ec.SECP256R1())
    numbers = private.public_key().public_numbers()
    cose = cbor2.dumps(
        {
            1: 2,
            3: -7,
            -1: 1,
            -2: numbers.x.to_bytes(32, "big"),
            -3: numbers.y.to_bytes(32, "big"),
        }
    )
    credential_id = secrets.token_bytes(32)
    auth_data = (
        hashlib.sha256(b"localhost").digest()
        + bytes([0x45 if uv else 0x41])
        + (0).to_bytes(4, "big")
        + bytes(16)
        + len(credential_id).to_bytes(2, "big")
        + credential_id
        + cose
    )
    client_data = json.dumps(
        {"type": "webauthn.create", "challenge": options["challenge"], "origin": origin}
    ).encode()
    credential = {
        "id": b64(credential_id),
        "rawId": b64(credential_id),
        "type": "public-key",
        "response": {
            "clientDataJSON": b64(client_data),
            "attestationObject": b64(
                cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
            ),
        },
        "clientExtensionResults": {},
    }
    return private, credential


def assertion(
    options,
    private,
    credential_id,
    *,
    origin="http://localhost:3000",
    rp="localhost",
    uv=True,
    count=1,
):
    """challengeとoriginを含むデータへ本物の公開鍵署名を付ける。"""
    client_data = json.dumps(
        {"type": "webauthn.get", "challenge": options["challenge"], "origin": origin}
    ).encode()
    auth_data = (
        hashlib.sha256(rp.encode()).digest()
        + bytes([5 if uv else 1])
        + count.to_bytes(4, "big")
    )
    signature = private.sign(
        auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
    )
    return {
        "id": credential_id,
        "rawId": credential_id,
        "type": "public-key",
        "response": {
            "clientDataJSON": b64(client_data),
            "authenticatorData": b64(auth_data),
            "signature": b64(signature),
        },
        "clientExtensionResults": {},
    }


def test_initial_enrollment_and_existing_session_gate(client, create_test_user, db):
    """旧セッション・limited Cookie・確認済み通常セッションを区別する。"""
    user = create_test_user()
    authorize_as(client, user)
    assert client.get("/auth/me").json()["code"] == "MFA_REQUIRED"
    assert client.get("/users").json()["code"] == "MFA_REQUIRED"
    assert login(client, user) == "mfa_setup"
    assert client.get("/auth/flow").json()["next_step"] == "mfa_setup"
    assert client.get("/auth/me").status_code == 401
    secret, codes = enroll_totp(client, user, already_started=True)
    factor = db.query(MfaFactor).one()
    assert secret not in factor.secret
    assert (
        db.query(MfaRecoveryCode)
        .filter(MfaRecoveryCode.code_hash == digest(codes[0].replace("-", "")))
        .count()
        == 1
    )
    session = (
        db.query(UserSession).filter(UserSession.mfa_verified_at.is_not(None)).one()
    )
    assert session.mfa_method == "totp"
    assert post(client, "/auth/mfa/finish").status_code == 400


def test_totp_replay_failure_limit_and_csrf(client, create_test_user, db):
    """登録時に使ったコードの再利用、CSRF不備、5回超過を拒否する。"""
    user = create_test_user()
    secret, _ = enroll_totp(client, user)
    assert login(client, user) == "mfa_verify"
    assert (
        client.post("/auth/mfa/verify-code", json={"code": "000000"}).status_code == 403
    )
    registered_step = db.query(MfaFactor).one().last_step
    assert (
        post(
            client,
            "/auth/mfa/verify-code",
            {"code": pyotp.TOTP(secret).at(registered_step * 30)},
        ).status_code
        == 400
    )
    for _ in range(4):
        assert (
            post(client, "/auth/mfa/verify-code", {"code": "ああああああ"}).status_code
            == 400
        )
    assert client.get("/auth/flow").status_code == 400
    assert max(flow.attempts for flow in db.query(MfaFlow)) == 5


def test_recovery_code_is_single_use_even_concurrently(client, create_test_user):
    """同じ限定状態からの並行送信でも通常セッションを一度だけ発行する。"""
    from concurrent.futures import ThreadPoolExecutor

    user = create_test_user()
    _, codes = enroll_totp(client, user)
    login(client, user)
    cookies = dict(client.cookies)

    def verify():
        with TestClient(client.app) as other:
            other.cookies.update(cookies)
            return post(
                other, "/auth/mfa/verify-code", {"code": codes[0], "recovery": True}
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda _: verify(), range(2)))
    assert sorted(statuses) == [200, 400]
    login(client, user)
    assert (
        post(
            client, "/auth/mfa/verify-code", {"code": codes[0], "recovery": True}
        ).status_code
        == 400
    )
    assert (
        post(
            client, "/auth/mfa/verify-code", {"code": codes[1], "recovery": True}
        ).status_code
        == 200
    )


@pytest.mark.parametrize("changed", ["expiry", "password", "version"])
def test_pending_flow_cannot_survive_auth_change(client, create_test_user, db, changed):
    """期限切れ・パスワード変更・MFA変更前の限定状態を拒否する。"""
    user = create_test_user()
    login(client, user)
    if changed == "expiry":
        db.query(MfaFlow).one().expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif changed == "password":
        user.hashed_password = "changed"
    else:
        user.mfa_security_version += 1
    db.commit()
    assert client.get("/auth/flow").status_code == 400


def test_passkey_actual_registration_signature_and_replay(client, create_test_user, db):
    """実登録・実署名で追加認証し、challengeを再利用できない。"""
    user = create_test_user()
    login(client, user)
    options = post(
        client, "/auth/mfa/passkeys/register-options", {"name": "Mac"}
    ).json()
    assert options["authenticatorSelection"]["userVerification"] == "required"
    private, registration = software_passkey(options)
    result = post(client, "/auth/mfa/passkeys/verify", {"credential": registration})
    assert result.status_code == 200, result.text
    post(client, "/auth/mfa/finish")
    assert client.get("/auth/me").status_code == 200
    login(client, user)
    options = post(client, "/auth/mfa/passkeys/authenticate-options").json()
    credential = assertion(options, private, registration["id"])
    result = post(client, "/auth/mfa/passkeys/verify", {"credential": credential})
    assert result.status_code == 200, result.text
    assert db.query(MfaFactor).one().sign_count == 1
    assert (
        post(
            client, "/auth/mfa/passkeys/verify", {"credential": credential}
        ).status_code
        == 400
    )


@pytest.mark.parametrize(
    "invalid", ["origin", "rp", "uv", "signature", "challenge", "owner"]
)
def test_passkey_rejects_invalid_proof(client, create_test_user, invalid):
    """署名をmockせず、各信頼境界の不一致を検証する。"""
    user = create_test_user()
    login(client, user)
    options = post(
        client, "/auth/mfa/passkeys/register-options", {"name": "Mac"}
    ).json()
    private, registration = software_passkey(options)
    assert (
        post(
            client, "/auth/mfa/passkeys/verify", {"credential": registration}
        ).status_code
        == 200
    )
    post(client, "/auth/mfa/finish")
    login(client, user)
    options = post(client, "/auth/mfa/passkeys/authenticate-options").json()
    if invalid == "challenge":
        options["challenge"] = b64(secrets.token_bytes(32))
    if invalid == "signature":
        private = ec.generate_private_key(ec.SECP256R1())
    credential = assertion(
        options,
        private,
        b64(secrets.token_bytes(32)) if invalid == "owner" else registration["id"],
        origin="https://attacker.example"
        if invalid == "origin"
        else "http://localhost:3000",
        rp="attacker.example" if invalid == "rp" else "localhost",
        uv=invalid != "uv",
    )
    response = post(client, "/auth/mfa/passkeys/verify", {"credential": credential})
    assert response.status_code == 400, response.text
    assert settings.auth_cookie_name not in client.cookies


def test_last_factor_and_ownership_then_revoke_sessions_and_mcp(
    client, create_test_user, create_test_project, assign_project_role, db, monkeypatch
):
    """最後の要素と所有者を保護し、削除時に全ての委任を失効する。"""
    from tests.routers.test_mcp import connect

    user = create_test_user()
    project = create_test_project()
    assign_project_role(user=user, project=project, role_key="member")
    monkeypatch.setattr(settings, "mcp_enabled", True)
    monkeypatch.setattr(settings, "mcp_issuer_url", "http://127.0.0.1:8000")
    monkeypatch.setattr(settings, "mcp_resource_url", "http://127.0.0.1:8765/mcp")
    enroll_totp(client, user)
    factor = db.query(MfaFactor).one()
    csrf = {settings.csrf_header_name: client.cookies.get(settings.csrf_cookie_name)}
    assert (
        client.delete(f"/auth/mfa/factors/{factor.id}", headers=csrf).status_code == 403
    )
    client.headers.update(csrf)
    direct, tokens, _ = connect(client, project.id)
    assert post(client, "/auth/mfa/manage").status_code == 200
    options = post(
        client, "/auth/mfa/passkeys/register-options", {"name": "Backup"}
    ).json()
    _, registration = software_passkey(options)
    assert (
        post(
            client, "/auth/mfa/passkeys/verify", {"credential": registration}
        ).status_code
        == 200
    )
    csrf = {settings.csrf_header_name: client.cookies.get(settings.csrf_cookie_name)}
    assert (
        client.delete(f"/auth/mfa/factors/{factor.id}", headers=csrf).status_code == 204
    )
    assert client.get("/auth/me").status_code == 401
    assert db.query(McpConnection).one().revoked_at is not None
    from app.core.mcp import CLIENT_ID

    assert (
        direct.post(
            "/oauth/token",
            data={
                "client_id": CLIENT_ID,
                "resource": settings.mcp_resource_url,
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
            },
        ).json()["error"]
        == "invalid_token"
    )


def test_operator_recovery_requires_permission_recent_proof_and_ticket(
    client, create_test_user, db, mfa_settings
):
    """組織所属だけでは解除不可。承認後もパスワードだけで復旧できない。"""
    actor = create_test_user(email="operator@example.com", system_role="system_admin")
    target = create_test_user(email="target@example.com")
    enroll_totp(client, actor)
    data = {
        "verification_method": "in_person",
        "identity_confirmed": True,
        "reason": "対面で本人情報と申請内容を照合した",
    }
    assert post(client, f"/users/{actor.id}/mfa-recovery", data).status_code == 403
    assert post(client, f"/users/{target.id}/mfa-recovery", data).status_code == 204
    message = next(item for item in mfa_settings if item.subject.endswith("再登録"))
    match = re.search(r"#token=([A-Za-z0-9_-]+)", message.text)
    assert match is not None
    token = match.group(1)
    client.cookies.clear()
    assert login(client, target) == "mfa_recovery"
    assert (
        post(
            client, "/auth/mfa/recover", {"token": token, "password": "wrong"}
        ).status_code
        == 400
    )
    assert (
        post(
            client, "/auth/mfa/recover", {"token": token, "password": "password123"}
        ).status_code
        == 200
    )
    assert (
        post(
            client, "/auth/mfa/recover", {"token": token, "password": "password123"}
        ).status_code
        == 400
    )
    enroll_totp(client, target, already_started=True)
    db.refresh(target)
    assert not target.mfa_recovery_pending
    assert post(client, f"/users/{actor.id}/mfa-recovery", data).status_code == 403


def test_reauthentication_backups_codes_and_recent_window(client, create_test_user, db):
    """別TOTPの追加、再認証の必須化、コード差し替えを検証する。"""
    user = create_test_user()
    first_secret, old_codes = enroll_totp(client, user)
    assert post(client, "/auth/mfa/manage").status_code == 200
    response = post(client, "/auth/mfa/totp/setup", {"name": "Backup app"})
    backup = parse_qs(urlsplit(response.json()["provisioning_uri"]).query)["secret"][0]
    assert backup != first_secret
    assert (
        post(
            client, "/auth/mfa/verify-code", {"code": pyotp.TOTP(backup).now()}
        ).json()["next_step"]
        == "authenticated"
    )
    session = (
        db.query(UserSession).filter(UserSession.mfa_verified_at.is_not(None)).one()
    )
    session.mfa_reauthenticated_at = datetime.now(UTC) - timedelta(minutes=6)
    db.query(MfaFactor).update({"last_step": -1})
    db.commit()
    assert post(client, "/auth/mfa/manage").status_code == 403
    assert post(client, "/auth/mfa/recovery-codes").status_code == 403
    assert post(client, "/auth/mfa/reauth", {"password": "wrong"}).status_code == 400
    assert (
        post(client, "/auth/mfa/reauth", {"password": "password123"}).status_code == 200
    )
    assert (
        post(
            client, "/auth/mfa/verify-code", {"code": old_codes[0], "recovery": True}
        ).status_code
        == 400
    )
    assert (
        post(
            client, "/auth/mfa/verify-code", {"code": pyotp.TOTP(backup).now()}
        ).status_code
        == 200
    )
    response = post(client, "/auth/mfa/recovery-codes")
    assert response.status_code == 200
    assert old_codes[0] not in response.json()
    login(client, user)
    assert (
        post(
            client, "/auth/mfa/verify-code", {"code": old_codes[0], "recovery": True}
        ).status_code
        == 400
    )
    assert (
        post(
            client,
            "/auth/mfa/verify-code",
            {"code": response.json()[0], "recovery": True},
        ).status_code
        == 200
    )


@pytest.mark.parametrize("invalid", ["origin", "uv"])
def test_passkey_registration_requires_trusted_origin_and_user_verification(
    client, create_test_user, invalid
):
    """初回登録の段階でも本人確認と固定originが必要。"""
    user = create_test_user()
    login(client, user)
    options = post(
        client, "/auth/mfa/passkeys/register-options", {"name": "Mac"}
    ).json()
    _, registration = software_passkey(
        options,
        origin="https://attacker.example"
        if invalid == "origin"
        else "http://localhost:3000",
        uv=invalid != "uv",
    )
    assert (
        post(
            client, "/auth/mfa/passkeys/verify", {"credential": registration}
        ).status_code
        == 400
    )


def test_legacy_mcp_cannot_refresh_exchange_or_use_bearer(
    client, create_test_user, create_test_project, assign_project_role, monkeypatch
):
    """MFA導入前の委任は新しいコード交換以外の経路でも通らない。"""
    from app.core.mcp import CLIENT_ID
    from tests.routers.test_mcp import api_headers, connect

    monkeypatch.setattr(settings, "mcp_enabled", True)
    monkeypatch.setattr(settings, "mcp_issuer_url", "http://127.0.0.1:8000")
    monkeypatch.setattr(settings, "mcp_resource_url", "http://127.0.0.1:8765/mcp")
    monkeypatch.setattr(settings, "mfa_required", False)
    user, project = create_test_user(), create_test_project()
    assign_project_role(user=user, project=project, role_key="member")
    authorize_as(client, user)
    direct, tokens, _ = connect(client, project.id)
    headers = api_headers(direct, tokens["access_token"])
    monkeypatch.setattr(settings, "mfa_required", True)
    assert (
        direct.post(
            "/oauth/exchange",
            headers={"Authorization": "Bearer " + tokens["access_token"]},
        ).status_code
        == 401
    )
    assert direct.post(
        "/oauth/token",
        data={
            "client_id": CLIENT_ID,
            "resource": settings.mcp_resource_url,
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
        },
    ).status_code in {400, 401}
    assert direct.get("/integrations/mcp/catalog", headers=headers).status_code == 401


def test_demo_exemption_does_not_allow_normal_mfa_management(
    client, create_test_user, demo_settings, monkeypatch
):
    """使い捨て匿名デモは利用でき、通常Identityの認証変更はできない。"""
    from tests.routers.test_demo import start

    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "frontend_public_url", "http://testserver")
    user = create_test_user()
    login(client, user)
    old_flow = client.cookies.get(settings.mfa_cookie_name)
    client.cookies.clear()
    status = start(client)
    assert status["id"]
    assert client.get("/auth/me").status_code == 200
    client.cookies.set(settings.mfa_cookie_name, old_flow)
    assert client.get("/auth/flow").status_code == 403
    assert client.get("/auth/mfa").status_code == 403


def test_logout_revokes_pending_flow_and_shared_limit_survives_new_flows(
    client, create_test_user
):
    """ログアウト時の取消とCookieを作り直す総当たりへの制限を確認する。"""
    user = create_test_user()
    login(client, user)
    old_cookie = client.cookies.get(settings.mfa_cookie_name)
    assert post(client, "/auth/logout").status_code == 204
    client.cookies.set(settings.mfa_cookie_name, old_cookie)
    assert client.get("/auth/flow").status_code == 400
    client.cookies.clear()
    for _ in range(10):
        login(client, user)
        post(client, "/auth/mfa/totp/setup", {"name": "App"})
        assert (
            post(client, "/auth/mfa/verify-code", {"code": "invalid"}).status_code
            == 400
        )
    login(client, user)
    post(client, "/auth/mfa/totp/setup", {"name": "App"})
    assert post(client, "/auth/mfa/verify-code", {"code": "invalid"}).status_code == 429


def test_factor_changes_cannot_target_another_user(client, create_test_user, db):
    """UUIDを知っていても他人の認証器を変更できない。"""
    user = create_test_user()
    enroll_totp(client, user)
    other = create_test_user(email="other@example.com")
    factor = MfaFactor(
        user_id=other.id,
        name="Other",
        kind="totp",
        secret="encrypted",
        verified_at=datetime.now(UTC),
    )
    db.add(factor)
    db.commit()
    csrf = {settings.csrf_header_name: client.cookies.get(settings.csrf_cookie_name)}
    assert (
        client.patch(
            f"/auth/mfa/factors/{factor.id}", json={"name": "Changed"}, headers=csrf
        ).status_code
        == 403
    )
    assert (
        client.delete(f"/auth/mfa/factors/{factor.id}", headers=csrf).status_code == 403
    )
    own = db.query(MfaFactor).filter(MfaFactor.user_id == user.id).one()
    assert (
        client.patch(
            f"/auth/mfa/factors/{own.id}", json={"name": "Personal app"}, headers=csrf
        ).status_code
        == 204
    )
    db.refresh(factor)
    assert factor.name == "Other" and factor.revoked_at is None
    assert client.get("/auth/me").status_code == 200


def test_recovery_send_failure_preserves_identity_and_sessions(
    client, create_test_user, db, monkeypatch
):
    """復旧案内を送れない場合、失効だけを確定してロックアウトさせない。"""
    from app.core.exceptions import EmailUnavailableError

    actor = create_test_user(email="operator@example.com", system_role="system_admin")
    target = create_test_user(email="target@example.com")
    with TestClient(client.app) as target_client:
        enroll_totp(target_client, target)
        enroll_totp(client, actor)
        session = (
            db.query(UserSession)
            .filter(
                UserSession.user_id == target.id,
                UserSession.mfa_verified_at.is_not(None),
            )
            .one()
        )

        def unavailable(self, message):
            raise EmailUnavailableError()

        monkeypatch.setattr(EmailService, "send", unavailable)
        data = {
            "verification_method": "in_person",
            "identity_confirmed": True,
            "reason": "本人の申請と事前情報を対面で照合済み",
        }
        assert post(client, f"/users/{target.id}/mfa-recovery", data).status_code == 503
        db.refresh(target)
        db.refresh(session)
        assert not target.mfa_recovery_pending and target.mfa_security_version == 0
        assert session.revoked_at is None
        assert target_client.get("/auth/me").status_code == 200


def test_audit_failure_cannot_commit_factor_or_normal_session(
    client, create_test_user, db, monkeypatch
):
    """重要操作の監査保存と認証変更が別々に確定しない。"""
    from app.services.audit_log import AuditLogService

    user = create_test_user()
    login(client, user)
    response = post(client, "/auth/mfa/totp/setup", {"name": "App"})
    secret = parse_qs(urlsplit(response.json()["provisioning_uri"]).query)["secret"][0]
    original = AuditLogService.record

    def failed(self, *args, **kwargs):
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr(AuditLogService, "record", failed)
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        post(client, "/auth/mfa/verify-code", {"code": pyotp.TOTP(secret).now()})
    assert db.query(MfaFactor).count() == 0 and db.query(UserSession).count() == 0
    monkeypatch.setattr(AuditLogService, "record", original)
    assert (
        post(
            client, "/auth/mfa/verify-code", {"code": pyotp.TOTP(secret).now()}
        ).status_code
        == 200
    )


@pytest.mark.parametrize("revoke_before_finish", [False, True])
def test_preparation_mode_allows_enrollment_before_enforcement(
    client, create_test_user, db, monkeypatch, revoke_before_finish
):
    """公開前の運営者登録を可能にし、登録フローは元セッションへ拘束する。"""
    monkeypatch.setattr(settings, "mfa_required", False)
    user = create_test_user()
    session_id = authorize_as(client, user)
    result = post(client, "/auth/mfa/reauth", {"password": "password123"})
    assert result.status_code == 200, result.text
    assert result.json()["next_step"] == "mfa_setup"
    response = post(client, "/auth/mfa/totp/setup", {"name": "Operator app"})
    secret = parse_qs(urlsplit(response.json()["provisioning_uri"]).query)["secret"][0]
    assert (
        post(
            client, "/auth/mfa/verify-code", {"code": pyotp.TOTP(secret).now()}
        ).status_code
        == 200
    )
    if revoke_before_finish:
        session = db.get(UserSession, session_id)
        assert session is not None
        session.revoked_at = datetime.now(UTC)
        db.commit()
        assert post(client, "/auth/mfa/finish").status_code == 400
        assert (
            db.query(UserSession)
            .filter(UserSession.mfa_verified_at.is_not(None))
            .count()
            == 0
        )
        return
    assert post(client, "/auth/mfa/finish").status_code == 200
    monkeypatch.setattr(settings, "mfa_required", True)
    assert client.get("/auth/me").status_code == 200
