"""全コメントAPIのメンション保存、位置、所属、編集と削除を検証する。"""

import pytest
from sqlalchemy import select

from app.models.comment_mention import CommentMention
from app.models.project import ProjectMember
from app.models.rbac import Role
from tests.helpers.auth import authorize_as


def occurrence(user, start=0):
    """ブラウザと同じUTF-16単位で位置を作る。"""
    return {
        "user_id": user.id,
        "display_name": user.name,
        "start": start,
        "end": start + len(f"@{user.name}".encode("utf-16-le")) // 2,
    }


@pytest.fixture(params=["requirement", "target", "task", "design"])
def mention_context(
    request,
    client,
    db,
    create_test_user,
    create_test_project,
    assign_project_role,
    create_test_requirement_document,
    create_test_requirement,
    create_test_task,
):
    """既存4系統のコメント対象を用意する。"""
    author = create_test_user(email="author@example.com", name="投稿者")
    first = create_test_user(email="first@example.com", name="田中 太郎")
    second = create_test_user(email="second@example.com", name="佐藤 花子")
    project = create_test_project()
    for user in [author, first, second]:
        assign_project_role(user=user, project=project, role_key="project_admin")
    authorize_as(client, author)
    kind = request.param
    project_root = f"/projects/{project.id}"
    if kind in {"requirement", "target"}:
        document = create_test_requirement_document(project=project)
        requirement = create_test_requirement(document=document)
        if kind == "requirement":
            url = f"{project_root}/requirements/{requirement.id}/comments"
            extra = {}
            field = "comment"
            fk = "requirement_comment_id"
        else:
            url = f"{project_root}/comments"
            extra = {"target_type": "requirement_item", "target_id": requirement.id}
            field = "body"
            fk = "requirement_target_comment_id"
    elif kind == "task":
        task = create_test_task(project=project)
        url = f"/tasks/{task.id}/comments"
        extra = {}
        field = "body"
        fk = "task_comment_id"
    else:
        design = client.post(f"{project_root}/test-designs", json={"name": "設計"})
        assert design.status_code == 201, design.text
        url = f"{project_root}/test-designs/{design.json()['id']}/comments"
        extra = {"target_type": "design"}
        field = "body"
        fk = "test_design_comment_id"
    return {
        "kind": kind,
        "author": author,
        "first": first,
        "second": second,
        "project": project,
        "url": url,
        "extra": extra,
        "field": field,
        "fk": fk,
    }


def detail_url(ctx, comment_id):
    """タスクのみ独立したコメント更新URLを使用する。"""
    if ctx["kind"] == "task":
        return f"/task-comments/{comment_id}"
    return f"{ctx['url']}/{comment_id}"


def test_mentions_roundtrip_edit_and_delete(client, db, mention_context):
    """繰り返し出現は1関連、改名後も識別し、編集と削除で同期する。"""
    ctx = mention_context
    first, second = ctx["first"], ctx["second"]
    prefix = "😀 "
    token = f"@{first.name}"
    body = prefix + token + f" さん\n{first.name}さんの対応\n" + token
    end_start = len(body[: -len(token)].encode("utf-16-le")) // 2
    mentions = [occurrence(first, 3), occurrence(first, end_start)]
    created = client.post(
        ctx["url"], json={**ctx["extra"], ctx["field"]: body, "mentions": mentions}
    )
    assert created.status_code == 201, created.text
    comment = created.json()
    assert comment["mentions"] == mentions
    relation = db.scalars(select(CommentMention)).all()
    assert len(relation) == 1
    assert relation[0].user_id == first.id
    assert len(relation[0].occurrences) == 2
    assert getattr(relation[0], ctx["fk"]) == comment["id"]
    first.name = "変更された名前"
    db.commit()
    params = ctx["extra"] if ctx["kind"] == "target" else None
    fetched = client.get(ctx["url"], params=params).json()[0]
    assert fetched[ctx["field"]] == body
    assert fetched["mentions"] == mentions
    if ctx["kind"] != "requirement":
        # 以前の表示名を保った編集を許可する。
        changed = client.patch(
            detail_url(ctx, comment["id"]),
            json={
                "version": comment["version"],
                "body": body + "!",
                "mentions": mentions,
            },
        )
        assert changed.status_code == 200, changed.text
        comment = changed.json()
        replacement = f"@{second.name} 確認お願いします"
        changed = client.patch(
            detail_url(ctx, comment["id"]),
            json={
                "version": comment["version"],
                "body": replacement,
                "mentions": [occurrence(second)],
            },
        )
        assert changed.status_code == 200, changed.text
        comment = changed.json()
        db.expire_all()
        assert [row.user_id for row in db.scalars(select(CommentMention))] == [
            second.id
        ]
        # メンション情報を省略した本文更新では旧関連を残さない。
        changed = client.patch(
            detail_url(ctx, comment["id"]),
            json={
                "version": comment["version"],
                "body": "通常の本文",
            },
        )
        assert changed.status_code == 200, changed.text
        comment = changed.json()
        assert comment["mentions"] == []
        assert db.scalars(select(CommentMention)).all() == []
        changed = client.patch(
            detail_url(ctx, comment["id"]),
            json={
                "version": comment["version"],
                "body": replacement,
                "mentions": [occurrence(second)],
            },
        )
        assert changed.status_code == 200, changed.text
        comment = changed.json()
    deleted = client.delete(
        detail_url(ctx, comment["id"]), params={"version": comment.get("version", 1)}
    )
    assert deleted.status_code == (200 if ctx["kind"] == "design" else 204), (
        deleted.text
    )
    db.expire_all()
    assert db.scalars(select(CommentMention)).all() == []


