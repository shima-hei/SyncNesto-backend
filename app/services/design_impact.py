"""変更内容を比較し、関連する設計は更新せず確認候補を返す。"""

from hashlib import sha256
from types import SimpleNamespace
from typing import Literal, cast

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models.design_workspace import DesignImpactDecision
from app.models.specification import Specification, SpecificationRevision
from app.models.user import User
from app.schemas.design_workspace import (
    DesignImpactItem,
    DesignImpactRead,
    DesignImpactUpdate,
)
from app.services.design_impact_checks import mapping_change
from app.services.design_impact_shared import shared_changes
from app.services.specification import SpecificationService

SPECIFICATIONS = SpecificationService()


def _flatten(value, path="content"):
    """項目がUUIDを持つ場合、順番を除いて差分を比較する。"""
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {
                "mappings",
                "id",
                "x-syncnesto-id",
                "x-syncnesto-property-order",
            }:
                continue
            yield from _flatten(child, f"{path}/{key}")
    elif isinstance(value, list) and all(
        isinstance(item, dict) and item.get("id") for item in value
    ):
        for item in value:
            yield from _flatten(item, f"{path}/{item['id']}")
    else:
        yield path, value


def _key(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _mapping_result(before, source, other, mapping, *, reverse):
    change = mapping_change(before, source, other, mapping, reverse=reverse)
    if change is None:
        return None
    level, detail = change
    return DesignImpactItem(
        key=_key(f"mapping/{mapping['id']}"),
        level=level,
        detail=detail[:500],
        title=other.title,
        target_type="specification",
        target_id=str(other.id),
        target_version=other.version,
    )


def _dependent_designs(db: Session, row: Specification):
    document = Specification.content
    referenced = [{"target_id": row.id}]
    step = [{"specification_id": row.id}]
    conditions = [
        document[key].contains(referenced)
        for key in ("mappings", "constraints", "relationships", "transitions")
    ]
    return db.scalars(
        select(Specification)
        .where(
            Specification.project_id == row.project_id,
            Specification.id != row.id,
            Specification.deleted_at.is_(None),
            or_(
                *conditions,
                document["processing_steps"].contains(step),
                document["actions"].contains([{"steps": step}]),
            ),
        )
        .order_by(Specification.id)
    )


def _reference_impact(source: Specification, other: Specification):
    own = source.content
    content = other.content
    related_keys = ("constraints", "relationships", "transitions")
    relations = [
        item
        for kind in related_keys
        for item in content.get(kind, [])
        if item.get("target_id") == source.id
    ]
    processes = [
        step
        for steps in (
            content.get("processing_steps", []),
            *(item.get("steps", []) for item in content.get("actions", [])),
        )
        for step in steps
        if step.get("specification_id") == source.id
    ]
    if not relations and not processes:
        return None
    columns = {item["id"] for item in own.get("columns", [])}
    missing = any(
        item.get("kind") == "foreign_key"
        and not set(item.get("target_column_ids", [])) <= columns
        for item in relations
    )
    detail = (
        "外部キーが存在しない参照カラムを指しています。"
        if missing
        else "更新された画面・API・テーブルを処理または図の接続から参照しています。"
    )
    return DesignImpactItem(
        key=_key(f"reference/{other.id}"),
        level="error" if missing else "review",
        title=other.title,
        target_type="specification",
        target_id=str(other.id),
        target_version=other.version,
        detail=detail,
    )


def analyze_impact(
    db: Session,
    project_id: int,
    row: Specification,
    user: User,
    base_version: int | None,
) -> DesignImpactRead:
    """版比較と関連から要確認候補を出す。候補先の現行設計を変更しない。"""
    candidates = select(SpecificationRevision).where(
        SpecificationRevision.specification_id == row.id,
        SpecificationRevision.number < row.version,
    )
    if base_version is not None:
        candidates = candidates.where(SpecificationRevision.number == base_version)
    candidates = candidates.order_by(SpecificationRevision.number.desc()).limit(20)
    past = None
    for revision in db.scalars(candidates):
        if base_version is not None or revision.snapshot.get("content") != row.content:
            past = revision
            break
    if base_version is not None and past is None:
        raise NotFoundError("比較元の設計版が見つかりません")
    result = DesignImpactRead(
        source_version=row.version,
        base_version=past.number if past is not None else None,
        items=[],
    )
    result.items = [
        DesignImpactItem(
            key=_key(key),
            title=row.title,
            target_type="specification",
            target_id=str(row.id),
            target_version=row.version,
            level=level,
            detail=detail[:500],
        )
        for key, level, detail in shared_changes(db, row)
    ]
    if past is None:
        return _with_decisions(db, row, result)

    before_content = past.snapshot.get("content", {})
    before, after = dict(_flatten(before_content)), dict(_flatten(row.content))
    changes = sorted(
        key for key in before.keys() | after.keys() if before.get(key) != after.get(key)
    )
    if not changes:
        return _with_decisions(db, row, result)
    count = len(changes)
    entries = result.items
    for target in SPECIFICATIONS.links(db, project_id, row.id, user):
        entries.append(
            DesignImpactItem(
                key=_key(f"{target.target_type}/{target.target_id}"),
                level="review",
                title=target.title,
                target_type=target.target_type,
                target_id=target.target_id,
                container_id=target.container_id,
                detail=f"設計の{count}項目が変更されています",
            )
        )
    for item in SPECIFICATIONS.related(
        db, project_id, "specification", str(row.id), user
    ):
        entries.append(
            DesignImpactItem(
                key=_key(f"related/{item.id}"),
                level="review",
                title=item.title,
                target_type="specification",
                target_id=str(item.id),
                target_version=item.version,
                detail=f"参照している設計の{count}項目が変更されています",
            )
        )
    mappings = row.content.get("mappings", [])
    mapping_targets = {item["target_id"] for item in mappings}
    targets = {
        target.id: target
        for target in db.scalars(
            select(Specification).where(
                Specification.project_id == project_id,
                Specification.id.in_(mapping_targets),
                Specification.deleted_at.is_(None),
            )
        )
    }
    original = SimpleNamespace(content=before_content)
    for mapping in mappings:
        target = targets.get(mapping["target_id"])
        if target is None:
            continue
        impact = _mapping_result(original, row, target, mapping, reverse=False)
        if impact:
            entries.append(impact)
    for dependent in _dependent_designs(db, row):
        for mapping in dependent.content.get("mappings", []):
            if mapping["target_id"] == row.id:
                impact = _mapping_result(
                    original, row, dependent, mapping, reverse=True
                )
                if impact:
                    entries.append(impact)
        reference = _reference_impact(row, dependent)
        if reference:
            entries.append(reference)
    return _with_decisions(db, row, result)


def _with_decisions(db: Session, row: Specification, result: DesignImpactRead):
    if not result.items:
        return result
    states = {
        item.key: item.status
        for item in db.scalars(
            select(DesignImpactDecision).where(
                DesignImpactDecision.project_id == row.project_id,
                DesignImpactDecision.specification_id == row.id,
                DesignImpactDecision.source_version == row.version,
            )
        )
    }
    for item in result.items:
        item.status = cast(
            "Literal['confirmed', 'needs_action', 'pending']",
            states.get(item.key, "pending"),
        )
    return result


def save_impact(
    db: Session, project_id: int, design_id: int, data: DesignImpactUpdate, user: User
):
    """競合を防止し、現行の影響候補にだけ判断を記録する。"""
    row = SPECIFICATIONS.get(db, project_id, design_id, lock=True)
    SPECIFICATIONS.check_version(row, data.source_version)
    result = analyze_impact(db, project_id, row, user, None)
    selected = next((item for item in result.items if item.key == data.key), None)
    if selected is None:
        raise NotFoundError("確認対象が見つかりません")
    db.merge(
        DesignImpactDecision(
            project_id=project_id,
            specification_id=design_id,
            source_version=row.version,
            key=data.key,
            status=data.status,
        )
    )
    db.commit()
    selected.status = data.status
    return result
