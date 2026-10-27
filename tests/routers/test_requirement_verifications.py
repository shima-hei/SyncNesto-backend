"""確認表の保存境界・固定対象・候補の意味を実DBで検証する。"""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError

from app.core.config import settings
from app.models.rbac import Permission, RolePermission
from app.models.requirement import RequirementDetail
from app.models.requirement_verification import RequirementVerificationPlan as Plan
from app.models.requirement_verification import (
    RequirementVerificationRevision as Revision,
)
from app.services.audit_log import AuditLogService
from tests.helpers.auth import authorize_as
from tests.routers.test_artifact_releases import all_nodes, preview_data, save
from tests.routers.test_artifact_releases import context as context


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "requirement_verification_preparation_enabled", True)


@pytest.fixture
def prepared(client, context):
    c = context
    release, _ = save(client, c, preview_data(c, kind="requirement", include=False))
    nodes = all_nodes(client, c, release["id"])
    node = next(n for n in nodes if n["node_kind"] == "requirement")
    c["url"] = f"/projects/{c['project'].id}/requirement-verification-plans"
    c["input"] = dict(
        source_requirement_id=c["req"].id,
        release_id=release["id"],
        target_node_id=node["id"],
        expected_release_digest=release["content_digest"],
        request_id=str(uuid4()),
    )
    response = client.post(c["url"], json=c["input"])
    assert response.status_code == 201, response.text
    c["plan"] = response.json()
    c["detail"] = c["url"] + "/" + c["plan"]["id"]
    return c


def manual_row(**values):
    return {
        "id": str(uuid4()),
        "description": "入力を保持する",
        "reference": {"field": "whole"},
        "method": "manual",
        "manual_plan": "画面を戻って確認",
        **values,
    }


def update(client, c, rows=None, **values):
    data = {
        "version": c["plan"]["current_version"],
        "operation_id": str(uuid4()),
        "checklist": {"rows": rows or []},
        **values,
    }
    return client.put(c["detail"], json=data), data


def test_minimal_idempotency_and_fixed_projection(client, prepared, db):
    c = prepared
    assert c["plan"]["revision"]["checklist"] == {"rows": [], "coverage": None}
    response = client.post(c["url"], json=c["input"])
    assert response.status_code == 200 and response.json()["id"] == c["plan"]["id"]
    assert (
        client.post(
            c["url"], json={**c["input"], "source_requirement_id": 9999}
        ).status_code
        == 409
    )
    assert db.scalar(select(func.count()).select_from(Plan)) == 1
    assert db.scalar(select(func.count()).select_from(Revision)) == 1
    detail = client.get(c["detail"])
    assert detail.headers["cache-control"] == "private, no-store"
    assert detail.headers["referrer-policy"] == "no-referrer"
    assert detail.json()["comparison"] == "same"
    assert "release_id" not in detail.json() and "sources" not in detail.json()
    d = db.scalar(
        select(RequirementDetail).where(RequirementDetail.requirement_id == c["req"].id)
    )
    d.detail_json = {"rule": "親versionを上げず詳細だけ変わった"}
    db.commit()
    detail = client.get(c["detail"]).json()
    assert detail["comparison"] == "changed"
    assert (
        detail["fixed_subject"]["details"][0]["payload"]["detail_json"] != d.detail_json
    )


def test_append_confirmation_idempotency_conflict_and_history(client, prepared):
    c = prepared
    response, data = update(client, c, [manual_row()], confirm_coverage=True)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["current_version"] == 2
    coverage = saved["revision"]["checklist"]["coverage"]
    assert (
        coverage["actor_id"] == c["user"].id
        and coverage["subject_digest"] == saved["subject_digest"]
    )
    assert client.put(c["detail"], json=data).json()["revision"] == saved["revision"]
    assert (
        client.put(c["detail"], json={**data, "confirm_coverage": False}).status_code
        == 409
    )
    assert update(client, c)[0].status_code == 409
    changed, _ = update(client, c, [manual_row()], version=2)
    assert (
        changed.status_code == 200
        and changed.json()["revision"]["checklist"]["coverage"] is None
    )
    history = client.get(c["detail"] + "/revisions", params={"page_size": 1}).json()
    assert history["items"][0]["version"] == 1 and history["next_cursor"]
    second = client.get(
        c["detail"] + "/revisions",
        params={"cursor": history["next_cursor"], "page_size": 1},
    ).json()
    assert second["items"][0]["version"] == 2


@pytest.mark.parametrize(
    "role,prepare",
    [("project_admin", True), ("manager", True), ("member", False), ("viewer", False)],
)
def test_role_projection_without_generic_release_read(
    client, prepared, create_test_user, assign_project_role, role, prepare
):
    c = prepared
    user = create_test_user(email="reader@example.com")
    assign_project_role(user=user, project=c["project"], role_key=role)
    authorize_as(client, user)
    response = client.get(c["detail"])
    assert response.status_code == 200, response.text
    assert response.json()["can_prepare"] == prepare
    assert update(client, c)[0].status_code == (200 if prepare else 403)
    if not prepare:
        assert client.get(c["base"] + "/" + c["input"]["release_id"]).status_code == 403


