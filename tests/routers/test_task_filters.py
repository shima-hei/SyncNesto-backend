"""複数状態・階層の検索を実関係と既存ページ契約で検証する。"""

from datetime import UTC, datetime

import pytest

from app.models.tenant import Tenant
from tests.helpers.auth import authorize_as


@pytest.fixture
def filter_context(
    client,
    db,
    create_test_user,
    create_test_project,
    create_test_task,
    assign_project_role,
):
    """検索外にも子を持つ3階層と単独Taskを用意する。"""
    actor = create_test_user()
    project = create_test_project()
    assign_project_role(user=actor, project=project, role_key="viewer")
    tasks = {
        name: create_test_task(project=project, title=name, status=status)
        for name, status in (
            ("root", "todo"),
            ("middle", "in_progress"),
            ("leaf", "done"),
            ("single", "cancelled"),
            ("legacy", "legacy_custom"),
        )
    }
    tasks["middle"].parent_task_id = tasks["root"].id
    tasks["leaf"].parent_task_id = tasks["middle"].id
    tasks["leaf"].progress_percent = 100
    db.commit()
    authorize_as(client, actor)
    return project, tasks


def test_status_sets_preserve_legacy_filters_total_and_pagination(
    client, filter_context
):
    """複数指定はOR、除外と旧statusはANDで、件数はページング前に数える。"""
    project, tasks = filter_context
    url = f"/projects/{project.id}/tasks"
    params = [("statuses", "todo"), ("statuses", "in_progress"), ("statuses", "todo")]
    response = client.get(
        url, params=[*params, ("page_size", "1"), ("sort", "code_asc")]
    )
    assert response.status_code == 200, response.text
    first = response.json()
    second = client.get(
        url, params=[*params, ("page_size", "1"), ("page", "2"), ("sort", "code_asc")]
    ).json()
    assert first["total"] == second["total"] == 2
    assert {first["items"][0]["id"], second["items"][0]["id"]} == {
        tasks["root"].id,
        tasks["middle"].id,
    }
    excluded = client.get(url, params={"exclude_statuses": "done"}).json()
    assert {t["id"] for t in excluded["items"]} == {
        t.id for name, t in tasks.items() if name != "leaf"
    }
    active = client.get(
        url, params=[("exclude_statuses", "done"), ("exclude_statuses", "cancelled")]
    ).json()
    assert {t["id"] for t in active["items"]} == {
        tasks[name].id for name in ("root", "middle", "legacy")
    }
    legacy = client.get(url, params={"status": "todo"}).json()
    assert [t["id"] for t in legacy["items"]] == [tasks["root"].id]
    combined = client.get(
        url, params=[*params, ("status", "todo"), ("q", "middle")]
    ).json()
    assert combined["total"] == 0
    assert client.get(url, params={"hierarchy": "unknown"}).status_code == 422
    assert client.get(url, params=[("statuses", "todo")] * 51).status_code == 422


def test_hierarchy_uses_real_children_outside_filters_and_pages(
    client, filter_context, monkeypatch
):
    """中間の親・末端・単独を区別し、状態や検索から外れた子も数える。"""
    from app.routers.tasks_shared import task_presentation_data_service

    project, tasks = filter_context
    url = f"/projects/{project.id}/tasks"
    expected = {
        "parent": {"root", "middle"},
        "root_parent": {"root"},
        "middle_parent": {"middle"},
        "leaf": {"leaf"},
        "standalone": {"single", "legacy"},
    }
    for hierarchy, names in expected.items():
        response = client.get(url, params={"hierarchy": hierarchy})
        assert response.status_code == 200, response.text
        assert {t["id"] for t in response.json()["items"]} == {
            tasks[n].id for n in names
        }
    repository = task_presentation_data_service.task_repository
    original = repository.list_ids_with_children
    calls = []

    def batch(db, task_ids):
        calls.append(task_ids)
        return original(db, task_ids)

    monkeypatch.setattr(repository, "list_ids_with_children", batch)
    filtered = client.get(
        url,
        params={
            "hierarchy": "parent",
            "status": "in_progress",
            "q": "middle",
            "page_size": 1,
        },
    ).json()
    assert filtered["total"] == 1
    assert calls == [[tasks["middle"].id]]
    assert filtered["items"][0]["has_children"] is True
    assert filtered["items"][0]["parent_summary"]["title"] == "root"
    assert client.get(f"/tasks/{tasks['leaf'].id}").json()["has_children"] is False
    children = client.get(
        url, params={"parent_task_id": tasks["root"].id, "hierarchy": "parent"}
    ).json()
    assert [t["id"] for t in children["items"]] == [tasks["middle"].id]
    roots = client.get(url, params={"root_only": True, "hierarchy": "parent"}).json()
    assert [t["id"] for t in roots["items"]] == [tasks["root"].id]


@pytest.mark.parametrize("hidden", ["deleted", "other_project", "other_tenant"])
def test_unavailable_children_do_not_classify_a_task_as_parent(
    client, db, filter_context, create_test_project, create_test_task, hidden
):
    """旧データに不正な所属が残っても親判定から別境界の子を漏らさない。"""
    project, tasks = filter_context
    child_project = project
    if hidden != "deleted":
        child_project = create_test_project()
        if hidden == "other_tenant":
            tenant = Tenant(name="Other", slug="filter-other")
            db.add(tenant)
            db.flush()
            child_project.tenant_id = tenant.id
    child = create_test_task(project=child_project)
    child.parent_task_id = tasks["single"].id
    if hidden == "deleted":
        child.deleted_at = datetime.now(UTC)
    db.commit()
    url = f"/projects/{project.id}/tasks"
    response = client.get(url, params={"hierarchy": "standalone", "q": "single"})
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["has_children"] is False
    assert (
        client.get(url, params={"hierarchy": "parent", "q": "single"}).json()["total"]
        == 0
    )


def test_new_filters_keep_project_read_authorization(
    client, filter_context, create_test_user
):
    """検索用の新引数を付けても未参加者の閲覧権限を拡張しない。"""
    project, _ = filter_context
    outsider = create_test_user()
    authorize_as(client, outsider)
    response = client.get(
        f"/projects/{project.id}/tasks?hierarchy=parent&exclude_statuses=done"
    )
    assert response.status_code == 403
