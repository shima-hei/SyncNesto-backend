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


def test_estimate_baseline_keeps_each_confirmation_and_enforces_authority(
    client, phase_context, create_test_user, assign_project_role
):
    admin, project = phase_context
    task = client.post(
        f"/projects/{project.id}/tasks",
        json={"title": "基準見積", "estimated_minutes": 0, "start_date": "2026-11-17"},
    ).json()
    estimate_url = f"/tasks/{task['id']}/estimate"
    url = f"{estimate_url}/baselines"
    request = {"estimate_version": task["version"], "expected_baseline_id": None}
    member = create_test_user()
    assign_project_role(user=member, project=project, role_key="member")
    authorize_as(client, member)
    assert client.get(url).json()["current"] is None
    assert client.post(url, json=request).status_code == 403
    authorize_as(client, admin)
    response = client.post(url, json={**request, "reason": "初回確定"})
    assert response.status_code == 200, response.text
    baseline = response.json()
    assert baseline["estimate"]["minutes"] == 0
    assert baseline["confirmed_by"] == admin.id
    assert client.post(url, json=request).json()["id"] == baseline["id"]
    assert client.get(url).json()["total"] == 1
    assert client.get(estimate_url).json()["version"] == task["version"]

    saved = client.patch(
        estimate_url,
        json={
            "version": task["version"],
            "method": "simple",
            "minutes": 120,
        },
    )
    assert saved.status_code == 200, saved.text
    assert client.get(url).json()["current"]["estimate"]["minutes"] == 0
    assert client.post(url, json=request).status_code == 409
    current_estimate_version = saved.json()["current"]["task_version"]
    assert (
        client.post(
            url,
            json={
                "estimate_version": current_estimate_version,
                "expected_baseline_id": None,
            },
        ).status_code
        == 409
    )
    second = client.post(
        url,
        json={
            "estimate_version": current_estimate_version,
            "expected_baseline_id": baseline["id"],
            "reason": "見直し",
        },
    )
    assert second.status_code == 200, second.text
    result = client.get(url).json()
    assert [row["estimate"]["minutes"] for row in result["items"]] == [120, 0]
    assert [row["reason"] for row in result["items"]] == ["見直し", "初回確定"]
    assert client.get(f"/tasks/{task['id']}").json()["start_date"] == "2026-11-17"


def test_estimate_workbench_round_trips_and_tracks_legacy_edits(client, phase_context):
    _, project = phase_context
    root = f"/projects/{project.id}"
    task = client.post(
        f"{root}/tasks",
        json={
            "title": "工数見積",
            "estimated_minutes": 60,
            "actual_minutes": 15,
            "start_date": "2026-11-17",
            "estimated_basis": "own",
        },
    ).json()
    url = f"/tasks/{task['id']}/estimate"
    old = client.get(url).json()
    assert old["current"]["method"] == "simple"
    assert old["current"]["minutes"] == 60
    inputs = {
        "optimistic_minutes": 480,
        "likely_minutes": 720,
        "pessimistic_minutes": 1440,
    }
    invalid = client.patch(
        url,
        json={
            "version": task["version"],
            "method": "pert",
            **inputs,
            "optimistic_minutes": 800,
        },
    )
    assert invalid.status_code == 422
    result = client.patch(
        url,
        json={
            "version": task["version"],
            "method": "pert",
            **inputs,
            "assumptions": "API仕様確定後",
        },
    )
    assert result.status_code == 200, result.text
    estimate = result.json()
    assert estimate["history_total"] == 2
    assert estimate["current"]["minutes"] == 800
    assert estimate["current"]["assumptions"] == "API仕様確定後"
    assert (
        client.patch(
            url, json={"version": task["version"], "method": "triangular", **inputs}
        ).status_code
        == 409
    )
    triangle = client.patch(
        url, json={"version": estimate["version"], "method": "triangular", **inputs}
    )
    assert triangle.status_code == 200 and triangle.json()["current"]["minutes"] == 880
    methods = client.get(
        f"{root}/task-estimate-methods", params={"task_ids": task["id"]}
    )
    assert methods.json() == [{"task_id": task["id"], "method": "triangular"}]
    legacy = client.patch(
        f"/tasks/{task['id']}/planning",
        json={"version": triangle.json()["version"], "estimated_minutes": 0},
    )
    assert legacy.status_code == 200
    assert legacy.json()["estimated_minutes"] == 0
    assert legacy.json()["actual_minutes"] == 15
    assert legacy.json()["start_date"] == "2026-11-17"
    latest = client.get(url).json()
    assert latest["current"]["method"] == "simple"
    assert latest["current"]["minutes"] == 0
    assert [row["method"] for row in latest["history"]] == [
        "simple",
        "triangular",
        "pert",
        "simple",
    ]
    assert (
        client.get(f"{root}/tasks/effort-summary").json()["totals"]["estimated"][
            "minutes"
        ]
        == 0
    )


