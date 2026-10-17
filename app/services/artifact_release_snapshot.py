"""認可後の業務データを、版と構成を含む不変な本文へ投影する。"""

import copy
import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import (
    ArtifactReleaseTooLargeError,
    BadRequestError,
    NotFoundError,
    VersionConflictError,
)
from app.models.requirement import (
    Requirement,
    RequirementDetail,
    RequirementDocument,
    RequirementSection,
)
from app.models.specification import Specification
from app.schemas.artifact_release import ArtifactPreview, ArtifactPreviewRequest

MAX_PAGE_BYTES = 1024 * 1024
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
MAX_SNAPSHOT_NODES = 50000


def canonical_bytes(value: Any) -> bytes:
    """順序付き配列とnullを保持してJSONを正規化する。"""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def digest(value: Any) -> str:
    """schemaで指定したJSONの内容hashを返す。"""
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


@dataclass
class Snapshot:
    """DBの現在値を再参照せず保存できる、固定版の素材。"""

    request: ArtifactPreviewRequest
    captured_at: datetime
    title: str = ""
    nodes: list[dict] = field(default_factory=list)
    scope_digest: str = ""
    byte_size: int = 0
    seen: set[str] = field(default_factory=set)

    def preview(self) -> ArtifactPreview:
        """本文を返さず対象構成とhashを提示する。"""
        return ArtifactPreview(
            **self.request.model_dump(),
            title=self.title,
            captured_at=self.captured_at,
            scope_digest=self.scope_digest,
            node_count=len(self.nodes),
            node_counts=dict(Counter(n["node_kind"] for n in self.nodes)),
            byte_size=self.byte_size,
        )


