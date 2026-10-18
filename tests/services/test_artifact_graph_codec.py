"""単一の巨大セルもJSON escape後の上限内で可逆に分割する。"""

import hashlib
import json
import time
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.exceptions import ArtifactReleaseTooLargeError
from app.schemas.artifact_release import ArtifactPreviewRequest
from app.schemas.test_design import TestDesignRead as DesignRead
from app.services.artifact_release_snapshot import (
    MAX_SNAPSHOT_BYTES,
    ArtifactSnapshotBuilder,
    Snapshot,
    canonical_bytes,
)
from app.services.artifact_test_design import MAX_GRAPH_NODE_BYTES, graph_nodes

pytestmark = pytest.mark.no_db


def test_large_unicode_cell_roundtrip():
    """UUID、改行、引用符、絵文字、単一項目内の巨大データを保持する。"""
    data = DesignRead.model_validate(
        dict(
            id=17,
            project_id=3,
            name="テスト設計",
            version=8,
            updated_at=datetime.now(UTC),
            factors=[],
            levels=[],
            patterns=[],
            values=[],
            links=[],
            layout={},
            columns=[
                {"id": uuid4(), "key": f"custom_{i}", "label": str(i)}
                for i in range(30)
            ],
            items=[
                {
                    "id": uuid4(),
                    "code": "T1",
                    "custom_values": {
                        f"custom_{i}": ('日本語\\"\n🧪' * 1500) for i in range(30)
                    },
                }
            ],
        )
    )
    nodes = graph_nodes(data, canonical_bytes)
    assert len(nodes) > 4
    assert all(len(canonical_bytes(node)) <= MAX_GRAPH_NODE_BYTES for node in nodes)
    assert all(node["source_id"] == 17 for node in nodes)
    raw = "".join(n["payload"]["text"] for n in nodes[1:]).encode()
    assert hashlib.sha256(raw).hexdigest() == nodes[0]["payload"]["graph_digest"]
    assert json.loads(raw) == data.model_dump(mode="json")
    assert graph_nodes(data, canonical_bytes) == nodes


@pytest.mark.parametrize("kind", ["items", "levels", "patterns", "values", "layout"])
def test_large_valid_graph_collections(kind, record_property):
    """各上限の独立した有効graphでサイズ・復元・処理時間を記録する。"""
    data = dict(
        id=7,
        project_id=3,
        name="大規模確認",
        version=1,
        updated_at=datetime.now(UTC),
        items=[],
        factors=[],
        levels=[],
        patterns=[],
        values=[],
        links=[],
        columns=[],
        layout={},
    )
    factor = uuid4()
    if kind == "items":
        data["items"] = [{"id": uuid4(), "code": f"T{i}"} for i in range(10000)]
    elif kind == "levels":
        data["factors"] = [{"id": factor, "name": "因子"}]
        data["levels"] = [
            {"id": uuid4(), "factor_id": factor, "name": str(i)} for i in range(10000)
        ]
    elif kind == "patterns":
        data["patterns"] = [{"id": uuid4(), "code": f"P{i}"} for i in range(10000)]
    elif kind == "values":
        data["factors"] = [{"id": uuid4(), "name": str(i)} for i in range(100)]
        data["patterns"] = [{"id": uuid4(), "code": f"P{i}"} for i in range(1000)]
        data["values"] = [
            {
                "id": uuid4(),
                "pattern_id": p["id"],
                "factor_id": f["id"],
                "level_id": None,
            }
            for p in data["patterns"]
            for f in data["factors"]
        ]
    else:
        data["layout"] = {
            "cells": {f"items:{i}:content": {"bold": True} for i in range(200000)}
        }
    started = time.perf_counter()
    graph = DesignRead.model_validate(data)
    nodes = graph_nodes(graph, canonical_bytes)
    raw = "".join(n["payload"]["text"] for n in nodes[1:])
    assert json.loads(raw) == graph.model_dump(mode="json")
    assert all(len(canonical_bytes(n)) <= MAX_GRAPH_NODE_BYTES for n in nodes)
    record_property("graph_kind", kind)
    record_property("stored_bytes", sum(len(canonical_bytes(n)) for n in nodes))
    record_property("parts", len(nodes) - 1)
    record_property("seconds", time.perf_counter() - started)


def test_real_32mib_boundary_is_atomic():
    """実際の32MiBちょうどを受理し、次のnodeを部分追加しない。"""
    snapshot = Snapshot(
        ArtifactPreviewRequest.model_validate(
            {"source": {"kind": "test_design", "id": 1, "version": 1}}
        ),
        datetime.now(UTC),
    )
    index = 0
    while snapshot.byte_size < MAX_SNAPSHOT_BYTES:
        node = {
            "key": f"part:{index}",
            "parent": None,
            "position": index,
            "node_kind": "test_design_part",
            "source_id": 1,
            "source_version": 1,
            "payload": {"text": ""},
        }
        overhead = len(canonical_bytes(node))
        remaining = MAX_SNAPSHOT_BYTES - snapshot.byte_size
        length = min(MAX_GRAPH_NODE_BYTES - overhead, remaining - overhead)
        assert length >= 0
        node["payload"]["text"] = "x" * length
        ArtifactSnapshotBuilder._append_node(snapshot, node)
        index += 1
    assert snapshot.byte_size == MAX_SNAPSHOT_BYTES
    count = len(snapshot.nodes)
    with pytest.raises(ArtifactReleaseTooLargeError):
        ArtifactSnapshotBuilder._append_node(
            snapshot,
            {
                "key": "overflow",
                "parent": None,
                "position": index,
                "node_kind": "test_design_part",
                "source_id": 1,
                "source_version": 1,
                "payload": {"text": "x"},
            },
        )
    assert snapshot.byte_size == MAX_SNAPSHOT_BYTES and len(snapshot.nodes) == count
