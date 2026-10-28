"""工程の所属・認可・履歴と一意Task工数の回帰。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError
from app.models.task import RequirementTaskRelation, Task, TaskChangeLog
from app.models.task_phase import ProjectPhaseEvent
from app.schemas.task import TaskUpdate
from app.schemas.task_phase import PhaseAssignment, PhaseUpdate
from app.services.task import TaskService
from app.services.task_phase import TaskPhaseService
from tests.helpers.auth import authorize_as


@pytest.fixture
def phase_context(client, create_test_user, create_test_project, assign_project_role):
    user = create_test_user(email="phase-admin@example.com")
    project = create_test_project()
    assign_project_role(user=user, project=project, role_key="project_admin")
    authorize_as(client, user)
    return user, project


def create_phase(client, project, name="設計"):
    result = client.post(f"/projects/{project.id}/phases", json={"name": name})
    assert result.status_code == 201, result.text
    return result.json()


def test_phase_only_edit_does_not_fill_legacy_actual_dates(
    client, phase_context, create_test_task, db
):
    _, project = phase_context
    phase = create_phase(client, project)
    task = create_test_task(project=project, status="in_progress", progress_percent=40)
    assert task.actual_start_date is None
    response = client.patch(
        f"/tasks/{task.id}",
        json={
            "version": task.version,
            "phase_assignment": {"kind": "phase", "phase_id": phase["id"]},
        },
    )
    assert response.status_code == 200
    assert response.json()["actual_start_date"] is None
    assert response.json()["actual_end_date"] is None
    assert response.json()["progress_percent"] == 40


def test_archive_and_assignment_are_serialized_without_partial_task_save(
    client, phase_context, db
):
    user, project = phase_context
    phase = create_phase(client, project)
    task = client.post(f"/projects/{project.id}/tasks", json={"title": "保持"}).json()
    db.commit()
    TaskPhaseService().read(db, project.id, phase["id"], lock=True)
    entered = Event()
    bind = db.get_bind()

    def assign():
        with Session(bind) as worker:
            entered.set()
            return TaskService().update_task(
                worker,
                task_id=task["id"],
                task_in=TaskUpdate(
                    version=1,
                    title="部分保存禁止",
                    phase_assignment=PhaseAssignment(
                        kind="phase", phase_id=phase["id"]
                    ),
                ),
                actor_id=user.id,
            )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(assign)
        assert entered.wait(2)
        assert not future.done()
        TaskPhaseService().save(
            db, project.id, PhaseUpdate(version=1, archived=True), user.id, phase["id"]
        )
        with pytest.raises(BadRequestError):
            future.result(timeout=5)
    current = client.get(f"/tasks/{task['id']}").json()
    assert (
        current["title"] == "保持"
        and current["phase_scope"] == "unclassified"
        and current["version"] == 1
    )


@pytest.mark.parametrize("role", ["manager", "member", "viewer"])
def test_phase_management_does_not_expand_existing_project_permission(
    client, phase_context, create_test_user, assign_project_role, role
):
    _, project = phase_context
    user = create_test_user()
    assign_project_role(user=user, project=project, role_key=role)
    authorize_as(client, user)
    assert client.get(f"/projects/{project.id}/phases").status_code == 200
    assert (
        client.post(f"/projects/{project.id}/phases", json={"name": "禁止"}).status_code
        == 403
    )
    assert client.get(f"/projects/{project.id}/tasks/effort-summary").status_code == 200


def test_status_breakdown_preserves_done_cancelled_and_incomplete_totals(
    client, phase_context
):
    _, project = phase_context
    url = f"/projects/{project.id}/tasks/effort-summary"
    assert client.get(url).json()["status_groups"] == []
    phase = create_phase(client, project)
    for status, estimated, actual in (
        ("done", 60, 30),
        ("cancelled", 40, 20),
        ("backlog", 60, None),
        ("in_review", 999, 7),
    ):
        payload = {
            "title": status,
            "status": status,
            "progress_percent": 100 if status == "done" else 0,
            "estimated_minutes": estimated,
            "actual_minutes": actual,
        }
        if status in {"done", "cancelled"}:
            payload["phase_assignment"] = {"kind": "phase", "phase_id": phase["id"]}
        elif status == "in_review":
            payload.update(
                phase_assignment={"kind": "shared"},
                estimated_basis="unreviewed",
                actual_basis="rollup",
            )
        response = client.post(f"/projects/{project.id}/tasks", json=payload)
        assert response.status_code == 201, response.text
    summary = client.get(url).json()
    assert summary["totals"]["estimated"]["minutes"] == 160
    assert summary["totals"]["estimated"]["unreviewed_count"] == 1
    assert summary["totals"]["actual"]["minutes"] == 50
    assert summary["totals"]["actual"]["missing_count"] == 1
    assert len(summary["groups"]) == 3
    for groups in (summary["groups"], summary["status_groups"]):
        assert sum(g["totals"]["task_count"] for g in groups) == 4
        for metric in ("estimated", "actual"):
            for key in ("minutes", "missing_count", "unreviewed_count"):
                assert (
                    sum(g["totals"][metric][key] for g in groups)
                    == summary["totals"][metric][key]
                )
    done = client.get(url, params={"status": "done"}).json()
    assert [g["status"] for g in done["status_groups"]] == ["done"]
    assert done["totals"]["estimated"]["minutes"] == 60


def test_archive_preserves_task_values_and_history_and_rejects_new_assignment(
    client, phase_context, db
):
    _, project = phase_context
    phase = create_phase(client, project)
    payload = {
        "title": "Task",
        "estimated_minutes": 120,
        "phase_assignment": {"kind": "phase", "phase_id": phase["id"]},
    }
    task = client.post(f"/projects/{project.id}/tasks", json=payload).json()
    archived = client.patch(
        f"/projects/{project.id}/phases/{phase['id']}",
        json={"version": 1, "archived": True},
    )
    assert archived.status_code == 200
    assert archived.json()["task_count"] == 1
    current = client.get(f"/tasks/{task['id']}").json()
    assert (current["phase_id"], current["version"], current["estimated_minutes"]) == (
        phase["id"],
        task["version"],
        120,
    )
    assert current["phase_archived"]
    old_client = client.patch(
        f"/tasks/{task['id']}",
        json={"version": current["version"], "title": "名称だけ"},
    )
    assert old_client.status_code == 200
    assert old_client.json()["phase_id"] == phase["id"]
    assert client.post(f"/projects/{project.id}/tasks", json=payload).status_code == 400
    stale = client.patch(
        f"/projects/{project.id}/phases/{phase['id']}",
        json={"version": 1, "name": "stale"},
    )
    assert stale.status_code == 409
    assert stale.json()["current"]["archived_at"]
    assert client.get(f"/projects/{project.id}/phases").json()["total"] == 0
    renamed = client.patch(
        f"/projects/{project.id}/phases/{phase['id']}",
        json={"version": 2, "name": "基本設計", "archived": False},
    )
    assert renamed.status_code == 200
    events = db.query(ProjectPhaseEvent).order_by(ProjectPhaseEvent.id).all()
    assert events[0].new_value["name"] == "設計"
    assert events[-1].old_value["name"] == "設計"
    assert events[-1].new_value["name"] == "基本設計"
    assert (
        client.post(
            f"/projects/{project.id}/phases", json={"name": " 基本設計 "}
        ).status_code
        == 409
    )
    cleared = client.patch(
        f"/tasks/{task['id']}",
        json={"version": old_client.json()["version"], "phase_assignment": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["phase_scope"] == "unclassified"
    assert cleared.json()["phase_id"] is None
    snapshot = (
        db.query(TaskChangeLog)
        .filter(TaskChangeLog.action == "updated")
        .order_by(TaskChangeLog.id.desc())
        .first()
    )
    assert snapshot.old_value["snapshot"]["phase_assignment"]["name"] == "基本設計"
    client.patch(
        f"/projects/{project.id}/phases/{phase['id']}",
        json={"version": 3, "name": "再改名"},
    )
    db.refresh(snapshot)
    assert snapshot.old_value["snapshot"]["phase_assignment"]["name"] == "基本設計"


def test_assignment_validation_is_atomic_and_parent_independent(
    client,
    phase_context,
    create_test_project,
    create_test_user,
    assign_project_role,
    db,
):
    user, project = phase_context
    phase = create_phase(client, project)
    other = create_test_project(project_code="PHASE-OTHER")
    assign_project_role(user=user, project=other, role_key="project_admin")
    other_phase = create_phase(client, other)
    parent = client.post(
        f"/projects/{project.id}/tasks",
        json={
            "title": "親",
            "phase_assignment": {"kind": "phase", "phase_id": phase["id"]},
        },
    ).json()
    child = client.post(
        f"/projects/{project.id}/tasks",
        json={"title": "子", "parent_task_id": parent["id"]},
    ).json()
    assert child["phase_scope"] == "unclassified"
    for assignment, expected in [
        ({"kind": "shared", "phase_id": phase["id"]}, 422),
        ({"kind": "phase", "phase_id": other_phase["id"]}, 404),
    ]:
        response = client.patch(
            f"/tasks/{parent['id']}",
            json={"version": 1, "title": "保存禁止", "phase_assignment": assignment},
        )
        assert response.status_code == expected
    assert client.get(f"/tasks/{parent['id']}").json()["title"] == "親"
    moved = client.patch(
        f"/tasks/{parent['id']}",
        json={"version": 1, "phase_assignment": {"kind": "shared"}},
    )
    assert moved.status_code == 200
    assert client.get(f"/tasks/{child['id']}").json()["phase_scope"] == "unclassified"
    viewer = create_test_user()
    authorize_as(client, viewer)
    assert client.get(f"/projects/{project.id}/tasks/effort-summary").status_code == 403


def test_effort_counts_ids_once_and_keeps_missing_and_metric_bases_separate(
    client, phase_context, create_test_requirement, create_test_requirement_document, db
):
    _, project = phase_context
    phase = create_phase(client, project)
    parent = client.post(
        f"/projects/{project.id}/tasks",
        json={
            "title": "総量枠",
            "estimated_minutes": 300,
            "estimated_basis": "rollup",
            "actual_minutes": 30,
            "actual_basis": "own",
        },
    ).json()
    children = []
    for minutes in (150, 150):
        child = client.post(
            f"/projects/{project.id}/tasks",
            json={
                "title": "同額の別Task",
                "parent_task_id": parent["id"],
                "estimated_minutes": minutes,
                "actual_minutes": 0,
                "phase_assignment": {"kind": "phase", "phase_id": phase["id"]},
            },
        )
        assert child.status_code == 201, child.text
        children.append(child.json())
    document = create_test_requirement_document(project=project)
    req = create_test_requirement(document=document)
    for relation in ("implements", "tests"):
        db.add(
            RequirementTaskRelation(
                requirement_id=req.id, task_id=children[0]["id"], relation_type=relation
            )
        )
    db.commit()
    summary = client.get(f"/projects/{project.id}/tasks/effort-summary").json()
    assert summary["totals"]["estimated"] == {
        "minutes": 300,
        "missing_count": 0,
        "unreviewed_count": 0,
        "complete": True,
    }
    assert summary["totals"]["actual"]["minutes"] == 30
    assert sum(g["totals"]["estimated"]["minutes"] for g in summary["groups"]) == 300
    filtered = client.get(
        f"/projects/{project.id}/tasks/effort-summary",
        params={"requirement_id": req.id},
    ).json()
    assert filtered["totals"]["task_count"] == 1
    assert filtered["totals"]["estimated"]["minutes"] == 150
    assert (
        client.get(
            f"/projects/{project.id}/tasks/effort-summary",
            params={"requirement_id": 999999},
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/projects/{project.id}/tasks/effort-summary",
            params={"parent_task_id": 999999},
        ).status_code
        == 404
    )
    subtree = client.get(f"/tasks/{parent['id']}").json()["effort_totals"]
    assert subtree["task_count"] == 3
    assert subtree["estimated"]["minutes"] == 300
    assert subtree["actual"]["minutes"] == 30
    unknown = client.post(
        f"/projects/{project.id}/tasks",
        json={
            "title": "旧値未確認",
            "estimated_minutes": 999,
            "estimated_basis": "unreviewed",
        },
    ).json()
    current = client.get(f"/projects/{project.id}/tasks/effort-summary").json()[
        "totals"
    ]
    assert current["estimated"]["minutes"] == 300
    assert current["estimated"]["unreviewed_count"] == 1
    assert not current["estimated"]["complete"]
    assert current["actual"]["missing_count"] == 1
    edited = client.patch(
        f"/tasks/{unknown['id']}", json={"version": 1, "estimated_minutes": 888}
    )
    assert edited.json()["estimated_basis"] == "unreviewed"
    assert (
        client.patch(
            f"/tasks/{unknown['id']}", json={"version": 2, "estimated_basis": None}
        ).status_code
        == 422
    )
    before = client.get(f"/projects/{project.id}/tasks/effort-summary").json()["totals"]
    client.patch(
        f"/projects/{project.id}/phases/{phase['id']}",
        json={"version": 1, "archived": True},
    )
    after = client.get(f"/projects/{project.id}/tasks/effort-summary").json()
    assert after["totals"] == before
    assert any(g["phase"] and g["phase"]["archived_at"] for g in after["groups"])
    db.get(Task, children[1]["id"]).deleted_at = datetime.now(UTC)
    db.commit()
    assert (
        client.get(f"/projects/{project.id}/tasks/effort-summary").json()["totals"][
            "estimated"
        ]["minutes"]
        == 150
    )
