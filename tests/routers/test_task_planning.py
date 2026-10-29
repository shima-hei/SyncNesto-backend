"""ガントの行単位計画更新契約と旧更新の互換性。"""

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.models.audit_log import AuditLog
from app.models.notification import Notification
from app.models.task import TaskChangeLog
from app.models.tenant import Tenant, TenantMember
from app.schemas.task import TaskPlanningUpdate
from app.services.notification import NotificationService
from tests.helpers.auth import authorize_as


@pytest.fixture
def planning_context(
    client, create_test_user, create_test_project, assign_project_role, create_test_task
):
    """Task更新権限を持つ本人と、未補完のTaskを用意する。"""
    actor = create_test_user(email="planning-actor@example.com")
    project = create_test_project(project_code="PLANNING")
    assign_project_role(user=actor, project=project, role_key="member")
    task = create_test_task(project=project)
    authorize_as(client, actor)
    return actor, project, task


@pytest.mark.parametrize("status,progress", [("in_progress", 40), ("done", 100)])
def test_planning_updates_only_planned_fields_and_uses_shared_history(
    client, db, planning_context, status, progress
):
    """計画保存では実績を補完せず、既存根拠・所属・履歴契約を保つ。"""
    actor, _, task = planning_context
    task.status = status
    task.progress_percent = progress
    task.actual_start_date = None
    task.actual_end_date = None
    task.actual_minutes = 77
    task.estimated_minutes = 123
    task.estimated_basis = "unreviewed"
    task.actual_basis = "rollup"
    task.phase_scope = "shared"
    db.commit()
    before = client.get(f"/tasks/{task.id}").json()

    response = client.patch(
        f"/tasks/{task.id}/planning",
        json={
            "version": before["version"],
            "assignee_id": actor.id,
            "start_date": "2026-11-01",
            "due_date": "2026-11-02",
            "estimated_minutes": 0,
            "change_reason": "予定の調整",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] == before["version"] + 1
    assert body["assignee_id"] == actor.id
    assert body["start_date"] == "2026-11-01"
    assert body["due_date"] == "2026-11-02"
    assert body["estimated_minutes"] == 0
    for field in (
        "id",
        "project_id",
        "task_code",
        "parent_task_id",
        "status",
        "progress_percent",
        "actual_start_date",
        "actual_end_date",
        "actual_minutes",
        "phase_scope",
        "phase_id",
        "estimated_basis",
        "actual_basis",
        "requirements",
    ):
        assert body[field] == before[field], field
    history = client.get(f"/tasks/{task.id}/change-logs").json()["items"]
    assert len(history) == 1
    assert history[0]["action"] == "updated"
    assert history[0]["reason"] == "予定の調整"
    assert history[0]["old_value"]["snapshot"]["estimated_minutes"] == 123
    assert history[0]["new_value"]["snapshot"]["estimated_minutes"] == 0
    assert set(history[0]["new_value"]["updated_fields"]) == {
        "assignee_id",
        "start_date",
        "due_date",
        "estimated_minutes",
    }
    audit = db.query(AuditLog).filter_by(event_type="task.updated").one()
    assert audit.actor_user_id == actor.id
    assert set(audit.extra_metadata["updated_fields"]) == {
        "assignee_id",
        "start_date",
        "due_date",
        "estimated_minutes",
    }


def test_planning_distinguishes_omission_null_zero_and_one_sided_dates(
    client, db, planning_context
):
    """省略は保持、nullは解除、0は明示値、片側予定は許可する。"""
    actor, _, task = planning_context
    task.assignee_id = actor.id
    task.start_date = date(2026, 11, 1)
    task.due_date = date(2026, 11, 2)
    task.estimated_minutes = 90
    db.commit()
    first = client.patch(
        f"/tasks/{task.id}/planning",
        json={"version": task.version, "due_date": None, "estimated_minutes": 0},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["assignee_id"] == actor.id
    assert body["start_date"] == "2026-11-01"
    assert body["due_date"] is None
    assert body["estimated_minutes"] == 0
    second = client.patch(
        f"/tasks/{task.id}/planning",
        json={
            "version": body["version"],
            "assignee_id": None,
            "start_date": None,
            "estimated_minutes": None,
        },
    )
    assert second.status_code == 200, second.text
    body = second.json()
    assert all(
        body[field] is None
        for field in ("assignee_id", "start_date", "due_date", "estimated_minutes")
    )
    third = client.patch(
        f"/tasks/{task.id}/planning",
        json={"version": body["version"], "due_date": "2026-11-02"},
    )
    assert third.status_code == 200, third.text
    assert third.json()["start_date"] is None
    assert third.json()["due_date"] == "2026-11-02"


def test_planning_conflict_is_per_task_and_returns_current(
    client, db, planning_context, create_test_task
):
    """一行の競合は成功済みの他行を戻さず、競合行を部分更新しない。"""
    _, project, task = planning_context
    other = create_test_task(project=project)
    initial_version = task.version
    first = client.patch(
        f"/tasks/{task.id}/planning",
        json={"version": initial_version, "estimated_minutes": 10},
    )
    success = client.patch(
        f"/tasks/{other.id}/planning",
        json={"version": other.version, "estimated_minutes": 20},
    )
    conflict = client.patch(
        f"/tasks/{task.id}/planning",
        json={
            "version": initial_version,
            "estimated_minutes": 30,
            "due_date": "2026-11-02",
        },
    )
    assert first.status_code == success.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "VERSION_CONFLICT"
    current = conflict.json()["current"]
    assert current["version"] == first.json()["version"]
    assert current["estimated_minutes"] == 10
    assert current["due_date"] is None
    assert client.get(f"/tasks/{other.id}").json()["estimated_minutes"] == 20
    assert db.query(TaskChangeLog).count() == 2


def test_planning_rejects_reversed_dates_without_changing_unrelated_legacy_values(
    client, db, planning_context, create_test_user
):
    """日付変更だけ整合性を確認し、省略した旧担当や予定を補正しない。"""
    _, _, task = planning_context
    legacy_assignee = create_test_user(email="former-member@example.com")
    legacy_assignee.is_active = False
    task.assignee_id = legacy_assignee.id
    task.start_date = date(2026, 11, 3)
    task.due_date = date(2026, 11, 2)
    db.commit()
    initial_version = task.version
    kept = client.patch(
        f"/tasks/{task.id}/planning",
        json={"version": initial_version, "estimated_minutes": 15},
    )
    assert kept.status_code == 200, kept.text
    assert kept.json()["assignee_id"] == legacy_assignee.id
    assert kept.json()["start_date"] == "2026-11-03"
    rejected = client.patch(
        f"/tasks/{task.id}/planning",
        json={
            "version": kept.json()["version"],
            "start_date": "2026-11-04",
            "estimated_minutes": 99,
        },
    )
    assert rejected.status_code == 400
    assert rejected.json()["code"] == "BAD_REQUEST"
    current = client.get(f"/tasks/{task.id}").json()
    assert current["version"] == kept.json()["version"]
    assert current["start_date"] == "2026-11-03"
    assert current["estimated_minutes"] == 15


@pytest.mark.parametrize(
    "invalid", ["outsider", "inactive", "removed", "suspended", "other_tenant"]
)
def test_planning_rejects_ineligible_new_assignees_atomically(
    client, db, planning_context, create_test_user, assign_project_role, invalid
):
    """新しい担当は有効本人・同じ組織・Project所属を全て満たす。"""
    _, project, task = planning_context
    candidate = create_test_user(email="candidate@example.com")
    member = None
    if invalid != "outsider":
        member = assign_project_role(user=candidate, project=project, role_key="member")
    if invalid == "inactive":
        candidate.is_active = False
    elif invalid == "removed":
        assert member is not None
        member.deleted_at = datetime.now(UTC)
    elif invalid in {"suspended", "other_tenant"}:
        membership = db.query(TenantMember).filter_by(user_id=candidate.id).one()
        if invalid == "suspended":
            membership.status = "suspended"
        else:
            other = Tenant(name="Other", slug="planning-other")
            db.add(other)
            db.flush()
            membership.tenant_id = other.id
    db.commit()
    version = task.version
    response = client.patch(
        f"/tasks/{task.id}/planning",
        json={"version": version, "assignee_id": candidate.id, "estimated_minutes": 25},
    )
    assert response.status_code == 400, response.text
    current = client.get(f"/tasks/{task.id}").json()
    assert current["version"] == version
    assert current["assignee_id"] is None
    assert current["estimated_minutes"] is None
    assert db.query(TaskChangeLog).count() == db.query(Notification).count() == 0


@pytest.mark.parametrize("actor_kind", ["viewer", "outsider", "system_admin"])
def test_planning_enforces_existing_task_update_permission(
    client, planning_context, create_test_user, assign_project_role, actor_kind
):
    """viewerや所属のない運営権限だけでは計画を更新できない。"""
    _, project, task = planning_context
    actor = create_test_user(
        email="unauthorized@example.com",
        system_role="system_admin" if actor_kind == "system_admin" else None,
    )
    if actor_kind == "viewer":
        assign_project_role(user=actor, project=project, role_key="viewer")
    authorize_as(client, actor)
    response = client.patch(
        f"/tasks/{task.id}/planning",
        json={"version": task.version, "estimated_minutes": 25},
    )
    assert response.status_code in {403, 404}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"change_reason": "理由だけ"},
        {"estimated_minutes": -1},
        {"estimated_minutes": 1.5},
        {"estimated_minutes": True},
        {"estimated_minutes": "12"},
        {"start_date": "not-a-date"},
        {"estimated_minutes": 12, "status": "done"},
        {"estimated_minutes": 12, "actual_start_date": None},
        {"estimated_minutes": 12, "estimated_basis": "own"},
    ],
)
@pytest.mark.no_db
def test_planning_schema_rejects_non_planning_or_invalid_requests(payload):
    """計画以外の副作用項目と空更新・曖昧な数値を受け付けない。"""
    with pytest.raises(ValidationError):
        TaskPlanningUpdate.model_validate({"version": 1, **payload})


