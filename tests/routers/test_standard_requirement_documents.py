"""標準章付き文書の原子性・再送・権限境界。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import uuid4

import pytest

from app.models.rbac import Permission, Role, RolePermission
from app.models.requirement import (
    RequirementChangeLog,
    RequirementDocument,
    RequirementSection,
)
from app.models.requirement_document_creation import RequirementDocumentCreationReceipt
from app.repositories.requirement_section import RequirementSectionRepository
from tests.helpers.auth import authorize_as


@pytest.fixture
def context(client, db, create_test_user, create_test_project, assign_project_role):
    actor = create_test_user(email="standard-creator@example.com")
    project = create_test_project()
    assign_project_role(user=actor, project=project, role_key="manager")
    authorize_as(client, actor)
    return actor, project, f"/projects/{project.id}/requirement-documents/standard"


def payload(**changes):
    return {
        "title": "標準文書",
        "document_code": "DOC-STANDARD",
        "request_key": str(uuid4()),
        **changes,
    }


def test_atomic_create_and_replay_preserve_edited_sections(client, db, context):
    _, _, url = context
    data = payload()
    first = client.post(url, json=data)
    assert first.status_code == 201
    assert first.json()["replayed"] is False
    document_id = first.json()["document"]["id"]
    sections = (
        db.query(RequirementSection)
        .filter_by(document_id=document_id)
        .order_by(RequirementSection.sort_order)
        .all()
    )
    assert [s.title for s in sections] == [
        "背景・目的",
        "対象範囲",
        "利用者・業務",
        "機能要件",
        "非機能要件",
        "受け入れ条件",
        "未決事項・制約",
    ]
    assert [s.sort_order for s in sections] == list(range(10, 71, 10))
    assert all(s.content is None and s.status == "draft" for s in sections)
    assert (
        db.query(RequirementChangeLog).filter_by(document_id=document_id).count() == 8
    )
    sections[0].content = "本人が保存した本文"
    db.commit()
    replay = client.post(url, json=data)
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["document"]["id"] == document_id
    db.expire_all()
    assert db.get(RequirementSection, sections[0].id).content == "本人が保存した本文"
    assert db.query(RequirementSection).filter_by(document_id=document_id).count() == 7
    assert (
        db.query(RequirementChangeLog).filter_by(document_id=document_id).count() == 8
    )


def test_changed_payload_is_conflict_without_another_document(client, db, context):
    _, _, url = context
    data = payload()
    assert client.post(url, json=data).status_code == 201
    response = client.post(url, json={**data, "title": "別の内容"})
    assert response.status_code == 409
    assert response.json()["code"] == "REQUIREMENT_DOCUMENT_CREATION_KEY_CONFLICT"
    assert db.query(RequirementDocument).count() == 1


@pytest.mark.parametrize("failure", ["section", "history"])
def test_midway_failure_rolls_back_all_rows(client, db, context, monkeypatch, failure):
    from app.services.requirement_change_log import RequirementChangeLogService

    _, _, url = context
    calls = 0
    target = (
        RequirementSectionRepository
        if failure == "section"
        else RequirementChangeLogService
    )
    method = "create" if failure == "section" else "record"
    original = getattr(target, method)

    def fail(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("simulated storage failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(target, method, fail)
    with pytest.raises(RuntimeError, match="simulated storage failure"):
        client.post(url, json=payload())
    for model in [
        RequirementDocument,
        RequirementSection,
        RequirementChangeLog,
        RequirementDocumentCreationReceipt,
    ]:
        assert db.query(model).count() == 0


@pytest.mark.parametrize("physical", [False, True])
def test_deleted_result_is_not_recreated(client, db, context, physical):
    _, _, url = context
    data = payload()
    document_id = client.post(url, json=data).json()["document"]["id"]
    if physical:
        db.query(RequirementChangeLog).filter_by(document_id=document_id).delete()
        db.query(RequirementSection).filter_by(document_id=document_id).delete()
        db.query(RequirementDocument).filter_by(id=document_id).delete()
    else:
        db.get(RequirementDocument, document_id).deleted_at = datetime.now(UTC)
    db.commit()
    response = client.post(url, json=data)
    assert response.status_code == 409
    assert response.json()["code"] == "REQUIREMENT_DOCUMENT_CREATION_TARGET_DELETED"
    assert db.query(RequirementDocumentCreationReceipt).count() == 1
    assert db.query(RequirementDocument).count() == (0 if physical else 1)


@pytest.mark.parametrize("permission", ["requirement:create", "requirement:update"])
def test_both_permissions_are_required(
    client, db, context, assign_project_role, permission
):
    actor, project, url = context
    role = Role(key="one-permission", name="One permission", scope="project")
    db.add(role)
    db.flush()
    row = db.query(Permission).filter_by(code=permission).one()
    db.add(RolePermission(role_id=role.id, permission_id=row.id))
    from app.models.project import ProjectMember

    db.query(ProjectMember).filter_by(project_id=project.id, user_id=actor.id).update(
        {"role_id": role.id}
    )
    db.commit()
    assert client.post(url, json=payload()).status_code == 403
    assert db.query(RequirementDocument).count() == 0


def test_unauthenticated_and_csrf_are_rejected(client, db, context):
    _, _, url = context
    del client.headers["X-CSRF-Token"]
    assert client.post(url, json=payload()).status_code == 403
    client.cookies.clear()
    assert client.post(url, json=payload()).status_code == 401
    assert db.query(RequirementDocument).count() == 0


@pytest.mark.parametrize("different", [False, True])
def test_concurrent_same_key_has_one_result(client, db, context, different):
    _, _, url = context
    data = payload()
    barrier = Barrier(2)

    def send(index):
        barrier.wait(timeout=10)
        return client.post(
            url, json={**data, "document_code": f"DOC-{index}"} if different else data
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(send, [0, 1]))
    assert sorted(r.status_code for r in responses) == (
        [201, 409] if different else [200, 201]
    )
    assert db.query(RequirementDocument).count() == 1
    assert db.query(RequirementSection).count() == 7
    assert db.query(RequirementDocumentCreationReceipt).count() == 1


def test_legacy_post_still_creates_no_sections(client, db, context):
    _, project, _ = context
    response = client.post(
        f"/projects/{project.id}/requirement-documents",
        json={"title": "従来", "document_code": "LEGACY"},
    )
    assert response.status_code == 201
    assert "replayed" not in response.json()
    assert db.query(RequirementSection).count() == 0
    assert db.query(RequirementDocumentCreationReceipt).count() == 0


def test_replay_rechecks_membership_and_session(client, db, context):
    from app.models.project import ProjectMember
    from app.models.session import UserSession

    actor, project, url = context
    data = payload()
    assert client.post(url, json=data).status_code == 201
    db.query(ProjectMember).filter_by(project_id=project.id, user_id=actor.id).delete()
    db.commit()
    assert client.post(url, json=data).status_code == 403
    db.query(UserSession).filter_by(user_id=actor.id).update(
        {"revoked_at": datetime.now(UTC)}
    )
    db.commit()
    assert client.post(url, json=data).status_code == 401
    assert db.query(RequirementDocument).count() == 1
    assert db.query(RequirementChangeLog).count() == 8


def test_same_key_is_separate_for_actor_and_project(
    client, db, context, create_test_user, create_test_project, assign_project_role
):
    actor, _, url = context
    data = payload()
    first = client.post(url, json=data).json()["document"]["id"]
    another_project = create_test_project(name="Another project")
    assign_project_role(user=actor, project=another_project, role_key="manager")
    other_url = f"/projects/{another_project.id}/requirement-documents/standard"
    second = client.post(other_url, json=data)
    assert second.status_code == 201
    assert second.json()["document"]["id"] != first
    other_actor = create_test_user(email="another-creator@example.com")
    assign_project_role(user=other_actor, project=another_project, role_key="manager")
    authorize_as(client, other_actor)
    third = client.post(other_url, json={**data, "document_code": "OTHER-ACTOR"})
    assert third.status_code == 201
    assert third.json()["document"]["id"] not in [
        first,
        second.json()["document"]["id"],
    ]
    assert db.query(RequirementDocumentCreationReceipt).count() == 3


def test_system_administrator_without_project_role_cannot_create(
    client, db, context, create_test_user
):
    _, _, url = context
    administrator = create_test_user(
        email="system@example.com", system_role="system_admin"
    )
    authorize_as(client, administrator)
    assert client.post(url, json=payload()).status_code == 403
    assert db.query(RequirementDocument).count() == 0


def test_mfa_gate_is_not_bypassed(client, db, context, monkeypatch):
    from app.core.config import settings

    _, _, url = context
    monkeypatch.setattr(settings, "mfa_required", True)
    assert client.post(url, json=payload()).status_code == 403
    assert db.query(RequirementDocument).count() == 0


def test_selected_tenant_cannot_reuse_another_tenants_project(client, db, context):
    from app.models.tenant import Tenant, TenantMember

    actor, _, url = context
    tenant = Tenant(name="Another organization", slug="another-organization")
    db.add(tenant)
    db.flush()
    role = db.query(Role).filter_by(key="tenant_member", scope="tenant").one()
    db.add(
        TenantMember(
            tenant_id=tenant.id,
            user_id=actor.id,
            role_id=role.id,
            display_name=actor.name,
        )
    )
    db.commit()
    assert (
        client.post(
            url, json=payload(), headers={"X-Tenant-ID": str(tenant.id)}
        ).status_code
        == 403
    )
    assert db.query(RequirementDocument).count() == 0
