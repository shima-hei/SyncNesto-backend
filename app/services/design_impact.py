"""変更内容を比較し、関連する設計は更新せず確認候補を返す。"""

from hashlib import sha256
from types import SimpleNamespace
from typing import Literal, cast

from sqlalchemy import select
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
from app.services.design_paths import design_field
from app.services.specification import SpecificationService

SPECIFICATIONS = SpecificationService()


def _flatten(value, path="content"):
    """項目がUUIDを持つ場合、順番を除いて差分を比較する。"""
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"mappings", "id"}:
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
    if past is None:
        return result

    before_content = past.snapshot.get("content", {})
    before, after = dict(_flatten(before_content)), dict(_flatten(row.content))
    changes = sorted(
        key for key in before.keys() | after.keys() if before.get(key) != after.get(key)
    )
    if not changes:
        return result
    count = len(changes)
    entries = []
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
        path = mapping["source_path"]
        old_value, new_value = design_field(original, path), design_field(row, path)
        if old_value == new_value:
            continue
        target = targets.get(mapping["target_id"])
        if target is None:
            continue
        target_value = design_field(target, mapping["target_path"])
        incompatible = new_value is None or target_value is None
        label = mapping["name"] or path
        detail = f"{label}: {old_value or 'なし'} → {new_value or 'なし'}"
        entries.append(
            DesignImpactItem(
                key=_key(f"mapping/{mapping['id']}"),
                level="error" if incompatible else "warning",
                title=target.title,
                target_type="specification",
                target_id=str(target.id),
                target_version=target.version,
                detail=detail[:500],
            )
        )
    states = {
        item.key: item.status
        for item in db.scalars(
            select(DesignImpactDecision).where(
                DesignImpactDecision.project_id == project_id,
                DesignImpactDecision.specification_id == row.id,
                DesignImpactDecision.source_version == row.version,
            )
        )
    }
    for item in entries:
        item.status = cast(
            "Literal['confirmed', 'needs_action', 'pending']",
            states.get(item.key, "pending"),
        )
    result.items = entries
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