def test_planning_route_returns_422_for_forbidden_fields(client, db, planning_context):
    """対象外フィールドはHTTPでも無視せず422とする。"""
    _, _, task = planning_context
    response = client.patch(
        f"/tasks/{task.id}/planning",
        json={"version": task.version, "estimated_minutes": 12, "status": "done"},
    )
    assert response.status_code == 422
    assert db.query(TaskChangeLog).count() == 0
    current = client.get(f"/tasks/{task.id}").json()
    assert current["estimated_minutes"] is None
    assert current["version"] == 1


def test_planning_notification_failure_rolls_back_all_planned_fields(
    client, db, planning_context, create_test_user, assign_project_role, monkeypatch
):
    """担当通知に失敗した行は予定・見積・version・履歴も保存しない。"""
    _, project, task = planning_context
    candidate = create_test_user(email="next-assignee@example.com")
    assign_project_role(user=candidate, project=project, role_key="member")

    def fail(*args, **kwargs):
        raise RuntimeError("notification failure")

    monkeypatch.setattr(NotificationService, "assignment_changed", fail)
    with pytest.raises(RuntimeError, match="notification failure"):
        client.patch(
            f"/tasks/{task.id}/planning",
            json={
                "version": task.version,
                "assignee_id": candidate.id,
                "start_date": "2026-11-01",
                "estimated_minutes": 60,
            },
        )
    current = client.get(f"/tasks/{task.id}").json()
    assert current["assignee_id"] is None
    assert current["start_date"] is None
    assert current["estimated_minutes"] is None
    assert current["version"] == 1
    assert db.query(TaskChangeLog).count() == db.query(AuditLog).count() == 0


