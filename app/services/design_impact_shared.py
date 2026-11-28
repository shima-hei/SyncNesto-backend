"""共通設定の変更は、設計本体の版を変えずに影響候補として提示する。"""

from urllib.parse import unquote
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.design_workspace import DesignCollection, WireframePartRevision
from app.models.specification import Specification, SpecificationRevision
from app.services.design_impact_checks import Level


def _part_changes(db: Session, row: Specification) -> list[tuple[str, Level, str]]:
    pins = {
        (UUID(pin["part_id"]), pin["version"]): layer["name"] or "ワイヤーフレーム部品"
        for board in row.content.get("artboards", [])
        for layer in board.get("components", [])
        if (pin := layer.get("part_pin"))
    }
    if not pins:
        return []
    parts = WireframePartRevision
    latest = {
        identifier: version
        for identifier, version in db.execute(
            select(parts.part_id, func.max(parts.version))
            .where(
                parts.project_id == row.project_id,
                parts.part_id.in_({identifier for identifier, _ in pins}),
            )
            .group_by(parts.part_id)
        )
    }
    return [
        (
            f"part/{identifier}/{version}/{latest[identifier]}",
            "review",
            f"共通部品「{name}」に新版 {latest[identifier]} があります。"
            f"画面は版 {version} を使用中です。",
        )
        for (identifier, version), name in pins.items()
        if latest.get(identifier, version) > version
    ]


def _used_components(
    config: dict, operation: dict, path: str
) -> dict[tuple[str, str], object]:
    """参照の循環を停止し、指定した操作が使うローカルの共通定義を辿る。"""
    components = config.get("components", {})
    paths = config.get("paths", {})
    pending = [operation, paths.get(path, {}) if isinstance(paths, dict) else {}]
    seen: dict[tuple[str, str], object] = {}
    while pending:
        value = pending.pop()
        if isinstance(value, list):
            pending.extend(value)
        if not isinstance(value, dict):
            continue
        for key in ("$ref", "$dynamicRef"):
            ref = value.get(key)
            if not isinstance(ref, str) or not ref.startswith("#/components/"):
                continue
            parts = unquote(ref[13:]).split("/")
            if len(parts) < 2:
                continue
            kind, name = (
                part.replace("~1", "/").replace("~0", "~") for part in parts[:2]
            )
            if (kind, name) in seen:
                continue
            category = components.get(kind) if isinstance(components, dict) else None
            target = category.get(name) if isinstance(category, dict) else None
            seen[kind, name] = target
            pending.append(target)
        pending.extend(value.values())
    return seen


def _api_changes(db: Session, row: Specification) -> list[tuple[str, Level, str]]:
    collection_id = row.content.get("collection_id")
    operation = row.content.get("operation")
    if not collection_id or not operation:
        return []
    table = SpecificationRevision
    fixed = db.scalar(
        select(table.snapshot["collection"])
        .where(
            table.specification_id == row.id,
            table.snapshot["status"].as_string().in_(("in_review", "approved")),
        )
        .order_by(table.number.desc())
        .limit(1)
    )
    if not fixed or fixed.get("id") != collection_id:
        return []
    current = db.scalar(
        select(DesignCollection).where(
            DesignCollection.project_id == row.project_id,
            DesignCollection.id == UUID(collection_id),
            DesignCollection.kind == "api_set",
        )
    )
    if current is None or current.version == fixed["version"]:
        return []
    path = row.content.get("path", "")
    before = _used_components(fixed["configuration"], operation, path)
    after = _used_components(current.configuration, operation, path)
    changes = []
    for category, name in sorted(before.keys() | after.keys()):
        old, new = before.get((category, name)), after.get((category, name))
        if old == new:
            continue
        label = (
            "参照先が見つかりません"
            if new is None and (category, name) in after
            else "共通定義が変更されました"
        )
        changes.append(
            (
                f"component/{collection_id}/{fixed['version']}/{current.version}/{category}/{name}",
                "error" if new is None and (category, name) in after else "review",
                f"{category} / {name} · {label}"
                f"（仕様セット 版 {fixed['version']} → {current.version}）",
            )
        )
    return changes


def shared_changes(db: Session, row: Specification) -> list[tuple[str, Level, str]]:
    if row.kind == "screen":
        return _part_changes(db, row)
    if row.kind == "api":
        return _api_changes(db, row)
    return []