def test_domain_loss_disabled_cross_project_and_nonmember(
    client,
    prepared,
    db,
    create_test_project,
    create_test_user,
    assign_project_role,
    monkeypatch,
):
    c = prepared
    other = create_test_project()
    assign_project_role(user=c["user"], project=other, role_key="project_admin")
    assert (
        client.get(
            f"/projects/{other.id}/requirement-verification-plans/{c['plan']['id']}"
        ).status_code
        == 404
    )
    from app.models.project import ProjectMember

    role_id = db.scalar(
        select(ProjectMember.role_id).where(
            ProjectMember.project_id == c["project"].id,
            ProjectMember.user_id == c["user"].id,
        )
    )
    pid = db.scalar(select(Permission.id).where(Permission.code == "test_case:read"))
    db.execute(
        delete(RolePermission).where(
            RolePermission.role_id == role_id, RolePermission.permission_id == pid
        )
    )
    db.commit()
    result = client.get(c["url"]).json()
    assert result["state"] == "restricted" and result["items"] == []
    denied = client.get(c["detail"])
    assert denied.status_code == 403
    assert denied.headers["cache-control"] == "private, no-store"
    assert denied.headers["referrer-policy"] == "no-referrer"
    monkeypatch.setattr(settings, "requirement_verification_preparation_enabled", False)
    assert client.get(c["url"]).json()["state"] == "disabled"
    assert client.get(c["detail"]).status_code in (403, 404)
    outsider = create_test_user(email="outsider@example.com")
    authorize_as(client, outsider)
    assert client.get(c["url"]).status_code == 403


def test_validation_boundaries_quotes_and_no_forged_verdict(client, prepared):
    c = prepared
    assert update(client, c, confirm_coverage=True)[0].status_code == 400
    bad = manual_row(reference={"field": "description", "start": 0, "text": "違う"})
    assert update(client, c, [bad])[0].status_code == 400
    assert update(client, c, [manual_row(status="passed")])[0].status_code == 422
    assert update(client, c, [manual_row() for _ in range(101)])[0].status_code == 422
    assert (
        update(client, c, [manual_row(notes="あ" * 8000) for _ in range(20)])[
            0
        ].status_code
        == 413
    )
    response, _ = update(client, c, [manual_row(description="") for _ in range(100)])
    assert response.status_code == 200, response.text


def test_nonempty_migration_cannot_remove_history(client, prepared, db):
    from alembic.config import Config

    from alembic import command

    # 子表のFKを戻すDDLをfixtureの読み取りtransactionで待たせない。
    db.commit()
    with pytest.raises(RuntimeError, match="確認表"):
        command.downgrade(Config("alembic.ini"), "d6e0f3b8a214")
    assert client.get(prepared["detail"]).status_code == 200


def test_direct_sql_guards_and_retention(client, prepared, db):
    c = prepared
    for sql in [
        "DELETE FROM requirement_verification_plans WHERE id=:id",
        "UPDATE requirement_verification_plans "
        "SET source_requirement_id=999 WHERE id=:id",
        "UPDATE requirement_verification_plans "
        "SET current_version=current_version+1 WHERE id=:id",
        "UPDATE requirement_verification_plan_revisions "
        "SET checklist='{}' WHERE plan_id=:id",
        "DELETE FROM requirement_verification_plan_revisions WHERE plan_id=:id",
    ]:
        with pytest.raises(DBAPIError):
            db.execute(text(sql), {"id": c["plan"]["id"]})
            db.commit()
        db.rollback()
    # 回収計画にも確認表や固定版を含めない。
    from app.repositories.trash import TrashRepository

    purge = TrashRepository().purge_plan(db, "requirements", c["req"].id)
    assert (
        "requirement_verification_plans" not in purge
        and "artifact_releases" not in purge
    )
    # 固定対象の正本は元の詳細・要件を回収しても保持する。
    db.execute(
        text("DELETE FROM requirement_details WHERE requirement_id=:id"),
        {"id": c["req"].id},
    )
    db.execute(text("DELETE FROM requirements WHERE id=:id"), {"id": c["req"].id})
    db.commit()
    read = client.get(c["detail"])
    assert read.status_code == 200 and read.json()["comparison"] == "unavailable"
    assert read.json()["fixed_subject"]["payload"]["title"] == "保存を続けられる"


def test_audit_failure_rolls_back_and_concurrent_save(
    client, prepared, db, monkeypatch
):
    c = prepared

    def fail(*args, **kwargs):
        raise RuntimeError("audit failed")

    with monkeypatch.context() as patch:
        patch.setattr(AuditLogService, "record", fail)
        with pytest.raises(RuntimeError, match="audit failed"):
            update(client, c, [manual_row()])
    assert client.get(c["detail"]).json()["current_version"] == 1
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: update(client, c, [manual_row()])[0].status_code, range(2)
            )
        )
    assert sorted(results) == [200, 409]
    assert (
        db.scalar(
            select(func.count())
            .select_from(Revision)
            .where(Revision.plan_id == UUID(c["plan"]["id"]))
        )
        == 2
    )
