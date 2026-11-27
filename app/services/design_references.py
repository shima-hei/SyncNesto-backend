"""保存時に設計対象・処理分岐の参照範囲を照合する。"""

from collections import defaultdict
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError
from app.models.design_workspace import DesignCollection
from app.models.specification import Specification
from app.schemas.design_content import DatabaseSpecification, ProcessingStep
from app.schemas.specification import (
    ApiSpecification,
    ScreenSpecification,
    SpecificationContent,
)
from app.services.design_openapi import validate_operation
from app.services.design_paths import design_field


def check_design_references(
    db: Session, project_id: int, content: SpecificationContent, source_id: int | None
) -> None:
    """変更した定義の参照先を同じ案件と設計種別に限定する。"""
    targets: dict[str, set[int]] = defaultdict(set)
    namespaces: dict[str, UUID] = {}
    flows: list[list[ProcessingStep]] = []
    if isinstance(content, ScreenSpecification):
        targets["screen"].update(item.target_id for item in content.transitions)
        flows = [event.steps for event in content.actions]
    if isinstance(content, ApiSpecification):
        if content.operation is not None:
            if content.path:
                validate_operation(content.path, content.operation)
        flows = [content.processing_steps]
        if content.collection_id is not None:
            namespaces["api_set"] = content.collection_id
    if isinstance(content, DatabaseSpecification):
        targets["database"].update(item.target_id for item in content.relationships)
        targets["database"].update(
            item.target_id for item in content.constraints if item.target_id is not None
        )
        if content.collection_id is not None:
            namespaces["db_model"] = content.collection_id

    for flow in flows:
        step_ids = {step.id for step in flow}
        for step in flow:
            if {step.on_success, step.on_failure} - {None} - step_ids:
                raise BadRequestError("処理ステップの分岐先が見つかりません")
            if step.specification_id is None:
                continue
            match step.kind:
                case "api":
                    targets["api"].add(step.specification_id)
                case "db_read" | "db_write":
                    targets["database"].add(step.specification_id)
                case "transition":
                    targets["screen"].add(step.specification_id)
                case _:
                    raise BadRequestError("この処理では設計先を指定できません")

    ids = {
        mapping.target_id
        for mapping in content.mappings
        if mapping.target_id != source_id
    } | {
        identifier
        for values in targets.values()
        for identifier in values
        if identifier != source_id
    }
    rows = (
        {
            row.id: (row.kind, row.content)
            for row in db.execute(
                select(
                    Specification.id, Specification.kind, Specification.content
                ).where(
                    Specification.project_id == project_id,
                    Specification.id.in_(ids),
                    Specification.deleted_at.is_(None),
                )
            )
        }
        if ids
        else {}
    )
    if source_id is not None:
        rows[source_id] = (content.type, content.model_dump(mode="json"))
    if any(
        rows.get(identifier, (None, None))[0] != kind
        for kind, values in targets.items()
        for identifier in values
    ):
        raise BadRequestError("参照先は同じ案件にある有効な設計から選択してください")
    for kind, identifier in namespaces.items():
        if (
            db.scalar(
                select(DesignCollection.id).where(
                    DesignCollection.project_id == project_id,
                    DesignCollection.id == identifier,
                    DesignCollection.kind == kind,
                )
            )
            is None
        ):
            raise BadRequestError("仕様セット・DBモデルは同じ案件から選択してください")
    if isinstance(content, DatabaseSpecification):
        for constraint in content.constraints:
            if constraint.kind != "foreign_key" or constraint.target_id is None:
                continue
            reference = rows[constraint.target_id][1]
            if reference["collection_id"] != (
                str(content.collection_id) if content.collection_id else None
            ):
                raise BadRequestError("外部キーの参照先は同じDBモデルにしてください")
            target_columns = {item["id"] for item in reference["columns"]}
            if (
                not {str(identifier) for identifier in constraint.target_column_ids}
                <= target_columns
            ):
                raise BadRequestError("外部キーの参照カラムが見つかりません")
    own = SimpleNamespace(title="", summary="", content=content.model_dump(mode="json"))
    entries = set()
    for mapping in content.mappings:
        key = (
            mapping.source_path,
            mapping.target_id,
            mapping.target_path,
            mapping.meaning,
        )
        if key in entries:
            raise BadRequestError("同じ項目の対応を重複して登録できません")
        entries.add(key)
        if not mapping.source_path.startswith(
            "content/"
        ) or not mapping.target_path.startswith("content/"):
            raise BadRequestError("設計項目のパスを指定してください")
        if mapping.target_id == source_id or mapping.target_id not in rows:
            raise BadRequestError("関連先は同じ案件にある別の設計を指定してください")
        other = SimpleNamespace(
            title="", summary="", content=rows[mapping.target_id][1]
        )
        if (
            design_field(own, mapping.source_path) is None
            or design_field(other, mapping.target_path) is None
        ):
            raise BadRequestError("指定された対応元または対応先が見つかりません")
