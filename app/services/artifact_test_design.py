"""UUIDを含む設計graphを、可逆なJSON文字列のpartへ分割する。"""

import hashlib
from collections.abc import Callable
from typing import Any

from app.schemas.test_design import TestDesignRead

GRAPH_SCHEMA = "test-design-graph-v1"
MAX_GRAPH_NODE_BYTES = 256 * 1024
GRAPH_COLLECTIONS = (
    "pattern_tables",
    "items",
    "factors",
    "levels",
    "patterns",
    "values",
    "links",
    "columns",
    "expected_values",
    "expected_selections",
)


def graph_nodes(graph: TestDesignRead, encode: Callable[[Any], bytes]) -> list[dict]:
    """JSON escape後のnodeサイズを測り、巨大な単一セルも欠落なく保存する。"""
    data = graph.model_dump(mode="json")
    raw = encode(data)
    text = raw.decode("utf-8")
    key = f"test_design:{graph.id}"

    def part(index: int, fragment: str) -> dict:
        return {
            "key": f"test_design_part:{graph.id}:{index}",
            "parent": key,
            "position": index,
            "node_kind": "test_design_part",
            "source_id": graph.id,
            "source_version": graph.version,
            "payload": {"graph_schema": GRAPH_SCHEMA, "index": index, "text": fragment},
        }

    parts = []
    offset = 0
    while offset < len(text):
        # 1文字のescape後は最大6byte。通常は大きなpartを作り、二分探索で実測する。
        low, high = 1, min(len(text) - offset, MAX_GRAPH_NODE_BYTES)
        while low < high:
            middle = (low + high + 1) // 2
            if (
                len(encode(part(len(parts), text[offset : offset + middle])))
                <= MAX_GRAPH_NODE_BYTES
            ):
                low = middle
            else:
                high = middle - 1
        parts.append(part(len(parts), text[offset : offset + low]))
        offset += low
    return [
        {
            "key": key,
            "parent": None,
            "position": 0,
            "node_kind": "test_design",
            "source_id": graph.id,
            "source_version": graph.version,
            "payload": {
                "title": graph.name,
                "graph_schema": GRAPH_SCHEMA,
                "graph_digest": hashlib.sha256(raw).hexdigest(),
                "part_count": len(parts),
                "counts": {name: len(data[name]) for name in GRAPH_COLLECTIONS},
            },
        },
        *parts,
    ]