@pytest.mark.parametrize(
    "invalid_user",
    ["outside", "missing", "inactive", "deleted_member", "no_permission"],
)
def test_reject_ineligible_user_atomically(
    client, db, mention_context, create_test_user, assign_project_role, invalid_user
):
    """直接APIへ送られたスコープ外・無効ユーザーは本文も保存しない。"""
    ctx = mention_context
    target = create_test_user(email="invalid@example.com", name="対象外")
    if invalid_user in {"inactive", "deleted_member"}:
        membership = assign_project_role(
            user=target, project=ctx["project"], role_key="member"
        )
        if invalid_user == "inactive":
            target.is_active = False
        else:
            from datetime import UTC, datetime

            membership.deleted_at = datetime.now(UTC)
        db.commit()
    elif invalid_user == "no_permission":
        role = Role(key="empty", name="権限なし", scope="project")
        db.add(role)
        db.flush()
        db.add(
            ProjectMember(
                user_id=target.id, project_id=ctx["project"].id, role_id=role.id
            )
        )
        db.commit()
    mention = occurrence(target)
    if invalid_user == "missing":
        mention["user_id"] = 999999
    response = client.post(
        ctx["url"],
        json={
            **ctx["extra"],
            ctx["field"]: f"@{target.name}",
            "mentions": [mention],
        },
    )
    assert response.status_code == 400, response.text
    assert db.scalars(select(CommentMention)).all() == []
    params = ctx["extra"] if ctx["kind"] == "target" else None
    assert client.get(ctx["url"], params=params).json() == []


@pytest.mark.parametrize(
    "invalid_span",
    [
        "overlap",
        "wrong_text",
        "split_emoji",
        "out_of_range",
        "reversed",
        "spoofed_name",
    ],
)
def test_reject_invalid_positions(client, mention_context, invalid_span):
    """重複区間、絵文字途中、表示名偽装などを拒否する。"""
    ctx = mention_context
    first = ctx["first"]
    body = f"😀 @{first.name}"
    mention = occurrence(first, 3)
    mentions = [mention]
    if invalid_span == "overlap":
        mentions = [mention, mention]
    elif invalid_span == "wrong_text":
        body = "通常のテキスト" + body
    elif invalid_span == "split_emoji":
        mention["start"] = 1
    elif invalid_span == "out_of_range":
        mention["end"] = 100000
    elif invalid_span == "reversed":
        mention["end"] = 2
    else:
        body = "@別の表示名"
        mention.update(display_name="別の表示名", start=0, end=len(body))
    response = client.post(
        ctx["url"], json={**ctx["extra"], ctx["field"]: body, "mentions": mentions}
    )
    assert response.status_code == 400, response.text


def test_candidates_use_same_scope_and_access_rules(
    client, db, create_test_user, create_test_project, assign_project_role
):
    """検索を30件以降にも適用し、無効・無所属・権限なしを候補から除く。"""
    author = create_test_user(email="a@example.com", name="投稿者")
    project = create_test_project()
    assign_project_role(user=author, project=project, role_key="project_admin")
    authorize_as(client, author)
    for index in range(32):
        user = create_test_user(
            email=f"user{index}@example.com", name=f"ユーザー{index}"
        )
        assign_project_role(user=user, project=project, role_key="viewer")
    late = create_test_user(email="tanaka@example.com", name="田中 太郎")
    assign_project_role(user=late, project=project, role_key="viewer")
    outside = create_test_user(email="tanaka-outside@example.com", name="田中 太郎")
    inactive = create_test_user(email="tanaka-inactive@example.com", name="田中 太郎")
    assign_project_role(user=inactive, project=project, role_key="viewer")
    inactive.is_active = False
    db.commit()
    url = f"/projects/{project.id}/member-users"
    response = client.get(
        url, params={"mention_permission": "task:read", "q": "tanaka", "limit": 30}
    )
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == [late.id]
    authorize_as(client, outside)
    assert (
        client.get(url, params={"mention_permission": "task:read"}).status_code == 403
    )


