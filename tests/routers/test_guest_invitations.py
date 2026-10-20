"""メール確認・通常認証・参加資格の境界を実PostgreSQLで確認する。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pyotp
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import DBAPIError

from app.core.config import settings
from app.db.session import session_local
from app.models.artifact_publication_access import (
    ArtifactPublicationGrant,
    ProjectGuestParticipation,
)
from app.models.guest_invitation import (
    ProjectGuestInvitation,
    ProjectGuestInvitationCheck,
    ProjectGuestInvitationEvent,
)
from app.models.project import ProjectMember
from app.models.session import UserSession
from app.models.tenant import TenantMember
from app.models.user import User
from app.services.email import EmailService
from tests.helpers.auth import authorize_as
from tests.routers.test_artifact_releases import context as context  # noqa: F401
from tests.routers.test_mfa import enroll_totp

ORIGIN = {"origin": "http://localhost:3000"}
EMAIL = "ux-invitation-recipient@example.com"


@pytest.fixture(autouse=True)
def invitation_settings(monkeypatch):
    """外部配送せず、ダミー宛メールをメモリへ捕捉する。"""
    monkeypatch.setattr(settings, "guest_invitations_enabled", True)
    monkeypatch.setattr(settings, "local_email_capture_recipients", EMAIL)
    messages = []
    monkeypatch.setattr(
        EmailService,
        "send",
        lambda self, message: messages.append(message) or "test-id",
    )
    monkeypatch.setattr(
        "app.routers.guest_invitations.consume_email_request_budget", lambda *a: None
    )
    monkeypatch.setattr(
        "app.routers.guest_invitations.consume_mfa_budget", lambda *a: None
    )
    return messages


def base(ctx):
    """内部のProject限定URLを返す。"""
    return f"/projects/{ctx['project'].id}/guest-invitations"


def create(client, ctx, messages):
    """メールの送信受付と受諾を区別する。"""
    data = dict(
        request_id=str(uuid4()),
        email=EMAIL,
        display_label="お客様",
        participation_expires_at=(datetime.now(UTC) + timedelta(days=30)).isoformat(),
    )
    response = client.post(base(ctx), json=data)
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["state"] == "pending" and row["delivery_state"] == "sent"
    assert "recipient_id" not in row
    token = messages[-1].text.split("#token=", 1)[1].split()[0]
    assert token not in response.text
    return row, token, data


def inspect(client, row, token):
    """BFF専用pathの確認Cookieを、TestClientでは明示転送する。"""
    response = client.post(
        f"/guest-invitations/{row['id']}/inspect", json={"token": token}, headers=ORIGIN
    )
    assert response.status_code == 200, response.text
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    proof = cookie["guest_invitation_check"]
    assert proof["httponly"] and proof["samesite"] == "lax"
    assert proof["path"] == f"/api/guest-invitations/{row['id']}"
    assert 0 < int(proof["max-age"]) <= 1799
    client.cookies.set(
        "guest_invitation_check", proof.value, domain="testserver.local", path="/"
    )
    return response.json(), proof.value


def command(row, **values):
    """同じ確認版で操作する。"""
    return {"request_id": str(uuid4()), "version": row["version"], **values}


def guest(db, create_test_user):
    """通常組織の所属を持たない既存本人を用意する。"""
    user = create_test_user(email=EMAIL)
    db.execute(
        update(TenantMember)
        .where(TenantMember.user_id == user.id)
        .values(status="removed")
    )
    user.user_type = "guest"
    db.commit()
    return user


def authorize_recipient(client, person):
    """認可境界テスト用に検証済みsessionを用意する。実TOTPは別ケース。"""
    identity = authorize_as(client, person)
    with session_local() as session:
        session.execute(
            update(UserSession)
            .where(UserSession.id == identity)
            .values(
                mfa_verified_at=datetime.now(UTC),
                mfa_method="totp",
                mfa_security_version=person.mfa_security_version,
            )
        )
        session.commit()


def test_register_login_accept_without_automatic_grants(
    client, db, context, invitation_settings
):
    """本人が設定し、参加だけを作る。資料許可/内部所属は増やさない。"""
    row, token, creation = create(client, context, invitation_settings)
    assert client.post(base(context), json=creation).json() == row
    assert len(invitation_settings) == 1
    client.cookies.clear()
    info, proof = inspect(client, row, token)
    assert info["next_step"] == "register"
    path = f"/guest-invitations/{row['id']}"
    data = command(info, name="確認するお客様", password="dummy-register-password-123")
    response = client.post(path + "/register", json=data, headers=ORIGIN)
    assert response.status_code == 200, response.text
    registered = response.json()
    assert registered["next_step"] == "login"
    assert (
        client.post(path + "/register", json=data, headers=ORIGIN).json() == registered
    )
    person = db.scalar(select(User).where(User.email == EMAIL))
    assert person.user_type == "guest" and person.name == data["name"]
    assert person.hashed_password != data["password"]
    assert (
        client.post(
            path + "/accept", json=command(registered), headers=ORIGIN
        ).status_code
        == 401
    )
    authorize_recipient(client, person)
    assert client.get(path).json()["next_step"] == "accept"
    accept_data = command(registered)
    response = client.post(path + "/accept", json=accept_data, headers=ORIGIN)
    assert response.status_code == 200, response.text
    assert (
        client.post(path + "/accept", json=accept_data, headers=ORIGIN).json()
        == response.json()
    )
    part = db.scalar(
        select(ProjectGuestParticipation).where(
            ProjectGuestParticipation.user_id == person.id
        )
    )
    assert part and part.generation == 1 and part.created_by == person.id
    assert db.scalar(select(func.count()).select_from(ArtifactPublicationGrant)) == 0
    assert (
        db.scalar(
            select(func.count())
            .select_from(TenantMember)
            .where(TenantMember.user_id == person.id)
        )
        == 0
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(ProjectMember)
            .where(ProjectMember.user_id == person.id)
        )
        == 0
    )
    for cls in (
        ProjectGuestInvitation,
        ProjectGuestInvitationCheck,
        ProjectGuestInvitationEvent,
    ):
        rows = str(db.execute(select(cls.__table__)).mappings().all())
        assert token not in rows and proof not in rows and data["password"] not in rows


def test_decline_without_account(client, db, context, invitation_settings):
    """メール確認者として辞退し、Userを作らない。"""
    row, token, _ = create(client, context, invitation_settings)
    client.cookies.clear()
    inspect(client, row, token)
    path = f"/guest-invitations/{row['id']}/decline"
    data = command(row)
    response = client.post(path, json=data, headers=ORIGIN)
    assert response.status_code == 200, response.text
    assert client.post(path, json=data, headers=ORIGIN).json() == response.json()
    assert db.scalar(select(User).where(User.email == EMAIL)) is None
    event = db.scalar(
        select(ProjectGuestInvitationEvent).where(
            ProjectGuestInvitationEvent.kind == "declined"
        )
    )
    assert event.actor_id is None and event.actor_kind == "email_recipient"


def test_existing_guest_and_wrong_account(
    client, db, context, create_test_user, invitation_settings
):
    """別本人は受諾も登録もできず、既存本人の認証情報は変更しない。"""
    person = guest(db, create_test_user)
    before = person.hashed_password
    row, token, _ = create(client, context, invitation_settings)
    inspect(client, row, token)
    path = f"/guest-invitations/{row['id']}"
    assert client.get(path).json()["next_step"] == "wrong_account"
    assert (
        client.post(path + "/accept", json=command(row), headers=ORIGIN).status_code
        == 403
    )
    client.cookies.clear()
    inspect(client, row, token)
    assert (
        client.post(
            path + "/register",
            json=command(row, name="上書き", password="dummy-register-password"),
            headers=ORIGIN,
        ).status_code
        == 409
    )
    authorize_recipient(client, person)
    assert (
        client.post(path + "/accept", json=command(row), headers=ORIGIN).status_code
        == 200
    )
    db.refresh(person)
    assert person.hashed_password == before


def test_resend_invalidates_link_and_check_without_extending_deadlines(
    client, context, invitation_settings
):
    """旧リンク・旧Cookieと古いversionは再利用できない。"""
    row, token, _ = create(client, context, invitation_settings)
    _, proof = inspect(client, row, token)
    response = client.post(f"{base(context)}/{row['id']}/resend", json=command(row))
    assert response.status_code == 200, response.text
    current = response.json()
    assert current["expires_at"] == row["expires_at"]
    assert current["participation_expires_at"] == row["participation_expires_at"]
    path = f"/guest-invitations/{row['id']}"
    assert client.get(path).status_code == 404
    assert (
        client.post(
            path + "/inspect", json={"token": token}, headers=ORIGIN
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{base(context)}/{row['id']}/resend", json=command(row)
        ).status_code
        == 409
    )
    new_token = invitation_settings[-1].text.split("#token=", 1)[1].split()[0]
    assert new_token != token
    assert inspect(client, current, new_token)[1] != proof


def test_revoke_and_issuer_loss(client, db, context, invitation_settings):
    """取消と招待者の権限失効を、確認後も再検証する。"""
    row, token, _ = create(client, context, invitation_settings)
    inspect(client, row, token)
    data = command(row, reason="宛先を確認し直す")
    url = f"{base(context)}/{row['id']}/revoke"
    response = client.post(url, json=data)
    assert response.status_code == 200, response.text
    assert client.post(url, json=data).json() == response.json()
    assert (
        client.get(f"/guest-invitations/{row['id']}").json()["next_step"]
        == "unavailable"
    )
    row2, token2, _ = create(client, context, invitation_settings)
    inspect(client, row2, token2)
    context["user"].is_active = False
    db.commit()
    client.cookies.clear()
    assert (
        client.post(
            f"/guest-invitations/{row2['id']}/inspect",
            json={"token": token2},
            headers=ORIGIN,
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    "origin", [None, "https://evil.example", "http://localhost:3000.evil.example"]
)
def test_anonymous_origin_required(client, context, invitation_settings, origin):
    """匿名POSTも任意サイトから実行できない。"""
    row, token, _ = create(client, context, invitation_settings)
    client.cookies.clear()
    response = client.post(
        f"/guest-invitations/{row['id']}/inspect",
        json={"token": token},
        headers={"origin": origin} if origin else {},
    )
    assert response.status_code == 403
    assert "no-store" in response.headers["cache-control"]


def test_same_organization_member_is_not_guest(
    client, db, context, invitation_settings
):
    """同じ組織の社内本人をゲスト参加へ迂回しない。"""
    context["user"].email = EMAIL
    db.commit()
    authorize_as(client, context["user"])
    row, token, _ = create(client, context, invitation_settings)
    assert (
        client.post(
            f"/guest-invitations/{row['id']}/inspect",
            json={"token": token},
            headers=ORIGIN,
        ).status_code
        == 404
    )


def test_new_operations_stop_but_management_reader_remains(
    client, context, invitation_settings, monkeypatch
):
    """flag停止時は新操作を止め、内部履歴は読める。"""
    row, token, _ = create(client, context, invitation_settings)
    monkeypatch.setattr(settings, "guest_invitations_enabled", False)
    assert client.get(base(context)).status_code == 200
    assert client.get(f"{base(context)}/{row['id']}/events").status_code == 200
    assert (
        client.post(
            f"{base(context)}/{row['id']}/resend", json=command(row)
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/guest-invitations/{row['id']}/inspect",
            json={"token": token},
            headers=ORIGIN,
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "table",
    [ProjectGuestInvitation, ProjectGuestInvitationCheck, ProjectGuestInvitationEvent],
)
def test_history_delete_is_rejected(client, db, context, invitation_settings, table):
    """新しい証跡3表をDELETEで消せない。"""
    row, token, _ = create(client, context, invitation_settings)
    inspect(client, row, token)
    with pytest.raises(DBAPIError):
        db.execute(delete(table))
        db.commit()
    db.rollback()


def test_confirmation_bound_to_invitation(client, context, invitation_settings):
    """確認Cookieは別のUUIDに使えない。"""
    row, token, _ = create(client, context, invitation_settings)
    inspect(client, row, token)
    assert client.get(f"/guest-invitations/{uuid4()}").status_code == 404
    assert client.get(f"/guest-invitations/{row['id']}").status_code == 200


def test_no_capture_for_unapproved_recipient(client, context, invitation_settings):
    """通常SMTPへフォールバックして実宛先へ送らない。"""
    data = dict(
        request_id=str(uuid4()),
        email="actual@example.net",
        display_label="相手",
        participation_expires_at=(datetime.now(UTC) + timedelta(days=30)).isoformat(),
    )
    assert client.post(base(context), json=data).status_code == 403
    assert not invitation_settings


def test_non_superuser_operations_and_database_guards(
    client, db, context, invitation_settings, monkeypatch
):
    """管理権限のないDB接続でもguardを迂回せず作成/辞退する。"""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    import app.db.session as sessions

    role = "ux_invitation_test_runtime"
    admin_engine = db.get_bind()
    with admin_engine.begin() as connection:
        connection.execute(
            text(
                f"CREATE ROLE {role} LOGIN PASSWORD 'isolated-invitation-only' "
                "NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
            )
        )
        connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        connection.execute(
            text(
                "GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES "
                f"IN SCHEMA public TO {role}"
            )
        )
        connection.execute(
            text(f"GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}")
        )
    restricted = create_engine(
        admin_engine.url.set(username=role, password="isolated-invitation-only")
    )
    try:
        monkeypatch.setattr(sessions, "session_local", sessionmaker(bind=restricted))
        row, token, _ = create(client, context, invitation_settings)
        client.cookies.clear()
        inspect(client, row, token)
        result = client.post(
            f"/guest-invitations/{row['id']}/decline", json=command(row), headers=ORIGIN
        )
        assert result.status_code == 200, result.text
        with restricted.connect() as connection:
            assert not any(
                connection.execute(
                    text(
                        "SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole "
                        "FROM pg_roles WHERE rolname=current_user"
                    )
                ).one()
            )
            with pytest.raises(DBAPIError):
                connection.execute(
                    text("UPDATE project_guest_invitation_events SET kind='forged'")
                )
            connection.rollback()
            with pytest.raises(DBAPIError):
                connection.execute(
                    text(
                        "UPDATE project_guest_invitations "
                        "SET status='pending',version=version+1"
                    )
                )
    finally:
        restricted.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP OWNED BY {role}"))
            connection.execute(text(f"DROP ROLE {role}"))


def test_real_mfa_required_before_acceptance(
    client, db, context, create_test_user, invitation_settings, monkeypatch
):
    """通常TOTP登録と復旧コード確認が完了するまで受諾できない。"""
    person = guest(db, create_test_user)
    row, token, _ = create(client, context, invitation_settings)
    client.cookies.clear()
    _, proof = inspect(client, row, token)
    monkeypatch.setattr(settings, "mfa_required", True)
    monkeypatch.setattr(settings, "mfa_encryption_keys", Fernet.generate_key().decode())
    authorize_as(client, person)
    url = f"/guest-invitations/{row['id']}/accept"
    assert client.post(url, json=command(row), headers=ORIGIN).status_code == 403
    client.cookies.clear()
    enroll_totp(client, person)
    client.headers[settings.csrf_header_name] = client.cookies.get(
        settings.csrf_cookie_name
    )
    client.cookies.set(
        "guest_invitation_check", proof, domain="testserver.local", path="/"
    )
    response = client.post(url, json=command(row), headers=ORIGIN)
    assert response.status_code == 200, response.text
    assert any(
        message.to == EMAIL and "追加認証" in message.subject
        for message in invitation_settings
    )


def test_two_concurrent_accepts_create_one_participation(
    client, db, context, create_test_user, invitation_settings
):
    """同時リクエストでも1参加・1受諾履歴に収束する。"""
    person = guest(db, create_test_user)
    row, token, _ = create(client, context, invitation_settings)
    client.cookies.clear()
    _, proof = inspect(client, row, token)
    data = command(row)

    def submit(_):
        with TestClient(client.app) as other:
            authorize_recipient(other, person)
            other.cookies.set(
                "guest_invitation_check", proof, domain="testserver.local", path="/"
            )
            result = other.post(
                f"/guest-invitations/{row['id']}/accept", json=data, headers=ORIGIN
            )
            assert result.status_code == 200, result.text
            return result.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert results[0] == results[1]
    assert db.scalar(select(func.count()).select_from(ProjectGuestParticipation)) == 1
    assert (
        db.scalar(
            select(func.count())
            .select_from(ProjectGuestInvitationEvent)
            .where(ProjectGuestInvitationEvent.kind == "accepted")
        )
        == 1
    )


def test_validation_does_not_reflect_secret_inputs(
    client, context, invitation_settings
):
    """新しい422もtoken・パスワード原文を返さない。"""
    row, token, _ = create(client, context, invitation_settings)
    client.cookies.clear()
    url = f"/guest-invitations/{row['id']}"
    inspect(client, row, token)
    for suffix, data, secret in [
        ("inspect", {"token": "short-secret-value"}, "short-secret-value"),
        (
            "register",
            command(row, name="本人", password="tinysecret"),
            "tinysecret",
        ),
    ]:
        response = client.post(f"{url}/{suffix}", json=data, headers=ORIGIN)
        assert response.status_code == 422, response.text
        assert secret not in response.text


def test_email_case_ambiguity_is_not_registered_again(
    client, db, context, create_test_user, invitation_settings
):
    """既存Identityの大小文字違いを別Userとして作らない。"""
    person = guest(db, create_test_user)
    person.email = EMAIL.upper()
    db.commit()
    row, token, _ = create(client, context, invitation_settings)
    client.cookies.clear()
    response = client.post(
        f"/guest-invitations/{row['id']}/inspect", json={"token": token}, headers=ORIGIN
    )
    assert response.status_code == 404
    assert (
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(func.lower(User.email) == EMAIL)
        )
        == 1
    )


def test_other_organization_internal_keeps_identity(
    client, db, context, create_test_user, invitation_settings
):
    """別組織の内部Identityをglobal guestへ変更しない。"""
    from app.models.tenant import Tenant

    person = guest(db, create_test_user)
    person.user_type = "internal"
    tenant = Tenant(name="別組織", slug="other-company")
    db.add(tenant)
    db.flush()
    role_id = db.scalar(
        select(TenantMember.role_id).where(TenantMember.user_id == person.id)
    )
    db.add(
        TenantMember(
            tenant_id=tenant.id, user_id=person.id, role_id=role_id, status="active"
        )
    )
    db.commit()
    row, token, _ = create(client, context, invitation_settings)
    client.cookies.clear()
    inspect(client, row, token)
    authorize_recipient(client, person)
    response = client.post(
        f"/guest-invitations/{row['id']}/accept", json=command(row), headers=ORIGIN
    )
    assert response.status_code == 200, response.text
    db.refresh(person)
    assert person.user_type == "internal"
    assert (
        db.scalar(
            select(func.count())
            .select_from(TenantMember)
            .where(TenantMember.user_id == person.id, TenantMember.status == "active")
        )
        == 1
    )


def test_local_optional_mfa_still_requires_verified_invitation_session(
    client, db, context, create_test_user, invitation_settings, monkeypatch
):
    """全体flagを変えず既存reauth/setup/TOTPで本人受諾へ戻す。"""
    person = guest(db, create_test_user)
    row, token, _ = create(client, context, invitation_settings)
    monkeypatch.setattr(settings, "mfa_encryption_keys", Fernet.generate_key().decode())
    path = f"/guest-invitations/{row['id']}"

    def post(url, data=None):
        client.headers[settings.csrf_header_name] = (
            client.cookies.get(settings.csrf_cookie_name) or ""
        )
        response = client.post(url, json=data, headers=ORIGIN)
        assert response.status_code == 200, response.text
        return response.json()

    client.cookies.clear()
    inspect(client, row, token)
    post("/auth/login", {"email": person.email, "password": "password123"})
    assert client.get(path).json()["next_step"] == "mfa"
    client.headers[settings.csrf_header_name] = client.cookies.get(
        settings.csrf_cookie_name
    )
    assert (
        client.post(path + "/accept", json=command(row), headers=ORIGIN).status_code
        == 403
    )
    assert (
        post("/auth/mfa/reauth", {"password": "password123"})["next_step"]
        == "mfa_setup"
    )
    setup = post("/auth/mfa/totp/setup", {"name": "LOCAL本人"})
    secret = parse_qs(urlsplit(setup["provisioning_uri"]).query)["secret"][0]
    codes = post("/auth/mfa/verify-code", {"code": pyotp.TOTP(secret).now()})[
        "recovery_codes"
    ]
    post("/auth/mfa/finish")
    assert client.get(path).json()["next_step"] == "accept"
    client.cookies.clear()
    inspect(client, row, token)
    post("/auth/login", {"email": person.email, "password": "password123"})
    assert client.get(path).json()["next_step"] == "mfa"
    assert (
        post("/auth/mfa/reauth", {"password": "password123"})["next_step"]
        == "mfa_verify"
    )
    assert len(codes) == 10
    post(
        "/auth/mfa/verify-code",
        {"code": pyotp.TOTP(secret).at(datetime.now(UTC) + timedelta(seconds=30))},
    )
    assert client.get(path).json()["next_step"] == "accept"
    post(path + "/accept", command(row))
    assert settings.mfa_required is False