def test_legacy_patch_still_applies_its_existing_actual_date_normalization(
    client, planning_context
):
    """専用APIの追加で旧PATCHの実績日補完の意味を変更しない。"""
    _, _, task = planning_context
    first = client.patch(
        f"/tasks/{task.id}",
        json={"version": task.version, "status": "in_progress", "progress_percent": 40},
    )
    assert first.status_code == 200, first.text
    assert first.json()["actual_start_date"] == date.today().isoformat()


def test_planning_estimate_respects_integer_storage_boundary(client, planning_context):
    """格納できる最大値は保存し、1超過はDB例外ではなく422にする。"""
    _, _, task = planning_context
    maximum = client.patch(
        f"/tasks/{task.id}/planning",
        json={"version": task.version, "estimated_minutes": 2_147_483_647},
    )
    assert maximum.status_code == 200, maximum.text
    assert maximum.json()["estimated_minutes"] == 2_147_483_647
    rejected = client.patch(
        f"/tasks/{task.id}/planning",
        json={"version": maximum.json()["version"], "estimated_minutes": 2_147_483_648},
    )
    assert rejected.status_code == 422, rejected.text
    current = client.get(f"/tasks/{task.id}").json()
    assert current["version"] == maximum.json()["version"]
    assert current["estimated_minutes"] == 2_147_483_647


@pytest.mark.parametrize(
    "field,expected_status", [("assignee_id", 400), ("version", 409)]
)
def test_planning_large_reference_values_fail_without_driver_error(
    client, planning_context, field, expected_status
):
    """未解決の巨大IDと不一致versionはDB書込に渡らず既存エラーとなる。"""
    _, _, task = planning_context
    response = client.patch(
        f"/tasks/{task.id}/planning",
        json={"version": task.version, "estimated_minutes": 10, field: 2**100},
    )
    assert response.status_code == expected_status, response.text
    current = client.get(f"/tasks/{task.id}").json()
    assert current["version"] == 1
    assert current["estimated_minutes"] is None
