"""共有の現在認証と、既存資源7種の削除境界を追加検証する。"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text, update

from app.core.config import settings
from app.models.artifact_publication_access import ArtifactPublicationGrant
from app.models.rbac import Permission, Role, RolePermission
from app.models.session import UserSession
from app.models.test_design import TestDesign as DesignModel
from tests.helpers.auth import authorize_as
from tests.routers.test_artifact_publications import create, nodes, publication_base
from tests.routers.test_artifact_releases import context as context  # noqa: F401
from tests.routers.test_artifact_test_design import freeze
from tests.routers.test_publication_access import access_fixture as access_fixture  # noqa: F401
from tests.routers.test_publication_access import denied, publish, shared_path
from tests.routers.test_test_designs import design_context as design_context  # noqa: F401
from tests.routers.test_test_designs import graph
from tests.routers.test_trash import context as trash_context  # noqa: F401


@pytest.mark.parametrize(
    "state", ["session_revoked", "session_expired", "mfa", "initial_password"]
)
def test_shared_rechecks_identity(client, db, access_fixture, monkeypatch, state):
    """元許可が有効でも現在の本人認証を省略しない。"""
    ctx = access_fixture
    publish(client, ctx)
    person = ctx["guests"][0][0]
    sid = authorize_as(client, person)
    assert client.get(shared_path(ctx)).status_code == 200
    if state == "session_revoked":
        db.get(UserSession, sid).revoked_at = datetime.now(UTC)
    elif state == "session_expired":
        db.get(UserSession, sid).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif state == "initial_password":
        person.password_change_required = True
    else:
        monkeypatch.setattr(settings, "mfa_required", True)
    db.commit()
    response = client.get(shared_path(ctx))
    assert response.status_code in {401, 403}, response.text
    assert response.headers["cache-control"] == "private, no-store"


def test_publish_csrf_and_stop_after_source_permission_loss(client, db, access_fixture):
    """公開にCSRFを要求し、本文権限を失っても停止は実行できる。"""
    ctx = access_fixture
    _, url, data = publish(client, ctx)
    csrf = client.headers.pop(settings.csrf_header_name)
    assert client.post(url, json=data).status_code == 403
    client.headers[settings.csrf_header_name] = csrf
    role = db.scalar(
        select(Role).where(Role.key == "project_admin", Role.scope == "project")
    )
    permission = db.scalar(
        select(Permission).where(Permission.code == "requirement:read")
    )
    assert role is not None and permission is not None
    db.execute(
        delete(RolePermission).where(
            RolePermission.role_id == role.id,
            RolePermission.permission_id == permission.id,
        )
    )
    db.commit()
    base = f"{publication_base(ctx)}/{ctx['publication']['content']['id']}"
    assert client.get(base).status_code == 403
    assert (
        client.post(
            base + "/withdraw",
            json=dict(request_id=str(uuid4()), version=2, reason="本文権限なしで停止"),
        ).status_code
        == 200
    )


def test_expiry_from_clock_and_cross_tenant_header_ignored(client, db, access_fixture):
    """他組織headerで権限を増やさず、DB時刻を超えた許可を返さない。"""
    ctx = access_fixture
    expiry = datetime.now(UTC) + timedelta(seconds=2)
    publish(client, ctx, expires_at=expiry)
    authorize_as(client, ctx["guests"][0][0])
    client.headers["X-Tenant-ID"] = "999999"
    assert client.get(shared_path(ctx)).status_code == 200
    grant = db.scalar(select(ArtifactPublicationGrant))
    assert grant is not None
    assert (
        client.get("/shared/publications", params={"q": "存在しない内容"}).json()[
            "total"
        ]
        == 0
    )
    db.execute(
        text(
            "SELECT pg_sleep(greatest(0, "
            "extract(epoch FROM (:expiry - clock_timestamp())) + 0.01))"
        ),
        {"expiry": grant.expires_at},
    )
    denied(client, ctx)


def test_test_design_source_edit_keeps_fixed_and_delete_stops(
    client, db, design_context, create_test_user
):
    """テスト項目編集は固定本文を変えず、設計全体削除は公開を止める。"""
    from app.models.artifact_publication_access import ProjectGuestParticipation
    from app.models.tenant import TenantMember

    _, url, design, actor, project = design_context
    response = client.put(url, json=graph(design))
    assert response.status_code == 200
    release, _ = freeze(
        client, f"/projects/{project.id}/artifact-releases", response.json()
    )
    publication, _ = create(client, {"project": project}, release)
    person = create_test_user(email="test-design-guest@example.com")
    db.execute(
        update(TenantMember)
        .where(TenantMember.user_id == person.id)
        .values(status="removed")
    )
    person.user_type = "guest"
    db.commit()
    part = ProjectGuestParticipation(
        id=uuid4(),
        project_id=project.id,
        user_id=person.id,
        generation=1,
        display_label="テスト設計閲覧",
        status="active",
        version=1,
        expires_at=datetime.now(UTC) + timedelta(days=2),
        created_by=actor.id,
    )
    db.add(part)
    db.commit()
    authorize_as(client, actor)
    ctx = dict(project=project, publication=publication, guests=[(person, part)])
    publish(client, ctx)
    original = nodes(client, publication_base(ctx), publication["content"]["id"])
    db.execute(
        update(DesignModel)
        .where(DesignModel.id == design["id"])
        .values(name="元を編集")
    )
    db.commit()
    authorize_as(client, person)
    assert client.get(shared_path(ctx)).status_code == 200
    db.execute(
        update(DesignModel)
        .where(DesignModel.id == design["id"])
        .values(deleted_at=datetime.now(UTC))
    )
    db.commit()
    denied(client, ctx)
    authorize_as(client, actor)
    assert (
        nodes(client, publication_base(ctx), publication["content"]["id"]) == original
    )


def test_purge_preflight_failure_never_deletes_storage(db, request, monkeypatch):
    """公開境界の失敗を注入して、Storageより前に中断することを確認する。"""
    from app.models.document import DocumentAttachment, ProjectDocument
    from app.services.storage import StorageService
    from app.services.trash import TrashService
    from tests.fakes.storage import MemoryS3Client

    user, project, _, identity = request.getfixturevalue("trash_context")
    storage = MemoryS3Client()
    storage.put_object(
        Key="preflight/file.txt", Body=b"dummy", ContentType="text/plain"
    )
    db.add(
        DocumentAttachment(
            document_id=identity,
            filename="file.txt",
            content_type="text/plain",
            byte_size=5,
            storage_key="preflight/file.txt",
            uploaded_by=user.id,
        )
    )
    document = db.get(ProjectDocument, identity)
    assert document is not None
    document.deleted_at = datetime.now(UTC) - timedelta(days=31)
    db.commit()
    original = db.execute

    def fail(statement, *args, **kwargs):
        if "publication_lock" in str(statement):
            raise RuntimeError("injected publication preflight failure")
        return original(statement, *args, **kwargs)

    monkeypatch.setattr(db, "execute", fail)
    with pytest.raises(RuntimeError, match="preflight failure"):
        TrashService(StorageService(storage)).purge_one(
            db,
            dict(kind="document", project_id=project.id, id=str(identity)),
            datetime.now(UTC),
        )
    db.rollback()
    assert "preflight/file.txt" in storage.objects
    assert db.get(ProjectDocument, identity) is not None
