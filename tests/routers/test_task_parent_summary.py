"""一覧の取得範囲に依存しない親Taskの名称参照。"""

from datetime import UTC, datetime

import pytest

from app.models.tenant import Tenant
from tests.helpers.auth import authorize_as


def test_task_list_resolves_off_page_parent_names_in_one_batch(
    client,
    db,
    create_test_user,
    create_test_project,
    create_test_task,
    assign_project_role,
    monkeypatch,
):
    """検索から外れる親を一括参照し、各子のレスポンスへ名称を付ける。"""
    from app.routers.tasks_shared import task_presentation_data_service

    actor = create_test_user()
    project = create_test_project()
    assign_project_role(user=actor, project=project, role_key="member")
    parent = create_test_task(project=project, title="全体の親課題")
    children = [
        create_test_task(project=project, title="子の確認A"),
        create_test_task(project=project, title="子の確認B"),
    ]
    for child in children:
        child.parent_task_id = parent.id
    db.commit()
    authorize_as(client, actor)
    calls = []
    repository = task_presentation_data_service.task_repository
    original = repository.list_by_ids

    def batch(db, task_ids):
        calls.append(task_ids)
        return original(db, task_ids)

    monkeypatch.setattr(repository, "list_by_ids", batch)
    response = client.get(f"/projects/{project.id}/tasks?q=子")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert {item["id"] for item in items} == {child.id for child in children}
    assert calls == [[parent.id]]
    expected = {"id": parent.id, "title": parent.title, "task_code": parent.task_code}
    assert all(item["parent_summary"] == expected for item in items)
    paged = client.get(f"/projects/{project.id}/tasks?q=子&page_size=1").json()
    assert len(paged["items"]) == 1
    assert paged["items"][0]["parent_summary"] == expected
    root = client.get(f"/tasks/{parent.id}").json()
    assert root["parent_summary"] is None


@pytest.mark.parametrize("hidden", ["deleted", "other_project", "other_tenant"])
def test_task_parent_summary_does_not_expose_unavailable_parent(
    client,
    db,
    create_test_user,
    create_test_project,
    create_test_task,
    assign_project_role,
    hidden,
):
    """旧データに参照が残っても、削除済み・別Project・別組織の名称を返さない。"""
    actor = create_test_user()
    project = create_test_project()
    assign_project_role(user=actor, project=project, role_key="member")
    child = create_test_task(project=project)
    parent_project = project
    if hidden in {"other_project", "other_tenant"}:
        parent_project = create_test_project()
        if hidden == "other_tenant":
            tenant = Tenant(name="Other", slug="parent-other")
            db.add(tenant)
            db.flush()
            parent_project.tenant_id = tenant.id
    parent = create_test_task(project=parent_project, title="非公開の親課題")
    child.parent_task_id = parent.id
    if hidden == "deleted":
        parent.deleted_at = datetime.now(UTC)
    db.commit()
    authorize_as(client, actor)

    response = client.get(f"/tasks/{child.id}")
    assert response.status_code == 200, response.text
    assert response.json()["parent_task_id"] == parent.id
    assert response.json()["parent_summary"] is None
    assert "非公開の親課題" not in response.text
