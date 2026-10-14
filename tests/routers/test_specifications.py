"""設計から指摘・修正・承認までと、権限・組織境界を検証する。"""

from uuid import uuid4

import pytest

from tests.helpers.auth import authorize_as


@pytest.fixture
def context(client, create_test_user, create_test_project, assign_project_role):
    """実際のProject permissionを持つ利用者を用意する。"""
    user = create_test_user()
    project = create_test_project()
    assign_project_role(user=user, project=project, role_key="project_admin")
    authorize_as(client, user)
    return user, project, f"/projects/{project.id}/specifications"


def create(client, base, kind="api"):
    """API/画面の最小下書きを作成する。"""
    response = client.post(
        base,
        json={
            "title": "ログイン仕様",
            "summary": "登録済みユーザーがログインできる",
            "content": {"type": kind, "path" if kind == "api" else "route": "/login"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def update_data(row):
    return {key: row[key] for key in ("title", "summary", "content", "version")}


def test_review_fix_reopen_and_history(client, context):
    """指摘先の更新を通知し、確認・解決後にだけ承認できる。"""
    _, _, base = context
    row = create(client, base)
    url = f"{base}/{row['id']}"
    comment = client.post(
        url + "/comments",
        json={
            "specification_version": 1,
            "field_path": "content/path",
            "body": "リソース名を明確にしてください",
        },
    )
    assert comment.status_code == 201, comment.text
    blocked = client.patch(
        url + "/status", json={"version": 1, "status": "approved"}
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "SPECIFICATION_UNRESOLVED_COMMENTS"
    data = update_data(row)
    data["content"]["path"] = "/auth/login"
    saved = client.patch(url, json=data)
    assert saved.status_code == 200, saved.text
    assert client.patch(url, json=data).status_code == 409
    assert client.get(url + "/comments").json()[0]["anchor_changed"] is True
    c = comment.json()
    resolved = client.patch(
        f"{url}/comments/{c['id']}", json={"version": c["version"], "is_resolved": True}
    )
    assert resolved.status_code == 200, resolved.text
    approved = client.patch(url + "/status", json={"version": 2, "status": "approved"})
    assert approved.status_code == 200, approved.text
    changed = client.patch(
        url, json={**update_data(approved.json()), "summary": "修正版"}
    )
    assert changed.json()["status"] == "draft"
    history = client.get(url + "/revisions").json()
    assert [r["number"] for r in history] == [4, 3, 2, 1]
    assert "snapshot" not in history[-1]
    assert (
        client.get(url + "/revisions/1").json()["snapshot"]["content"]["path"]
        == "/login"
    )


def test_uuid_anchor_survives_reordering_and_flags_removal(client, context):
    """並べ替えで指摘を別の項目に付け替えず、削除時は変更済みと示す。"""
    _, _, base = context
    row = create(client, base, "screen")
    first, second = str(uuid4()), str(uuid4())
    data = update_data(row)
    data["content"]["fields"] = [
        {"id": first, "name": "メール"},
        {"id": second, "name": "パスワード"},
    ]
    row = client.patch(f"{base}/{row['id']}", json=data).json()
    url = f"{base}/{row['id']}"
    c = client.post(
        url + "/comments",
        json={
            "specification_version": row["version"],
            "field_path": f"content/fields/{first}/name",
            "body": "メールアドレスに統一",
        },
    )
    assert c.status_code == 201, c.text
    data = update_data(row)
    data["content"]["fields"].reverse()
    row = client.patch(url, json=data).json()
    assert client.get(url + "/comments").json()[0]["anchor_changed"] is False
    data = update_data(row)
    data["content"]["fields"] = [data["content"]["fields"][0]]
    assert client.patch(url, json=data).status_code == 200
    assert client.get(url + "/comments").json()[0]["anchor_changed"] is True
    assert (
        client.post(
            url + "/comments",
            json={
                "specification_version": 4,
                "field_path": "content/missing",
                "body": "存在しない項目",
            },
        ).status_code
        == 400
    )


@pytest.mark.parametrize(
    "role,edit,delete",
    [
        ("viewer", False, False),
        ("member", True, False),
        ("manager", True, False),
        ("project_admin", True, True),
    ],
)
def test_permissions(
    client, context, create_test_user, assign_project_role, role, edit, delete
):
    """既存の文書権限とレビュー権限を混同しない。"""
    _, project, base = context
    row = create(client, base)
    user = create_test_user(email="collaborator@example.com")
    assign_project_role(user=user, project=project, role_key=role)
    authorize_as(client, user)
    url = f"{base}/{row['id']}"
    assert client.get(url).status_code == 200
    assert client.patch(url, json=update_data(row)).status_code == (
        200 if edit else 403
    )
    assert client.delete(url, params={"version": 2 if edit else 1}).status_code == (
        204 if delete else 403
    )
    if role == "member":
        assert (
            client.patch(
                url + "/status", json={"version": 2, "status": "approved"}
            ).status_code
            == 403
        )


def test_cross_project_links_inverse_search_and_restore(
    client, context, create_test_project, create_test_task, assign_project_role
):
    """関連先のProjectを確認し、検索・ごみ箱にも同じ境界を使う。"""
    user, project, base = context
    row = create(client, base)
    url = f"{base}/{row['id']}"
    other = create_test_project()
    assign_project_role(user=user, project=other, role_key="project_admin")
    foreign = create_test_task(project=other)
    assert (
        client.get(f"/projects/{other.id}/specifications/{row['id']}").status_code
        == 404
    )
    assert (
        client.post(
            url + "/links", json={"target_type": "task", "target_id": str(foreign.id)}
        ).status_code
        == 404
    )
    task = create_test_task(project=project)
    data = {"target_type": "task", "target_id": str(task.id)}
    assert client.post(url + "/links", json=data).status_code == 201
    assert len(client.post(url + "/links", json=data).json()) == 1
    assert client.get(base + "/related", params=data).json()[0]["id"] == row["id"]
    assert (
        client.get(
            base + "/related", params={"target_type": "task", "target_id": "invalid"}
        ).status_code
        == 404
    )
    assert (
        client.get("/search", params={"q": "ログイン仕様"}).json()["items"][0]["kind"]
        == "specification"
    )
    assert client.delete(url, params={"version": 1}).status_code == 204
    trash = client.get(
        f"/projects/{project.id}/trash", params={"kind": "specification"}
    ).json()["items"][0]
    assert (
        client.post(
            f"/projects/{project.id}/trash/specification/{row['id']}/restore",
            json={"version": trash["version"], "deleted_at": trash["deleted_at"]},
        ).status_code
        == 204
    )
    assert client.get(url).status_code == 200


def test_shape_and_stale_review_validation(client, context):
    """種類の混在、重複ID、不正URL、古い版への投稿を拒否する。"""
    _, _, base = context
    same = str(uuid4())
    for content in [
        {"type": "api", "route": "/wrong"},
        {"type": "screen", "reference_url": "javascript:alert(1)"},
        {"type": "api", "request_fields": [{"id": same}, {"id": same}]},
    ]:
        assert (
            client.post(
                base, json={"title": "不正入力", "content": content}
            ).status_code
            == 422
        )
    row = create(client, base)
    assert (
        client.post(
            f"{base}/{row['id']}/comments",
            json={"specification_version": 2, "field_path": "title", "body": "古い版"},
        ).status_code
        == 409
    )