def test_plain_text_is_not_inferred_as_a_mention(client, db, mention_context):
    """旧本文の@表記や同名の通常テキストを自動で関連にしない。"""
    ctx = mention_context
    response = client.post(
        ctx["url"],
        json={
            **ctx["extra"],
            ctx["field"]: f"@{ctx['first'].name} {ctx['first'].name}",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["mentions"] == []
    assert db.scalars(select(CommentMention)).all() == []


def test_rejected_edit_preserves_body_and_mentions(
    client, db, mention_context, create_test_user
):
    """不正な編集を拒否して、本文と関連の両方を元のまま保つ。"""
    ctx = mention_context
    if ctx["kind"] == "requirement":
        return  # この旧APIには本文編集がない。
    mention = occurrence(ctx["first"])
    body = f"@{ctx['first'].name} 確認"
    response = client.post(
        ctx["url"],
        json={
            **ctx["extra"],
            "body": body,
            "mentions": [mention],
        },
    )
    assert response.status_code == 201, response.text
    comment = response.json()
    outside = create_test_user(email="outside-edit@example.com", name="外部")
    rejected = client.patch(
        detail_url(ctx, comment["id"]),
        json={
            "version": comment["version"],
            "body": f"@{outside.name}",
            "mentions": [occurrence(outside)],
        },
    )
    assert rejected.status_code == 400, rejected.text
    stale = client.patch(
        detail_url(ctx, comment["id"]),
        json={
            "version": comment["version"] + 1,
            "body": "競合する変更",
        },
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["current"]["mentions"] == [mention]
    params = ctx["extra"] if ctx["kind"] == "target" else None
    persisted = client.get(ctx["url"], params=params).json()[0]
    assert persisted["body"] == body
    assert persisted["mentions"] == [mention]
    assert persisted["version"] == comment["version"]
    assert db.scalars(select(CommentMention)).one().user_id == ctx["first"].id


def test_status_changes_preserve_mentions(client, mention_context):
    """状態だけの更新で本文と保存済みメンションを失わない。"""
    ctx = mention_context
    if ctx["kind"] == "requirement":
        return
    mentions = [occurrence(ctx["first"])]
    response = client.post(
        ctx["url"],
        json={
            **ctx["extra"],
            "body": f"@{ctx['first'].name}",
            "mentions": mentions,
        },
    )
    assert response.status_code == 201, response.text
    comment = response.json()
    if ctx["kind"] == "design":
        changed = client.patch(
            detail_url(ctx, comment["id"]),
            json={
                "version": comment["version"],
                "is_resolved": True,
            },
        )
    else:
        changed = client.post(
            detail_url(ctx, comment["id"]) + "/resolve",
            json={"version": comment["version"]},
        )
    assert changed.status_code == 200, changed.text
    assert changed.json()["mentions"] == mentions


def test_database_cascade_removes_mentions(client, db, mention_context):
    """ORMを介さない親削除でもDB外部キーが関連を削除する。"""
    from sqlalchemy import delete

    from app.models.requirement import RequirementComment, RequirementTargetComment
    from app.models.task import TaskComment
    from app.models.test_design import TestDesignComment, TestDesignCommentChange

    ctx = mention_context
    response = client.post(
        ctx["url"],
        json={
            **ctx["extra"],
            ctx["field"]: f"@{ctx['first'].name}",
            "mentions": [occurrence(ctx["first"])],
        },
    )
    assert response.status_code == 201, response.text
    comment_id = response.json()["id"]
    models = {
        "requirement": RequirementComment,
        "target": RequirementTargetComment,
        "task": TaskComment,
        "design": TestDesignComment,
    }
    if ctx["kind"] == "design":
        # コメント履歴の既存FKはcascadeではない。
        db.execute(
            delete(TestDesignCommentChange).where(
                TestDesignCommentChange.comment_id == comment_id
            )
        )
    model = models[ctx["kind"]]
    db.execute(delete(model).where(model.id == comment_id))
    db.commit()
    assert db.scalars(select(CommentMention)).all() == []
