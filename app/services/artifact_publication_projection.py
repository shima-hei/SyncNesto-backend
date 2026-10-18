"""固定本文から公開する値だけを組み立てる純粋な投影。"""

import copy
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid5

from pydantic import ValidationError

from app.core import error_messages
from app.core.exceptions import ArtifactReleaseTooLargeError, BadRequestError
from app.schemas.artifact_publication import (
    PUBLIC_PROJECTION,
    PublicFile,
    PublicTestDesignGraph,
)
from app.schemas.specification import ApiSpecification, ScreenSpecification
from app.services.artifact_release_snapshot import (
    MAX_SNAPSHOT_BYTES,
    MAX_SNAPSHOT_NODES,
    canonical_bytes,
    digest,
)
from app.services.artifact_test_design import (
    GRAPH_COLLECTIONS,
    GRAPH_SCHEMA,
    MAX_GRAPH_NODE_BYTES,
)

PUBLIC_GRAPH_SCHEMA = "public-test-design-v1"
NAMESPACE = UUID("86ca4166-04b2-4d70-8bc2-0168ef5e6c73")
PAYLOAD_FIELDS = {
    "requirement_document": (
        "title",
        "document_code",
        "purpose",
        "target_system_name",
        "client_name",
        "status",
    ),
    "section": ("title", "section_type", "content", "status", "sort_order"),
    "requirement": (
        "requirement_code",
        "requirement_type",
        "category",
        "title",
        "description",
        "rationale",
        "acceptance_criteria",
        "priority",
        "status",
        "source",
    ),
    "requirement_detail": ("detail_type", "detail_json"),
    "specification": ("title", "kind", "summary", "content", "status"),
}
EXCLUDED_METADATA = [
    "管理項目としての担当者・作成者ID",
    "管理項目としての組織・Project・元資料ID",
    "関連先の自動取得・内部画面への操作リンク",
    "内部コメント・監査ログ・判断履歴",
    "テスト実行結果・Issue・Evidence",
    "Storageの保存先・署名URL",
]


def invalid_projection() -> BadRequestError:
    """内容をエラーに載せず、非対応や破損を明示する。"""
    return BadRequestError(error_messages.PUBLICATION_SOURCE_INVALID)


@dataclass
class PublicationProjection:
    """未保存の公開表現と社内対応。"""

    title: str
    nodes: list[dict]
    files: list[dict]
    source_manifest: dict
    source_manifest_digest: str
    public_digest: str
    byte_size: int

    def preview(self) -> dict:
        """本文を含まない確認用の要約。"""
        return dict(
            title=self.title,
            projection_version=PUBLIC_PROJECTION,
            public_digest=self.public_digest,
            source_manifest_digest=self.source_manifest_digest,
            node_count=len(self.nodes),
            node_counts=dict(Counter(n["node_kind"] for n in self.nodes)),
            byte_size=self.byte_size,
            files=self.files,
            excluded_metadata=EXCLUDED_METADATA,
        )


def public_id(kind: str, identity: Any) -> str:
    """表示参照を用途別の不透明なUUIDへ写す。認可の代わりにはしない。"""
    return str(uuid5(NAMESPACE, f"{PUBLIC_PROJECTION}:{kind}:{identity}"))


def _test_graph(root, parts, project_id: int) -> dict:
    """既存chunkを検証してから内部envelopeを除く。"""
    if root.payload.get("graph_schema") != GRAPH_SCHEMA or len(
        parts
    ) != root.payload.get("part_count"):
        raise invalid_projection()
    for i, part in enumerate(parts):
        if (
            part.node_kind != "test_design_part"
            or part.parent_id != root.id
            or part.position != i
            or part.ordinal != root.ordinal + i + 1
            or part.source_id != root.source_id
            or part.source_version != root.source_version
            or part.payload.get("graph_schema") != GRAPH_SCHEMA
            or part.payload.get("index") != i
            or not isinstance(part.payload.get("text"), str)
        ):
            raise invalid_projection()
    text = "".join(part.payload["text"] for part in parts)
    if hashlib.sha256(text.encode()).hexdigest() != root.payload.get("graph_digest"):
        raise invalid_projection()
    try:
        graph = json.loads(text)
        if (
            graph["id"] != root.source_id
            or graph["version"] != root.source_version
            or graph["project_id"] != project_id
            or any(
                len(graph[k]) != root.payload["counts"][k] for k in GRAPH_COLLECTIONS
            )
            or set(graph)
            != set(PublicTestDesignGraph.model_fields)
            | {"id", "project_id", "updated_at"}
        ):
            raise invalid_projection()
        projected = {
            k: copy.deepcopy(graph[k]) for k in PublicTestDesignGraph.model_fields
        }
        PublicTestDesignGraph.model_validate(projected)
        return projected
    except (ValueError, TypeError, KeyError, ValidationError):
        raise invalid_projection() from None


