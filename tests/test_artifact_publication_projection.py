"""DBに依存せず、壊れた構成・未知schema・分割上限を検証する。"""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.exceptions import ArtifactReleaseTooLargeError, BadRequestError
from app.schemas.test_design import TestDesignRead as DesignSnapshot
from app.services.artifact_publication_projection import build_projection
from app.services.artifact_release_snapshot import canonical_bytes
from app.services.artifact_test_design import graph_nodes

pytestmark = pytest.mark.no_db


def fixture():
    """空の参照整合graphを既存codecで固定する。"""
    graph = DesignSnapshot(
        id=5,
        project_id=3,
        updated_at="2026-10-18T00:00:00Z",
        version=2,
        name="日本語🧪",
        description="ダミー",
        items=[],
        factors=[],
        levels=[],
        patterns=[],
        values=[],
        links=[],
        columns=[],
        layout={"cells": {}, "widths": {}, "heights": {}},
    )
    source = graph_nodes(graph, canonical_bytes)
    ids = {n["key"]: uuid4() for n in source}
    nodes = [
        SimpleNamespace(
            **{k: v for k, v in n.items() if k not in {"key", "parent"}},
            id=ids[n["key"]],
            parent_id=ids[n["parent"]] if n["parent"] else None,
            ordinal=i,
        )
        for i, n in enumerate(source)
    ]
    release = SimpleNamespace(
        id=uuid4(),
        project_id=3,
        schema_version=1,
        projection_version="internal-preview-v1",
        node_count=len(nodes),
        title=graph.name,
        content_digest="a" * 64,
        source_refs=[dict(kind="test_design", id=5, version=2)],
    )
    return release, nodes


@pytest.mark.parametrize(
    "change", ["schema", "missing_part", "hash", "project", "unknown_node"]
)
def test_broken_sources_are_not_partially_accepted(change):
    """壊れたgraphや未知kindを部分的に成功させない。"""
    release, nodes = fixture()
    if change == "schema":
        release.schema_version = 999
    elif change == "missing_part":
        nodes.pop()
    elif change == "hash":
        nodes[-1].payload["text"] += "x"
    elif change == "unknown_node":
        nodes[0].node_kind = "new_unrecognized_content"
    else:
        release.project_id = 4
    with pytest.raises(BadRequestError):
        build_projection(release, nodes, identity=uuid4())


def test_exact_capacity_and_node_boundaries(monkeypatch):
    """実測byte数とnode数の境界で、1だけ超えた場合も拒否する。"""
    release, nodes = fixture()
    identity = uuid4()
    value = build_projection(release, nodes, identity=identity)
    monkeypatch.setattr(
        "app.services.artifact_publication_projection.MAX_SNAPSHOT_BYTES",
        value.byte_size,
    )
    monkeypatch.setattr(
        "app.services.artifact_publication_projection.MAX_SNAPSHOT_NODES",
        len(value.nodes),
    )
    assert (
        build_projection(release, nodes, identity=identity).public_digest
        == value.public_digest
    )
    monkeypatch.setattr(
        "app.services.artifact_publication_projection.MAX_SNAPSHOT_NODES",
        len(value.nodes) - 1,
    )
    with pytest.raises(ArtifactReleaseTooLargeError):
        build_projection(release, nodes, identity=identity)
    monkeypatch.setattr(
        "app.services.artifact_publication_projection.MAX_SNAPSHOT_NODES",
        len(value.nodes),
    )
    monkeypatch.setattr(
        "app.services.artifact_publication_projection.MAX_SNAPSHOT_BYTES",
        value.byte_size - 1,
    )
    with pytest.raises(ArtifactReleaseTooLargeError):
        build_projection(release, nodes, identity=identity)
    raw = "".join(
        n["payload"]["text"]
        for n in value.nodes
        if n["node_kind"] == "test_design_part"
    )
    assert set(json.loads(raw)).isdisjoint({"id", "project_id", "updated_at"})
    assert all(len(canonical_bytes(n)) <= 256 * 1024 for n in value.nodes)
