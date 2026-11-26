"""正式承認版と保存済みの編集内容を、固定版の論理IDで比較する。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, cast

from app.models.artifact_release import ArtifactReleaseNode
from app.schemas.requirement_workflow import RequirementBaselineChange
from app.services.artifact_release_snapshot import digest

ContentKind = Literal[
    "requirement_document", "section", "requirement", "requirement_detail"
]
REVIEW_IGNORED_FIELDS = {"status", "source", "sort_order"}
CONTENT_KINDS = ("requirement_document", "section", "requirement", "requirement_detail")


def workflow_content_digest(nodes: Iterable[dict]) -> str:
    """レビュー依頼が対象として確定した内容と同じ規則でhash化する。"""
    return digest(
        [
            {
                "kind": node["node_kind"],
                "source_id": node["source_id"],
                "parent": node["parent"],
                "position": node["position"],
                "payload": {
                    key: value
                    for key, value in node["payload"].items()
                    if key not in REVIEW_IGNORED_FIELDS
                },
            }
            for node in nodes
        ]
    )


def review_scopes_for_changes(
    changes: list[RequirementBaselineChange], nodes: list[ContentNode]
) -> list[tuple[str, ...]] | None:
    """変更単位ごとに、必要範囲を含む章・個別要件の候補を得る。"""
    current = {node.key: node for node in nodes}
    scopes = []
    for item in changes:
        if (
            item.kind == "requirement_document"
            or item.change == "removed"
            or {"parent", "position"}.intersection(item.fields)
        ):
            return None
        key = f"{item.kind}:{item.id}"
        parent = current[key].parent
        if item.kind == "requirement" and parent and current[parent].kind == "section":
            scopes.append((parent, key))
        else:
            scopes.append((key,))
    return scopes


@dataclass(frozen=True)
class ContentNode:
    """依頼ごとに変わる固定版本文IDを、対応する論理IDに置き換える。"""

    key: str
    kind: ContentKind
    source_id: int
    parent: str | None
    payload: dict


def saved_content(rows: Iterable[ArtifactReleaseNode]) -> list[ContentNode]:
    """取得順が固定版の表示順である、定義書の本文を表す。"""
    ordered = list(rows)
    keys = {node.id: f"{node.node_kind}:{node.source_id}" for node in ordered}
    return [
        ContentNode(
            keys[node.id],
            cast(ContentKind, node.node_kind),
            node.source_id,
            keys.get(node.parent_id) if node.parent_id is not None else None,
            node.payload,
        )
        for node in ordered
    ]


def editing_content(nodes: Iterable[dict]) -> list[ContentNode]:
    """作成前の読み取り専用スナップショットを同じ形で表す。"""
    return [
        ContentNode(
            node["key"],
            node["node_kind"],
            node["source_id"],
            node["parent"],
            node["payload"],
        )
        for node in nodes
        if node["node_kind"] in CONTENT_KINDS
    ]


def _siblings(
    nodes: list[ContentNode], other: dict[str, ContentNode]
) -> dict[str, int]:
    """追加・削除による通番のずれを除き、残った項目の順序だけを見る。"""
    offsets: dict[tuple[str | None, ContentKind], int] = defaultdict(int)
    positions = {}
    for item in nodes:
        match = other.get(item.key)
        if match is not None and match.parent == item.parent:
            container = (item.parent, item.kind)
            positions[item.key] = offsets[container]
            offsets[container] += 1
    return positions


def compare_content(
    before: list[ContentNode], after: list[ContentNode]
) -> list[RequirementBaselineChange]:
    """同一章・要件の本文差分と追加・削除を、型別詳細も含めて求める。"""
    old = {item.key: item for item in before}
    new = {item.key: item for item in after}
    before_positions = _siblings(before, new)
    after_positions = _siblings(after, old)
    changes = []
    modified_details: set[str] = set()
    for key in dict.fromkeys([*new, *old]):
        source, target = old.get(key), new.get(key)
        item = target or source
        assert item is not None
        fields: list[str] = []
        if source is None:
            change = "added"
        elif target is None:
            change = "removed"
        else:
            change = "changed"
            props = source.payload.keys() | target.payload.keys()
            fields = sorted(
                field
                for field in props - REVIEW_IGNORED_FIELDS
                if source.payload.get(field) != target.payload.get(field)
            )
            if source.parent != target.parent:
                fields.append("parent")
            elif before_positions.get(key) != after_positions.get(key):
                fields.append("position")
            if not fields:
                continue
        if item.kind == "requirement_detail":
            modified_details.update(
                candidate.parent
                for candidate in (source, target)
                if candidate is not None and candidate.parent is not None
            )
            continue
        changes.append(
            RequirementBaselineChange(
                kind=item.kind,
                id=item.source_id,
                title=str(item.payload.get("title") or ""),
                requirement_code=item.payload.get("requirement_code"),
                change=change,
                fields=fields,
            )
        )
    by_requirement = {
        f"requirement:{item.id}": item for item in changes if item.kind == "requirement"
    }
    for key in new:
        if key not in modified_details or key not in old:
            continue
        change = by_requirement.get(key)
        if change is not None:
            change.fields.append("details")
        else:
            item = new[key]
            changes.append(
                RequirementBaselineChange(
                    kind="requirement",
                    id=item.source_id,
                    title=str(item.payload.get("title") or ""),
                    requirement_code=item.payload.get("requirement_code"),
                    change="changed",
                    fields=["details"],
                )
            )
    return changes