def test_phase_details_and_scoped_task_listing(client, phase_context):
    _, project = phase_context
    root = f"/projects/{project.id}"
    phase = create_phase(client, project)
    url = f"{root}/phases/{phase['id']}"
    result = client.patch(
        url,
        json={"version": phase["version"], "color": "violet", "description": "仕様"},
    )
    assert result.status_code == 200
    assert result.json()["color"] == "violet"
    assert result.json()["description"] == "仕様"
    for kind in ("shared", "unclassified", "phase"):
        assignment = {
            "kind": kind,
            "phase_id": phase["id"] if kind == "phase" else None,
        }
        assert (
            client.post(
                f"{root}/tasks", json={"title": kind, "phase_assignment": assignment}
            ).status_code
            == 201
        )
    scoped = client.get(f"{root}/tasks", params={"phase_id": phase["id"]}).json()
    assert [row["title"] for row in scoped["items"]] == ["phase"]
    shared = client.get(f"{root}/tasks", params={"phase_scope": "shared"}).json()
    assert [row["title"] for row in shared["items"]] == ["shared"]
    assert client.get(f"{root}/tasks", params={"phase_id": 999999}).status_code == 404
    cleared = client.patch(
        url, json={"version": result.json()["version"], "color": None}
    )
    assert cleared.status_code == 200
    assert cleared.json()["color"] is None
    assert cleared.json()["description"] == "仕様"


def test_phase_transfers_preserve_task_details_and_require_fresh_preview(
    client, phase_context, create_test_user, assign_project_role
):
    admin, project = phase_context
    root = f"/projects/{project.id}"
    source = create_phase(client, project)
    target = create_phase(client, project, "実装")
    source_url = f"{root}/phases/{source['id']}"
    destination = {"kind": "phase", "phase_id": target["id"]}

    def task_in_source(title):
        result = client.post(
            f"{root}/tasks",
            json={
                "title": title,
                "estimated_minutes": 45,
                "start_date": "2026-11-17",
                "phase_assignment": {"kind": "phase", "phase_id": source["id"]},
            },
        )
        assert result.status_code == 201
        return result.json()

    first = task_in_source("移動対象1")
    second = task_in_source("移動対象2")
    bulk = f"{root}/phases/transfer-tasks"
    result = client.post(
        bulk,
        json={
            "tasks": [
                {"id": first["id"], "version": first["version"]},
                {"id": second["id"], "version": second["version"] + 1},
            ],
            "destination": destination,
        },
    )
    assert result.status_code == 409
    assert client.get(f"/tasks/{first['id']}").json()["phase_id"] == source["id"]

    preview = client.get(f"{source_url}/archive-preview").json()
    assert preview["task_count"] == 2
    result = client.post(
        bulk,
        json={
            "tasks": [
                {"id": first["id"], "version": first["version"]},
            ],
            "destination": destination,
        },
    )
    assert result.status_code == 200 and result.json()["moved_count"] == 1
    archive = f"{source_url}/archive-with-transfer"
    assert (
        client.post(
            archive, json={"snapshot": preview["snapshot"], "destination": destination}
        ).status_code
        == 409
    )
    fresh = client.get(f"{source_url}/archive-preview").json()
    assert fresh["task_count"] == 1
    viewer = create_test_user()
    assign_project_role(user=viewer, project=project, role_key="viewer")
    authorize_as(client, viewer)
    assert client.get(f"{source_url}/archive-preview").status_code == 403
    assert (
        client.post(
            archive, json={"snapshot": fresh["snapshot"], "destination": destination}
        ).status_code
        == 403
    )
    authorize_as(client, admin)

    result = client.post(
        archive, json={"snapshot": fresh["snapshot"], "destination": destination}
    )
    assert result.status_code == 200 and result.json()["moved_count"] == 1
    saved = client.get(f"/tasks/{second['id']}").json()
    assert saved["phase_id"] == target["id"]
    assert saved["estimated_minutes"] == 45
    assert saved["start_date"] == "2026-11-17"
    assert saved["version"] == second["version"] + 1
    archived = client.get(f"{root}/phases", params={"include_archived": True}).json()[
        "items"
    ][0]
    assert archived["id"] == source["id"] and archived["archived_at"]


