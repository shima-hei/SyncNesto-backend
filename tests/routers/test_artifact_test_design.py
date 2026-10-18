"""テスト設計graphの固定と既存権限・ケース・添付との整合を確認する。"""

import hashlib
import json
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, update

from app.models.artifact_release import ArtifactRelease, ArtifactReleaseNode
from app.models.rbac import Permission, Role, RolePermission
from app.models.test_design import TestDesign as DesignModel
from app.models.test_design import TestItem
from tests.routers.test_artifact_releases import all_nodes
from tests.routers.test_test_designs import design_context, graph  # noqa: F401


def freeze(client, base, source):
    """previewの対象hashを使い作成する。"""
    data: dict[str, Any] = {
        "source": {
            "kind": "test_design",
            "id": source["id"],
            "version": source["version"],
        }
    }
    preview = client.post(base + "/preview", json=data)
    assert preview.status_code == 200, preview.text
    data.update(
        request_id=str(uuid4()), expected_scope_digest=preview.json()["scope_digest"]
    )
    saved = client.post(base, json=data)
    assert saved.status_code == 201, saved.text
    return saved.json(), data


def test_graph_fixed_after_edit_delete_and_replay(client, db, request):
    """旧形式の項目対応・UUIDと書式を保全し、元編集・削除後も読める。"""
    _, url, design, _, project = request.getfixturevalue("design_context")
    data = graph(design)
    data["patterns"][1]["enabled"] = False
    data["layout"]["cells"] = {"items:x:content": {"bold": True}}
    updated = client.put(url, json=data)
    assert updated.status_code == 200, updated.text
    original = updated.json()
    base = f"/projects/{project.id}/artifact-releases"
    row, request = freeze(client, base, original)
    nodes = all_nodes(client, {"base": base}, row["id"], size=1)
    raw = "".join(n["payload"]["text"] for n in nodes[1:])
    assert json.loads(raw) == original
    assert (
        hashlib.sha256(raw.encode()).hexdigest() == nodes[0]["payload"]["graph_digest"]
    )
    assert client.post(base, json=request).json()["id"] == row["id"]
    data.update(version=original["version"], name="更新後")
    assert client.put(url, json=data).status_code == 200
    assert (
        client.post(base, json={**request, "request_id": str(uuid4())}).status_code
        == 409
    )
    assert (
        client.delete(url, params={"version": original["version"] + 1}).status_code
        == 204
    )
    meta = client.get(base + "/" + row["id"]).json()
    assert meta["source_state"] == "deleted"
    assert all_nodes(client, {"base": base}, row["id"]) == nodes
    db.execute(delete(DesignModel).where(DesignModel.id == original["id"]))
    db.commit()
    assert all_nodes(client, {"base": base}, row["id"]) == nodes


def test_test_plan_read_revocation_filters_count_and_nodes(client, db, request):
    """現在の権限失効を一覧COUNTと本文・添付の全取得へ反映する。"""
    _, _, design, _, project = request.getfixturevalue("design_context")
    base = f"/projects/{project.id}/artifact-releases"
    row, _ = freeze(client, base, design)
    role = db.scalar(
        select(Role.id).where(Role.key == "project_admin", Role.scope == "project")
    )
    permission = db.scalar(
        select(Permission.id).where(Permission.code == "test_plan:read")
    )
    db.execute(
        delete(RolePermission).where(
            RolePermission.role_id == role, RolePermission.permission_id == permission
        )
    )
    db.commit()
    assert client.get(base).json()["total"] == 0
    for suffix in ("", "/nodes", "/attachment-preservations"):
        assert client.get(f"{base}/{row['id']}{suffix}").status_code == 403


def test_graph_repeatable_read_and_atomic_failure(client, db, request, monkeypatch):
    """並行する子更新を混ぜず、監査失敗時には全partをrollbackする。"""
    from app.db.session import session_local
    from app.services.audit_log import AuditLogService
    from app.services.test_design import TestDesignService

    _, url, design, _, project = request.getfixturevalue("design_context")
    original = client.put(url, json=graph(design)).json()
    base = f"/projects/{project.id}/artifact-releases"
    read = TestDesignService.read

    def concurrent(self, work, row):
        with session_local() as other:
            other.execute(
                update(TestItem)
                .where(TestItem.id == original["items"][0]["id"])
                .values(content="後から変更")
            )
            other.commit()
        return read(self, work, row)

    with monkeypatch.context() as patch:
        patch.setattr(TestDesignService, "read", concurrent)
        preview = client.post(
            base + "/preview",
            json={"source": {"kind": "test_design", "id": design["id"], "version": 2}},
        )
        assert preview.status_code == 200
    # previewのRR snapshotは子更新前。保存時のhash差分を拒否する。
    assert (
        client.post(
            base,
            json={
                "source": {"kind": "test_design", "id": design["id"], "version": 2},
                "request_id": str(uuid4()),
                "expected_scope_digest": preview.json()["scope_digest"],
            },
        ).status_code
        == 409
    )
    p = client.post(
        base + "/preview",
        json={"source": {"kind": "test_design", "id": design["id"], "version": 2}},
    ).json()

    def fail(*args, **kwargs):
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(AuditLogService, "record", fail)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        client.post(
            base,
            json={
                "source": p["source"],
                "request_id": str(uuid4()),
                "expected_scope_digest": p["scope_digest"],
            },
        )
    assert db.scalar(select(func.count()).select_from(ArtifactRelease)) == 0
    assert db.scalar(select(func.count()).select_from(ArtifactReleaseNode)) == 0


def test_graph_total_capacity_and_node_limits(client, db, request, monkeypatch):
    """保存サイズの境界を越えた場合にheaderもpartも残さない。"""
    import app.services.artifact_release_snapshot as snapshots

    _, _, design, _, project = request.getfixturevalue("design_context")
    base = f"/projects/{project.id}/artifact-releases"
    source = {"source": {"kind": "test_design", "id": design["id"], "version": 1}}
    preview = client.post(base + "/preview", json=source).json()
    monkeypatch.setattr(snapshots, "MAX_SNAPSHOT_BYTES", preview["byte_size"])
    assert client.post(base + "/preview", json=source).status_code == 200
    monkeypatch.setattr(snapshots, "MAX_SNAPSHOT_BYTES", preview["byte_size"] - 1)
    assert client.post(base + "/preview", json=source).status_code == 413
    monkeypatch.setattr(snapshots, "MAX_SNAPSHOT_BYTES", 32 * 1024 * 1024)
    monkeypatch.setattr(snapshots, "MAX_SNAPSHOT_NODES", 1)
    assert client.post(base + "/preview", json=source).status_code == 413
    assert db.scalar(select(func.count()).select_from(ArtifactRelease)) == 0
