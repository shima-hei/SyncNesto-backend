"""要件追跡、対象コメント、実行証跡の振る舞いを検証する。"""

from uuid import uuid4

import pytest

from app.core.config import settings
from app.routers import test_collaboration as collaboration_router
from app.services.storage import StorageService
from tests.fakes.storage import MemoryS3Client
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


@pytest.fixture
def direct_evidence(client, context, monkeypatch):
    """実行履歴と直接送信用ストレージを用意する。"""
    user, _, url, _, _ = context
    monkeypatch.setattr(settings, "file_upload_mode", "presigned")
    s3 = MemoryS3Client()
    monkeypatch.setattr(
        collaboration_router.evidence_service, "storage", StorageService(s3)
    )
    case = client.get(url + "/cases").json()[0]
    result = client.patch(
        url + f"/cases/{case['id']}",
        json={
            "version": case["version"],
            "status": "failed",
            "actual_result": "NG",
        },
    )
    assert result.status_code == 200, result.text
    runs_url = url + f"/cases/{case['id']}/executions"
    execution = client.get(runs_url).json()[0]
    return result.json(), runs_url + f"/{execution['id']}/evidence", s3


def evidence_plan(
    client, url, content=b"test", content_type="text/plain", filename="a.txt"
):
    """送信計画を取得する。"""
    return client.post(
        url + "/upload-plan",
        json={
            "filename": filename,
            "content_type": content_type,
            "byte_size": len(content),
        },
    )


def test_direct_evidence_validates_and_promotes_file(client, direct_evidence):
    """Vercelの上限より大きいファイルをJSONだけで登録する。"""
    _, url, s3 = direct_evidence
    content = b"x" * (5 * 1024 * 1024)
    plan = evidence_plan(client, url, content).json()
    key = s3.presigned_params["Key"]
    s3.objects[key] = (content, "text/plain")
    completed = client.post(
        url + "/upload-complete", json={"upload_token": plan["upload_token"]}
    )
    assert completed.status_code == 201, completed.text
    assert completed.json()["byte_size"] == len(content)
    assert key not in s3.objects
    assert len(s3.objects) == 1
    stored_key = next(iter(s3.objects))
    assert stored_key.startswith("projects/")
    repeated = client.post(
        url + "/upload-complete", json={"upload_token": plan["upload_token"]}
    )
    assert repeated.json()["id"] == completed.json()["id"]
    assert len(client.get(url).json()) == 1
    s3.objects[key] = (b"changed", "text/plain")
    assert s3.objects[stored_key][0] == content
    client.delete(url + "/" + completed.json()["id"])
    assert (
        client.post(
            url + "/upload-complete", json={"upload_token": plan["upload_token"]}
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    "content,content_type",
    [
        (b"bad!", "image/png"),
        (b"wrong size", "text/plain"),
        (b"test", "application/json"),
    ],
)
def test_direct_evidence_rejects_bad_uploaded_file(
    client, direct_evidence, content, content_type
):
    """実際の容量・形式・ファイル内容を再検証する。"""
    _, url, s3 = direct_evidence
    requested_type = "image/png" if content_type == "image/png" else "text/plain"
    plan = evidence_plan(
        client,
        url,
        b"test",
        requested_type,
        "a.png" if requested_type == "image/png" else "a.txt",
    ).json()
    s3.objects[s3.presigned_params["Key"]] = (content, content_type)
    response = client.post(
        url + "/upload-complete", json={"upload_token": plan["upload_token"]}
    )
    assert response.status_code == 400, response.text
    assert client.get(url).json() == []
    assert s3.objects == {}


def test_direct_evidence_rechecks_permission(
    client, direct_evidence, create_test_user, context
):
    """計画取得後でも権限のない利用者は完了できない。"""
    _, url, s3 = direct_evidence
    plan = evidence_plan(client, url).json()
    s3.objects[s3.presigned_params["Key"]] = (b"test", "text/plain")
    stranger = create_test_user(email="stranger@example.com")
    authorize_as(client, stranger)
    assert evidence_plan(client, url).status_code == 403
    assert (
        client.post(
            url + "/upload-complete", json={"upload_token": plan["upload_token"]}
        ).status_code
        == 403
    )


def test_evidence_server_plan_and_missing_direct_object(
    client, direct_evidence, monkeypatch
):
    """未送信の完了を拒否し、通常方式を案内できる。"""
    _, url, _ = direct_evidence
    plan = evidence_plan(client, url).json()
    assert (
        client.post(
            url + "/upload-complete", json={"upload_token": plan["upload_token"]}
        ).status_code
        == 400
    )
    monkeypatch.setattr(settings, "file_upload_mode", "server")
    assert evidence_plan(client, url).json()["mode"] == "server"
    assert (
        client.post(
            url + "/upload-complete", json={"upload_token": plan["upload_token"]}
        ).status_code
        == 400
    )


def test_direct_evidence_token_is_execution_scoped(client, direct_evidence):
    """別の実行への完了登録を拒否する。"""
    case, url, s3 = direct_evidence
    plan = evidence_plan(client, url).json()
    s3.objects[s3.presigned_params["Key"]] = (b"test", "text/plain")
    case_url = url.split("/executions/")[0]
    response = client.patch(
        case_url, json={"version": case["version"], "status": "passed"}
    )
    assert response.status_code == 200, response.text
    execution = client.get(case_url + "/executions").json()[0]
    other_url = case_url + f"/executions/{execution['id']}/evidence"
    assert (
        client.post(
            other_url + "/upload-complete", json={"upload_token": plan["upload_token"]}
        ).status_code
        == 400
    )


def test_direct_evidence_enforces_file_count_at_completion(client, direct_evidence):
    """計画後に添付枠が埋まった場合も21件目を拒否する。"""
    _, url, s3 = direct_evidence
    plan = evidence_plan(client, url).json()
    pending_key = s3.presigned_params["Key"]
    s3.objects[pending_key] = (b"test", "text/plain")
    for index in range(20):
        response = client.post(
            url, files={"file": (f"{index}.txt", b"test", "text/plain")}
        )
        assert response.status_code == 201, response.text
    assert evidence_plan(client, url).status_code == 400
    assert (
        client.post(
            url + "/upload-complete", json={"upload_token": plan["upload_token"]}
        ).status_code
        == 400
    )
    assert len(client.get(url).json()) == 20


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
