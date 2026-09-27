"""不具合タスクとの関連と最新ケース集計を検証する。"""

from uuid import uuid4

import pytest

from tests.helpers.auth import authorize_as


@pytest.fixture
def context(client, create_test_user, create_test_project, assign_project_role):
    """設計書、ケース、管理者を用意する。"""
    user = create_test_user(email="test-issues@example.com")
    project = create_test_project(project_code="ISSUES", name="不具合連携")
    assign_project_role(user=user, project=project, role_key="project_admin")
    authorize_as(client, user)
    root = f"/projects/{project.id}"
    created = client.post(root + "/test-designs", json={"name": "ログイン"})
    assert created.status_code == 201, created.text
    design = created.json()
    design_url = root + f"/test-designs/{design['id']}"
    updated = client.put(
        design_url,
        json={
            "name": design["name"],
            "description": "",
            "version": design["version"],
            "items": [
                {
                    "id": str(uuid4()),
                    "code": "T001",
                    "target_feature": "ログイン画面",
                    "content": "ログインできること",
                }
            ],
            "pattern_tables": [],
            "factors": [],
            "levels": [],
            "patterns": [],
            "values": [],
            "links": [],
            "columns": [],
            "expected_values": [],
            "expected_selections": [],
            "layout": {"cells": {}, "widths": {}, "heights": {}},
        },
    )
    assert updated.status_code == 200, updated.text
    case = client.get(design_url + "/cases").json()[0]
    return root, design_url, case


def test_issue_link_is_many_to_many_and_progress_uses_latest_status(client, context):
    """NGとIssueを分け、再実行後も重複集計しない。"""
    root, design_url, case = context
    case_url = design_url + f"/cases/{case['id']}"
    failed = client.patch(
        case_url,
        json={"version": case["version"], "status": "failed", "actual_result": "失敗"},
    )
    assert failed.status_code == 200, failed.text
    progress_url = design_url + "/progress"
    before = client.get(progress_url)
    assert before.status_code == 200, before.text
    assert before.json()["failed"] == 1
    assert before.json()["failed_without_issue"] == 1
    assert before.json()["ng_denominator"] == 1
    assert (
        client.get(progress_url, params={"target_feature": "別画面"}).json()["total"]
        == 0
    )

    issue = client.post(
        root + "/tasks",
        json={"title": "ログイン時にエラー", "task_type": "bug"},
    )
    assert issue.status_code == 201, issue.text
    task = issue.json()
    run = client.get(case_url + "/executions").json()[0]
    links_url = case_url + "/issues"
    linked = client.post(
        links_url,
        json={"task_id": task["id"], "origin_execution_id": run["id"]},
    )
    assert linked.status_code == 201, linked.text
    assert client.post(links_url, json={"task_id": task["id"]}).status_code == 409
    assert client.get(links_url).json()[0]["task_code"] == task["task_code"]
    reverse = client.get(root + f"/tasks/{task['id']}/test-cases")
    assert reverse.status_code == 200, reverse.text
    assert reverse.json()[0]["item_code"] == "T001"
    summary = client.get(progress_url).json()
    assert summary["issue_count"] == 1
    assert summary["failed_without_issue"] == 0
    assert summary["issues"][0]["failed_case_count"] == 1
    assert (
        client.patch(
            f"/tasks/{task['id']}",
            json={"version": task["version"], "task_type": "test"},
        ).status_code
        == 400
    )

    passed = client.patch(
        case_url,
        json={"version": failed.json()["version"], "status": "passed"},
    )
    assert passed.status_code == 200, passed.text
    summary = client.get(progress_url).json()
    assert (summary["passed"], summary["failed"]) == (1, 0)
    assert summary["ng_denominator"] == 1
    assert summary["ng_numerator"] == 0
    assert len(client.get(case_url + "/executions").json()) == 2

    assert client.delete(links_url + f"/{linked.json()['id']}").status_code == 204
    assert client.get(links_url).json() == []
    assert client.get(f"/tasks/{task['id']}").status_code == 200


def test_issue_rejects_non_bug_and_wrong_execution(client, context):
    """通常タスクと別ケースの実行履歴を関連付けない。"""
    root, design_url, case = context
    links_url = design_url + f"/cases/{case['id']}/issues"
    ordinary = client.post(root + "/tasks", json={"title": "作業", "task_type": "test"})
    assert ordinary.status_code == 201, ordinary.text
    assert (
        client.post(links_url, json={"task_id": ordinary.json()["id"]}).status_code
        == 404
    )
    issue = client.post(root + "/tasks", json={"title": "不具合", "task_type": "bug"})
    assert issue.status_code == 201, issue.text
    assert (
        client.post(
            links_url,
            json={"task_id": issue.json()["id"], "origin_execution_id": str(uuid4())},
        ).status_code
        == 400
    )


def test_in_progress_and_not_applicable_have_separate_progress(client, context):
    """実施中は進捗率の分子に含めず、対象外は分母から除く。"""
    _, design_url, case = context
    case_url = design_url + f"/cases/{case['id']}"
    running = client.patch(
        case_url, json={"version": case["version"], "status": "in_progress"}
    )
    assert running.status_code == 200, running.text
    progress = client.get(design_url + "/progress").json()
    assert progress["in_progress"] == 1
    assert (progress["progress_numerator"], progress["progress_denominator"]) == (0, 1)
    excluded = client.patch(
        case_url,
        json={"version": running.json()["version"], "status": "not_applicable"},
    )
    assert excluded.status_code == 200, excluded.text
    progress = client.get(design_url + "/progress").json()
    assert (progress["progress_numerator"], progress["progress_denominator"]) == (0, 0)


def test_one_issue_can_cover_multiple_cases_and_one_case_multiple_issues(
    client, context
):
    """同じ不具合の再利用と一ケースの複数原因を許可する。"""
    root, design_url, first_case = context
    first_task = client.post(
        root + "/tasks", json={"title": "共通原因", "task_type": "bug"}
    ).json()
    second_task = client.post(
        root + "/tasks", json={"title": "別原因", "task_type": "bug"}
    ).json()
    first_links = design_url + f"/cases/{first_case['id']}/issues"
    assert (
        client.post(first_links, json={"task_id": first_task["id"]}).status_code
        == 201
    )
    assert (
        client.post(first_links, json={"task_id": second_task["id"]}).status_code
        == 201
    )

    second_design = client.post(root + "/test-designs", json={"name": "権限"}).json()
    second_url = root + f"/test-designs/{second_design['id']}"
    saved = client.put(
        second_url,
        json={
            "name": "権限",
            "description": "",
            "version": second_design["version"],
            "items": [{"id": str(uuid4()), "code": "T001", "content": "権限を確認"}],
            "pattern_tables": [],
            "factors": [],
            "levels": [],
            "patterns": [],
            "values": [],
            "links": [],
            "columns": [],
            "expected_values": [],
            "expected_selections": [],
            "layout": {"cells": {}, "widths": {}, "heights": {}},
        },
    )
    assert saved.status_code == 200, saved.text
    second_case = client.get(second_url + "/cases").json()[0]
    second_links = second_url + f"/cases/{second_case['id']}/issues"
    assert (
        client.post(second_links, json={"task_id": first_task["id"]}).status_code
        == 201
    )
    reverse = client.get(root + f"/tasks/{first_task['id']}/test-cases").json()
    assert {row["case_id"] for row in reverse} == {
        first_case["id"],
        second_case["id"],
    }
    assert client.get(design_url + "/progress").json()["issue_count"] == 2
    assert client.get(second_url + "/progress").json()["issue_count"] == 1
