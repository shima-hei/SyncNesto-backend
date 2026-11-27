"""本文とコメントの版を照合し、引用だけを更新する操作を検証する。"""

from sqlalchemy import select

from app.models.requirement import RequirementChangeLog
from tests.helpers.auth import authorize_as
from tests.routers.test_artifact_releases import context as context  # noqa: F401


def quote(ctx, source, quote, start):
    """表示側が数えたUnicode code pointの試験データ。"""
    section = ctx["section"]
    return dict(
        anchor_format="rendered-text-v1",
        scope="document_preview",
        source_view="requirement_document_overview_tab",
        preview_target_type="section",
        preview_target_id=section.id,
        document_id=ctx["doc"].id,
        document_version=ctx["doc"].version,
        section_id=section.id,
        section_version=section.version,
        field="section",
        label=section.title,
        quote=quote,
        source_value=source,
        start_offset=start,
        end_offset=start + len(quote),
        offset_unit="unicode_code_point",
        prefix=source[max(0, start - 48) : start],
        suffix=source[start + len(quote) :][:48],
    )


def test_reanchor_keeps_replies_and_checks_both_versions(
    client,
    db,
    context,
    create_test_user,
    assign_project_role,
):
    """引用元とコメントの競合、投稿者権限、返信保持および監査を確認する。"""
    ctx = context
    project = ctx["project"]
    member = create_test_user(email="another-author@example.com")
    assign_project_role(user=member, project=project, role_key="member")
    base = f"/projects/{project.id}/comments"
    target = dict(target_type="document", target_id=ctx["doc"].id)
    section = ctx["section"]
    url = f"/projects/{project.id}/requirement-sections/{section.id}"
    first = "先頭😀確認用 後半確認用"
    assert (
        client.patch(
            url, json={"version": section.version, "content": first}
        ).status_code
        == 200
    )
    db.refresh(section)
    original = quote(ctx, f"{section.title} {first}", "確認用", len(section.title) + 10)
    response = client.post(
        base, json={**target, "body": "指摘", "target_anchor": original}
    )
    assert response.status_code == 201, response.text
    comment = response.json()
    assert comment["target_anchor"] == original
    reply = client.post(
        base, json={**target, "body": "返信", "parent_comment_id": comment["id"]}
    )
    assert reply.status_code == 201, reply.text
    resolve = client.post(
        f"{base}/{comment['id']}/resolve", json={"version": comment["version"]}
    )
    assert resolve.status_code == 200, resolve.text
    version = resolve.json()["version"]
    uri = f"{base}/{comment['id']}/anchor"
    assert (
        client.patch(
            url, json={"version": section.version, "content": "新しい本文"}
        ).status_code
        == 200
    )
    assert (
        client.patch(uri, json={"version": version, "anchor": original}).status_code
        == 409
    )
    db.refresh(section)
    current = quote(
        ctx, f"{section.title} 新しい本文", "新しい本文", len(section.title) + 1
    )
    assert (
        client.patch(
            uri, json={"version": comment["version"], "anchor": current}
        ).status_code
        == 409
    )
    authorize_as(client, member)
    assert (
        client.patch(uri, json={"version": version, "anchor": current}).status_code
        == 403
    )
    authorize_as(client, ctx["user"])
    response = client.patch(uri, json={"version": version, "anchor": current})
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["target_anchor"] == current
    assert updated["body"] == comment["body"]
    assert updated["author_id"] == comment["author_id"]
    assert updated["is_resolved"] is True
    assert updated["created_at"] == comment["created_at"]
    assert updated["version"] == version + 1
    assert client.get(base, params=target).json()[1]["id"] == reply.json()["id"]
    entry = db.scalar(
        select(RequirementChangeLog).where(
            RequirementChangeLog.target_id == comment["id"],
            RequirementChangeLog.reason == "引用箇所を再指定",
        )
    )
    assert entry.changed_by == ctx["user"].id
    assert entry.old_value["target_anchor"] == original
    assert entry.new_value["target_anchor"] == current