class ArtifactSnapshotBuilder:
    """同じDB snapshot内で、元の型と親子関係を維持して読む。"""

    def build(
        self,
        db: Session,
        project_id: int,
        data: ArtifactPreviewRequest,
        captured_at: datetime,
    ) -> Snapshot:
        """元対象の版・所属を確認し、本文と構成のhashを作る。"""
        snapshot = Snapshot(data, captured_at)
        for index, ref in enumerate([data.source, *data.included_sources]):
            if ref.kind == "specification":
                row = db.scalar(
                    select(Specification).where(
                        Specification.id == ref.id,
                        Specification.project_id == project_id,
                        Specification.deleted_at.is_(None),
                    )
                )
                self._version(row, ref.version)
                assert row is not None
                self._append(
                    snapshot,
                    "specification",
                    row,
                    None,
                    index,
                    {
                        k: copy.deepcopy(getattr(row, k))
                        for k in ("title", "kind", "summary", "content", "status")
                    },
                )
            elif ref.kind == "requirement_document":
                document = self._document(db, project_id, ref.id)
                self._version(document, ref.version)
                key = self._append(
                    snapshot,
                    "requirement_document",
                    document,
                    None,
                    index,
                    {
                        k: getattr(document, k)
                        for k in (
                            "title",
                            "document_code",
                            "purpose",
                            "target_system_name",
                            "client_name",
                            "status",
                        )
                    },
                )
                self._document_children(db, snapshot, document.id, key)
            else:
                requirement = db.scalar(
                    select(Requirement)
                    .join(RequirementDocument)
                    .where(
                        Requirement.id == ref.id,
                        Requirement.deleted_at.is_(None),
                        RequirementDocument.project_id == project_id,
                        RequirementDocument.deleted_at.is_(None),
                    )
                )
                if requirement is None:
                    raise NotFoundError()
                self._document(db, project_id, requirement.document_id)
                if requirement.section_id is not None:
                    section = db.scalar(
                        select(RequirementSection).where(
                            RequirementSection.id == requirement.section_id,
                            RequirementSection.document_id == requirement.document_id,
                            RequirementSection.deleted_at.is_(None),
                        )
                    )
                    if section is None:
                        raise NotFoundError()
                self._version(requirement, ref.version)
                self._requirement(db, snapshot, requirement, None, index)
        snapshot.title = snapshot.nodes[0]["payload"]["title"]
        snapshot.scope_digest = digest(
            {
                "schema_version": 1,
                "projection_version": data.projection_version,
                "sources": [
                    s.model_dump() for s in [data.source, *data.included_sources]
                ],
                "nodes": snapshot.nodes,
            }
        )
        return snapshot

    @staticmethod
    def _version(row, expected: int) -> None:
        """版不一致の応答へ内部本文を含めない。"""
        if row is None:
            raise NotFoundError()
        if row.version != expected:
            raise VersionConflictError({"version": row.version})

    @staticmethod
    def _document(db: Session, project_id: int, document_id: int):
        """所属Projectと現在の文書の有効状態を検証する。"""
        row = db.scalar(
            select(RequirementDocument).where(
                RequirementDocument.id == document_id,
                RequirementDocument.project_id == project_id,
                RequirementDocument.deleted_at.is_(None),
            )
        )
        if row is None:
            raise NotFoundError()
        return row

    def _document_children(self, db, snapshot, document_id, parent):
        """章の並びと、未配置要件を含む有効な子集合を固定する。"""
        sections = list(
            db.scalars(
                select(RequirementSection)
                .where(
                    RequirementSection.document_id == document_id,
                    RequirementSection.deleted_at.is_(None),
                )
                .order_by(RequirementSection.sort_order, RequirementSection.id)
            )
        )
        requirements = list(
            db.scalars(
                select(Requirement)
                .where(
                    Requirement.document_id == document_id,
                    Requirement.deleted_at.is_(None),
                    or_(
                        Requirement.section_id.is_(None),
                        Requirement.section_id.in_([s.id for s in sections]),
                    ),
                )
                .order_by(Requirement.id)
            )
        )
        groups: dict[int | None, list[Requirement]] = {}
        for item in requirements:
            groups.setdefault(item.section_id, []).append(item)
        detail_groups: dict[int, list[RequirementDetail]] = {}
        for detail in db.scalars(
            select(RequirementDetail)
            .where(RequirementDetail.requirement_id.in_([r.id for r in requirements]))
            .order_by(RequirementDetail.id)
        ):
            detail_groups.setdefault(detail.requirement_id, []).append(detail)
        for index, section in enumerate(sections):
            key = self._append(
                snapshot,
                "section",
                section,
                parent,
                index,
                {
                    k: getattr(section, k)
                    for k in (
                        "title",
                        "section_type",
                        "content",
                        "status",
                        "sort_order",
                    )
                },
            )
            for position, item in enumerate(groups.get(section.id, [])):
                self._requirement(
                    db, snapshot, item, key, position, detail_groups.get(item.id, [])
                )
        for index, item in enumerate(groups.get(None, []), start=len(sections)):
            self._requirement(
                db, snapshot, item, parent, index, detail_groups.get(item.id, [])
            )

    def _requirement(self, db, snapshot, requirement, parent, position, details=None):
        """要件と型別詳細を固定し、担当・旧承認・内部関連は混ぜない。"""
        key = self._append(
            snapshot,
            "requirement",
            requirement,
            parent,
            position,
            {
                k: getattr(requirement, k)
                for k in (
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
                )
            },
        )
        if details is None:
            details = db.scalars(
                select(RequirementDetail)
                .where(RequirementDetail.requirement_id == requirement.id)
                .order_by(RequirementDetail.id)
            )
        for index, detail in enumerate(details):
            self._append(
                snapshot,
                "requirement_detail",
                detail,
                key,
                index,
                {
                    "detail_type": detail.detail_type,
                    "detail_json": copy.deepcopy(detail.detail_json),
                },
            )

    @staticmethod
    def _append(snapshot, kind, row, parent, position, payload) -> str:
        """本文を切り捨てず、応答可能な大きさだけを受け入れる。"""
        key = f"{kind}:{row.id}"
        if key in snapshot.seen:
            raise BadRequestError(error_messages.ARTIFACT_SOURCE_OVERLAP)
        node = {
            "key": key,
            "parent": parent,
            "position": position,
            "node_kind": kind,
            "source_id": row.id,
            "source_version": getattr(row, "version", None),
            "payload": payload,
        }
        size = len(canonical_bytes(node))
        if (
            size > MAX_PAGE_BYTES - 4096
            or snapshot.byte_size + size > MAX_SNAPSHOT_BYTES
            or len(snapshot.nodes) >= MAX_SNAPSHOT_NODES
        ):
            raise ArtifactReleaseTooLargeError()
        snapshot.nodes.append(node)
        snapshot.seen.add(key)
        snapshot.byte_size += size
        return key
