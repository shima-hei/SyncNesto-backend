"""要件追跡、対象コメント、実行証跡の振る舞いを検証する。"""

from uuid import uuid4

import pytest

from app.routers import test_collaboration as collaboration_router
from tests.helpers.auth import authorize_as


@pytest.fixture
def context(client, create_test_user, create_test_project, assign_project_role):
    """管理者と1件のテスト項目を作成する。"""
    user = create_test_user(email="collaboration@example.com")
    project = create_test_project(project_code="COLLAB", name="連携")
    assign_project_role(user=user, project=project, role_key="project_admin")
    authorize_as(client, user)
    root = f"/projects/{project.id}/test-designs"
    design = client.post(root, json={"name": "ログイン設計"}).json()
    item_id = str(uuid4())
    payload = {
        "name": design["name"],
        "description": "",
        "version": design["version"],
        "items": [
            {
                "id": item_id,
                "code": "T001",
                "content": "ログインを確認",
                "expected_result": "成功",
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
    }
    url = f"{root}/{design['id']}"
    response = client.put(url, json=payload)
    assert response.status_code == 200, response.text
    return user, project, url, item_id, payload


def test_requirement_links_survive_renumber_and_archive(
    client, context, create_test_requirement_document, create_test_requirement
):
    """表示番号変更では関連を維持し、削除項目はカバレッジから除外する。"""
    _, project, url, item_id, payload = context
    document = create_test_requirement_document(project=project)
    requirement = create_test_requirement(document=document, requirement_code="R-001")
    linked = client.post(
        url + "/requirement-links",
        json={"requirement_id": requirement.id, "item_id": item_id},
    )
    assert linked.status_code == 201, linked.text
    assert linked.json()["item_code"] == "T001"
    assert (
        len(
            client.get(
                f"/projects/{project.id}/requirements/{requirement.id}/test-items"
            ).json()
        )
        == 1
    )
    coverage = client.get(f"/projects/{project.id}/requirement-test-coverage").json()
    assert coverage[0]["has_tests"] is True
    payload["version"] = 2
    payload["items"][0]["code"] = "T002"
    assert client.put(url, json=payload).status_code == 200
    assert client.get(url + "/requirement-links").json()[0]["item_code"] == "T002"
    payload["version"] = 3
    payload["items"] = []
    assert client.put(url, json=payload).status_code == 200
    archived = client.get(
        f"/projects/{project.id}/requirements/{requirement.id}/test-items"
    ).json()
    assert archived[0]["item_deleted"] is True
    assert (
        client.get(f"/projects/{project.id}/requirement-test-coverage").json()[0][
            "has_tests"
        ]
        is False
    )


def test_comment_anchor_reply_conflict_and_deleted_target(
    client, context, create_test_user, assign_project_role
):
    """要件コメントに沿った返信、競合、投稿者制限、対象削除を確認する。"""
    author, project, url, item_id, payload = context
    comment_url = url + "/comments"
    body = {
        "target_type": "test_item",
        "target_id": item_id,
        "field": "expected_result",
        "body": "期待値を確認してください",
    }
    created = client.post(comment_url, json=body)
    assert created.status_code == 201, created.text
    comment = created.json()
    assert comment["target_status"] == "current"
    reply = client.post(
        comment_url,
        json={**body, "body": "確認します", "parent_comment_id": comment["id"]},
    )
    assert reply.status_code == 201, reply.text
    assert (
        client.patch(
            comment_url + f"/{comment['id']}", json={"version": 0, "body": "編集"}
        ).status_code
        == 422
    )
    member = create_test_user(email="other-commenter@example.com")
    assign_project_role(user=member, project=project, role_key="member")
    authorize_as(client, member)
    assert (
        client.patch(
            comment_url + f"/{comment['id']}", json={"version": 1, "body": "他人の編集"}
        ).status_code
        == 403
    )
    assert (
        client.patch(
            comment_url + f"/{comment['id']}", json={"version": 1, "is_resolved": True}
        ).status_code
        == 200
    )
    authorize_as(client, author)
    payload["version"] = 2
    payload["items"][0]["expected_result"] = "エラー表示"
    assert client.put(url, json=payload).status_code == 200
    assert client.get(comment_url).json()[0]["target_status"] == "changed"
    payload["version"] = 3
    payload["items"] = []
    assert client.put(url, json=payload).status_code == 200
    comments = client.get(comment_url).json()
    assert len(comments) == 2
    assert comments[0]["target_status"] == "missing"
    assert len(client.get(comment_url + f"/{comment['id']}/changes").json()) == 2


def test_evidence_belongs_to_one_execution(client, context, monkeypatch):
    """再実行後も古い証跡を新しい結果へ混ぜず、形式と所属を検証する。"""
    _, _, url, _, _ = context
    case = client.get(url + "/cases").json()[0]
    first = client.patch(
        url + f"/cases/{case['id']}",
        json={"version": case["version"], "status": "failed", "actual_result": "NG"},
    )
    assert first.status_code == 200, first.text
    saved: list[str] = []
    monkeypatch.setattr(
        collaboration_router.evidence_service.storage,
        "upload_private_object",
        lambda **kwargs: saved.append(kwargs["key"]),
    )
    runs_url = url + f"/cases/{case['id']}/executions"
    run1 = client.get(runs_url).json()[0]
    evidence_url = runs_url + f"/{run1['id']}/evidence"
    uploaded = client.post(
        evidence_url,
        files={"file": ("failure.png", b"\x89PNG\r\n\x1a\nimage", "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text
    assert saved and len(client.get(evidence_url).json()) == 1
    second = client.patch(
        url + f"/cases/{case['id']}",
        json={
            "version": first.json()["version"],
            "status": "passed",
            "actual_result": "OK",
        },
    )
    assert second.status_code == 200, second.text
    runs = client.get(runs_url).json()
    assert [row["status"] for row in runs] == ["passed", "failed"]
    assert runs[0]["evidence_count"] == 0
    assert runs[1]["evidence_count"] == 1
    assert (
        client.post(
            runs_url + f"/{runs[0]['id']}/evidence",
            files={"file": ("wrong.pdf", b"invalid", "application/pdf")},
        ).status_code
        == 400
    )

    evidence_id = uploaded.json()["id"]
    assert client.delete(evidence_url + f"/{evidence_id}").status_code == 204
    assert client.get(evidence_url).json() == []
    assert client.get(evidence_url + f"/{evidence_id}/download").status_code == 404


def test_permissions_and_project_scope(
    client,
    context,
    create_test_user,
    create_test_project,
    create_test_requirement_document,
    create_test_requirement,
    assign_project_role,
):
    """閲覧者の書込みと他プロジェクトの関連・証跡を拒否する。"""
    author, project, url, item_id, _ = context
    other = create_test_project(project_code="OTHER", name="別プロジェクト")
    assign_project_role(user=author, project=other, role_key="project_admin")
    foreign_document = create_test_requirement_document(project=other)
    foreign_requirement = create_test_requirement(
        document=foreign_document, requirement_code="R-FOREIGN"
    )
    assert (
        client.post(
            url + "/requirement-links",
            json={"requirement_id": foreign_requirement.id, "item_id": item_id},
        ).status_code
        == 404
    )

    viewer = create_test_user(email="test-viewer@example.com")
    assign_project_role(user=viewer, project=project, role_key="viewer")
    authorize_as(client, viewer)
    assert client.get(url + "/comments").status_code == 200
    assert (
        client.post(
            url + "/comments",
            json={"target_type": "test_item", "target_id": item_id, "body": "確認"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            url + "/requirement-links",
            json={"requirement_id": foreign_requirement.id, "item_id": item_id},
        ).status_code
        == 403
    )

    authorize_as(client, author)
    case = client.get(url + "/cases").json()[0]
    result = client.patch(
        url + f"/cases/{case['id']}",
        json={"version": case["version"], "status": "passed"},
    )
    assert result.status_code == 200
    run = client.get(url + f"/cases/{case['id']}/executions").json()[0]
    foreign_url = (
        f"/projects/{other.id}/test-designs/{url.rsplit('/', 1)[1]}"
        f"/cases/{case['id']}/executions/{run['id']}/evidence"
    )
    assert client.get(foreign_url).status_code == 404


def test_matrix_comment_detects_selected_level_rename(client, context):
    """組み合わせの選択水準が同じでも水準名の変更を示す。"""
    _, _, url, _, payload = context
    factor_id, level_id, pattern_id = [str(uuid4()) for _ in range(3)]
    payload["version"] = 2
    payload["factors"] = [{"id": factor_id, "name": "ブラウザ"}]
    payload["levels"] = [{"id": level_id, "factor_id": factor_id, "name": "Chrome"}]
    payload["patterns"] = [{"id": pattern_id, "code": "P001"}]
    payload["values"] = [
        {
            "id": str(uuid4()),
            "pattern_id": pattern_id,
            "factor_id": factor_id,
            "level_id": level_id,
        }
    ]
    assert client.put(url, json=payload).status_code == 200
    comment = client.post(
        url + "/comments",
        json={
            "target_type": "combination",
            "target_id": pattern_id,
            "field": f"level:{factor_id}",
            "body": "この水準で確認してください",
        },
    )
    assert comment.status_code == 201, comment.text
    assert comment.json()["target_status"] == "current"
    payload["version"] = 3
    payload["levels"][0]["name"] = "Edge"
    assert client.put(url, json=payload).status_code == 200
    assert client.get(url + "/comments").json()[0]["target_status"] == "changed"