def test_phase_list_keeps_the_whole_planned_period_and_empty_phases(
    client,
    phase_context,
    db,
):
    _, project = phase_context
    phase = create_phase(client, project)
    empty = create_phase(client, project, "実装")
    root = f"/projects/{project.id}"
    assignment = {"kind": "phase", "phase_id": phase["id"]}
    parent = client.post(
        f"{root}/tasks",
        json={
            "title": "親",
            "phase_assignment": assignment,
            "start_date": "2026-11-16",
        },
    )
    assert parent.status_code == 201
    child = client.post(
        f"{root}/tasks",
        json={
            "title": "子",
            "phase_assignment": assignment,
            "due_date": "2026-12-03",
            "parent_task_id": parent.json()["id"],
        },
    )
    assert child.status_code == 201
    unscheduled = client.post(
        f"{root}/tasks",
        json={
            "title": "未日程",
            "phase_assignment": assignment,
        },
    )
    assert unscheduled.status_code == 201
    outdated = client.post(
        f"{root}/tasks",
        json={
            "title": "削除済み",
            "phase_assignment": assignment,
            "start_date": "2026-11-01",
            "due_date": "2026-12-31",
        },
    )
    assert outdated.status_code == 201
    row = db.get(Task, outdated.json()["id"])
    row.deleted_at = datetime.now(UTC)
    db.commit()

    response = client.get(f"{root}/phases")
    assert response.status_code == 200
    by_id = {item["id"]: item for item in response.json()["items"]}
    assert by_id[phase["id"]]["task_count"] == 3
    assert by_id[phase["id"]]["planned_start_date"] == "2026-11-16"
    assert by_id[phase["id"]]["planned_end_date"] == "2026-12-03"
    assert by_id[empty["id"]]["planned_start_date"] is None
    assert by_id[empty["id"]]["planned_end_date"] is None


def test_gantt_order_persists_with_separate_phase_and_task_permissions(
    client,
    phase_context,
    create_test_user,
    assign_project_role,
):
    _, project = phase_context
    first_phase = create_phase(client, project, "設計")
    second_phase = create_phase(client, project, "実装")
    root = f"/projects/{project.id}"

    def create_task(title, start, phase, parent=None):
        data = {
            "title": title,
            "start_date": start,
            "parent_task_id": parent,
            "phase_assignment": {"kind": "phase", "phase_id": phase["id"]},
        }
        response = client.post(f"{root}/tasks", json=data)
        assert response.status_code == 201, response.text
        return response.json()

    first = create_task("A", "2026-11-16", first_phase)
    hidden = create_task("Hidden", "2026-11-17", first_phase)
    last = create_task("B", "2026-11-18", first_phase)
    child = create_task("Child", "2026-11-19", first_phase, first["id"])
    elsewhere = create_task("Other", "2026-11-20", second_phase)
    initial = client.get(f"{root}/gantt").json()
    assert [task["id"] for task in initial["tasks"]] == [
        first["id"],
        hidden["id"],
        last["id"],
        child["id"],
        elsewhere["id"],
    ]
    phase_order = f"{root}/gantt/phase-order"
    task_order = f"{root}/gantt/task-order"

    def move(url, item, anchor):
        return client.patch(
            url,
            json={
                "item_id": item["id"],
                "anchor_id": anchor["id"],
                "placement": "before",
            },
        )

    assert move(phase_order, second_phase, first_phase).status_code == 204
    phases = client.get(f"{root}/phases").json()["items"]
    assert [phase["id"] for phase in phases] == [second_phase["id"], first_phase["id"]]
    history = client.get(f"{root}/phases/{first_phase['id']}/events").json()["items"]
    assert history[0]["old_value"]["sort_order"] == 0
    assert history[0]["new_value"]["sort_order"] == 1

    member = create_test_user(email="gantt-member@example.com")
    assign_project_role(user=member, project=project, role_key="member")
    authorize_as(client, member)
    assert move(phase_order, first_phase, second_phase).status_code == 403
    assert move(task_order, child, first).status_code == 400
    assert move(task_order, elsewhere, first).status_code == 400
    assert move(task_order, last, first).status_code == 204
    assert move(task_order, last, first).status_code == 204

    gantt = client.get(f"{root}/gantt").json()
    siblings = [
        task["id"]
        for task in gantt["tasks"]
        if task["phase_id"] == first_phase["id"] and not task["parent_task_id"]
    ]
    assert siblings == [last["id"], first["id"], hidden["id"]]
    saved = client.get(f"/tasks/{last['id']}").json()
    assert saved["version"] == last["version"]
    assert saved["sort_order"] == last["sort_order"]
    assert saved["start_date"] == last["start_date"]

    viewer = create_test_user(email="gantt-viewer@example.com")
    assign_project_role(user=viewer, project=project, role_key="viewer")
    authorize_as(client, viewer)
    assert move(task_order, first, last).status_code == 403


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