def _graph_nodes(root, graph: dict, identity: UUID) -> list[dict]:
    """公開graphを再分割する。内部chunkを転送しない。"""
    text = canonical_bytes(graph).decode()
    parent = str(uuid5(identity, str(root.id)))

    def part(index, fragment):
        return dict(
            id=str(uuid5(identity, f"{root.id}:part:{index}")),
            parent_id=parent,
            position=index,
            node_kind="test_design_part",
            source_version=root.source_version,
            payload=dict(graph_schema=PUBLIC_GRAPH_SCHEMA, index=index, text=fragment),
        )

    parts, offset = [], 0
    while offset < len(text):
        low, high = 1, min(len(text) - offset, MAX_GRAPH_NODE_BYTES)
        while low < high:
            mid = (low + high + 1) // 2
            if (
                len(canonical_bytes(part(len(parts), text[offset : offset + mid])))
                <= MAX_GRAPH_NODE_BYTES - 256
            ):
                low = mid
            else:
                high = mid - 1
        parts.append(part(len(parts), text[offset : offset + low]))
        offset += low
    return [
        dict(
            id=parent,
            parent_id=None,
            position=root.position,
            node_kind="test_design",
            source_version=root.source_version,
            payload=dict(
                title=graph["name"],
                graph_schema=PUBLIC_GRAPH_SCHEMA,
                graph_digest=hashlib.sha256(text.encode()).hexdigest(),
                part_count=len(parts),
                counts={k: len(graph[k]) for k in GRAPH_COLLECTIONS},
            ),
        ),
        *parts,
    ]


def build_projection(
    release, source_nodes: list, preservation=None, *, identity: UUID
) -> PublicationProjection:
    """公開schemaと値を明示し、不明な構成は欠落させず拒否する。"""
    if (
        release.schema_version != 1
        or release.projection_version != "internal-preview-v1"
        or not source_nodes
    ):
        raise invalid_projection()
    if len(source_nodes) != release.node_count:
        raise invalid_projection()
    nodes, consumed, id_map = [], set(), {}
    for row in source_nodes:
        if row.id in consumed:
            continue
        if row.node_kind == "test_design":
            parts = [n for n in source_nodes if n.parent_id == row.id]
            graph = _test_graph(row, parts, release.project_id)
            projected_nodes = _graph_nodes(row, graph, identity)
            nodes.extend(projected_nodes)
            id_map.update({n["id"]: str(row.id) for n in projected_nodes})
            consumed.update(p.id for p in parts)
            continue
        fields = PAYLOAD_FIELDS.get(row.node_kind)
        if fields is None or set(row.payload) != set(fields):
            raise invalid_projection()
        payload = {k: copy.deepcopy(row.payload[k]) for k in fields}
        if row.node_kind == "specification":
            content = payload["content"]
            try:
                schema = {"api": ApiSpecification, "screen": ScreenSpecification}[
                    payload["kind"]
                ]
                schema.model_validate(content)
                # 既存型のdefault_factoryで新しい参照UUIDを発生させない。
                for key in (
                    "request_fields",
                    "response_fields",
                    "responses",
                    "fields",
                    "actions",
                    "states",
                ):
                    if any("id" not in item for item in content.get(key, [])):
                        raise invalid_projection()
            except (KeyError, TypeError, ValidationError):
                raise invalid_projection() from None
        node_id = str(uuid5(identity, str(row.id)))
        id_map[node_id] = str(row.id)
        nodes.append(
            dict(
                id=node_id,
                parent_id=str(uuid5(identity, str(row.parent_id)))
                if row.parent_id
                else None,
                position=row.position,
                node_kind=row.node_kind,
                source_version=row.source_version,
                payload=payload,
            )
        )
    for ordinal, node in enumerate(nodes):
        node["ordinal"] = ordinal
        node["content_digest"] = digest(node)
    ids = {n["id"] for n in nodes}
    if any(n["parent_id"] and n["parent_id"] not in ids for n in nodes):
        raise invalid_projection()
    files = []
    if preservation is not None:
        for index, item in enumerate(preservation.manifest["files"]):
            files.append(
                PublicFile(
                    id=UUID(public_id("file", f"{preservation.id}:{index}")),
                    **{
                        k: item[k]
                        for k in (
                            "filename",
                            "content_type",
                            "byte_size",
                            "content_digest",
                        )
                    },
                ).model_dump(mode="json")
            )
    manifest = dict(
        projection_version=PUBLIC_PROJECTION,
        release_id=str(release.id),
        body_digest=release.content_digest,
        preservation_id=str(preservation.id) if preservation else None,
        preservation_digest=preservation.manifest_digest if preservation else None,
        sources=release.source_refs,
        id_map=id_map,
    )
    ordinals = {n["id"]: n["ordinal"] for n in nodes}
    subject_nodes = [
        dict(
            **{
                k: v
                for k, v in n.items()
                if k not in {"id", "parent_id", "content_digest"}
            },
            parent_ordinal=ordinals[n["parent_id"]] if n["parent_id"] else None,
        )
        for n in nodes
    ]
    subject = dict(
        schema_version=1,
        projection_version=PUBLIC_PROJECTION,
        title=release.title,
        nodes=subject_nodes,
        files=files,
    )
    size = len(canonical_bytes(dict(subject, nodes=nodes)))
    if size > MAX_SNAPSHOT_BYTES or len(nodes) > MAX_SNAPSHOT_NODES:
        raise ArtifactReleaseTooLargeError()
    return PublicationProjection(
        release.title, nodes, files, manifest, digest(manifest), digest(subject), size
    )
