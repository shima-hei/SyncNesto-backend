"""Atomic worksheet edits and the independent project estimate default."""

from sqlalchemy import select

from app.models.audit_log import AuditLog
from tests.helpers.auth import authorize_as


def test_standard_and_atomic_estimates(
    client,
    db,
    create_test_user,
    create_test_project,
    assign_project_role,
    create_test_task,
):
    actor = create_test_user()
    project = create_test_project()
    assign_project_role(user=actor, project=project, role_key="project_admin")
    authorize_as(client, actor)
    first = create_test_task(project=project)
    second = create_test_task(project=project)
    path = f"/projects/{project.id}"
    first_id, second_id = first.id, second.id
    initial = [first.version, second.version]
    standard = path + "/estimate-standard"
    sheet = path + "/estimate-worksheet"
    rows = [
        {
            "task_id": first_id,
            "version": initial[0],
            "method": "pert",
            "optimistic_minutes": 0,
            "likely_minutes": 60,
            "pessimistic_minutes": 120,
        },
        {"task_id": second_id, "version": initial[1], "method": "simple", "minutes": 0},
    ]
    assert client.get(standard).json() == {"method": "simple", "version": 1}
    assert (
        client.patch(
            sheet, json={"rows": [rows[0], {**rows[1], "version": 999}]}
        ).status_code
        == 409
    )
    assert client.get(f"/tasks/{first_id}/estimate").json()["history_total"] == 0
    result = client.patch(sheet, json={"rows": rows})
    assert result.status_code == 200, result.text
    current = result.json()
    assert [row["current"]["minutes"] for row in current] == [60, 0]
    read = client.get(sheet, params=[("task_ids", first_id), ("task_ids", second_id)])
    assert read.json() == current
    assert client.patch(
        standard, json={"method": "triangular", "version": 1}
    ).json() == {"method": "triangular", "version": 2}
    assert (
        client.patch(standard, json={"method": "pert", "version": 1}).status_code == 409
    )
    assert client.get(sheet, params={"task_ids": first_id}).json() == current[:1]
    assert client.get(f"/tasks/{first_id}/estimate").json()["planned_minutes"] is None
    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.event_type == "project.estimate_standard_changed",
            AuditLog.project_id == project.id,
        )
    )
    assert audit is not None


def test_worksheet_project_boundary(
    client, create_test_user, create_test_project, assign_project_role, create_test_task
):
    project = create_test_project()
    other = create_test_project(project_code="ELSEWHERE")
    actor = create_test_user()
    assign_project_role(user=actor, project=project, role_key="member")
    authorize_as(client, actor)
    own = create_test_task(project=project)
    foreign = create_test_task(project=other)
    base = f"/projects/{project.id}"
    assert (
        client.patch(
            base + "/estimate-standard", json={"method": "pert", "version": 1}
        ).status_code
        == 403
    )
    rows = [
        {"task_id": t.id, "version": t.version, "method": "simple", "minutes": 60}
        for t in (own, foreign)
    ]
    assert (
        client.patch(base + "/estimate-worksheet", json={"rows": rows}).status_code
        == 404
    )
    assert client.get(f"/tasks/{own.id}/estimate").json()["history_total"] == 0
    assert [
        row["task_id"]
        for row in client.get(
            base + "/estimate-worksheet",
            params=[("task_ids", own.id), ("task_ids", foreign.id)],
        ).json()
    ] == [own.id]
    assert (
        client.patch(
            base + "/estimate-worksheet", json={"rows": [rows[0], rows[0]]}
        ).status_code
        == 422
    )
