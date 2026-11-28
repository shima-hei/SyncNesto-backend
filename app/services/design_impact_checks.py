"""項目対応の差分から、判定できる互換性と確認が必要な変更を求める。"""

from typing import Literal

from app.services.design_paths import design_field, design_value

Level = Literal["error", "warning", "review"]
TYPE_FAMILIES = {
    **dict.fromkeys(
        (
            "text",
            "varchar",
            "char",
            "uuid",
            "date",
            "timestamp",
            "timestamptz",
            "inet",
            "string",
        ),
        "string",
    ),
    **dict.fromkeys(("smallint", "integer", "bigint"), "integer"),
    **dict.fromkeys(("real", "double precision", "numeric", "number"), "number"),
    **dict.fromkeys(("boolean", "object", "array", "null"), None),
}
CONTRACT_LABELS = {
    "type": "型",
    "required": "必須条件",
    "nullable": "NULL許容",
    "maxLength": "最大文字数",
    "minLength": "最小文字数",
    "maximum": "最大値",
    "minimum": "最小値",
    "maxItems": "最大件数",
    "minItems": "最小件数",
    "pattern": "パターン",
    "validation": "検証条件",
    "constraints": "入力制約",
    "enum": "許容値",
    "format": "形式",
    "precision": "精度",
    "scale": "スケール",
}


def _contract(row, path: str) -> dict:
    parent, _, _ = path.rpartition("/")
    raw = design_value(row, parent)
    if not isinstance(raw, dict):
        return {}
    values = {key: raw[key] for key in CONTRACT_LABELS if key in raw}
    if "data_type" in raw:
        values["type"] = raw["data_type"]
    if raw.get("length") is not None:
        values["maxLength"] = raw["length"]
    if "/properties/" in parent:
        owner, prop = parent.rsplit("/properties/", 1)
        schema = design_value(row, owner)
        if isinstance(schema, dict):
            properties = schema.get("properties", {})
            name = (
                next((key for key, value in properties.items() if value is raw), None)
                if isinstance(properties, dict)
                else None
            )
            if name is not None and "/" not in prop:
                required = schema.get("required", [])
                if isinstance(required, list):
                    values["required"] = name in required
    return values


def _types(value) -> set[str]:
    values = value if isinstance(value, list) else [value]
    return {
        TYPE_FAMILIES[item] or item
        for item in values
        if isinstance(item, str) and item in TYPE_FAMILIES and item != "null"
    }


def _incompatibility(source: dict, target: dict) -> str | None:
    source_types, target_types = _types(source.get("type")), _types(target.get("type"))
    if "number" in target_types:
        target_types.add("integer")
    if source_types and target_types and not source_types <= target_types:
        return "対応先が受け付ける型と一致しません"
    if source.get("required") is False and target.get("required") is True:
        return "省略可能な入力が必須の対応先に渡ります"
    if source.get("nullable") is True and target.get("nullable") is False:
        return "NULLを許容する値がNULL不可の対応先に渡ります"
    for key in ("maxLength", "maxItems", "maximum", "minLength", "minItems", "minimum"):
        start, end = source.get(key), target.get(key)
        if isinstance(start, (int, float)) and isinstance(end, (int, float)):
            if (key.startswith("max") and start > end) or (
                key.startswith("min") and start < end
            ):
                return f"対応先の{CONTRACT_LABELS[key]}の範囲に収まりません"
    return None


def mapping_change(
    before, current, other, mapping: dict, *, reverse: bool
) -> tuple[Level, str] | None:
    path = mapping["target_path" if reverse else "source_path"]
    counterpart = mapping["source_path" if reverse else "target_path"]
    old_value, new_value = design_field(before, path), design_field(current, path)
    old_contract, new_contract = _contract(before, path), _contract(current, path)
    altered = [
        label
        for key, label in CONTRACT_LABELS.items()
        if old_contract.get(key) != new_contract.get(key)
    ]
    if old_value == new_value and not altered:
        return None
    label = mapping["name"] or path
    detail = (
        f"{label}の{'・'.join(altered)}が変更されました"
        if old_value == new_value
        else f"{label}: {old_value or 'なし'} → {new_value or 'なし'}"
    )
    if new_value is None or design_field(other, counterpart) is None:
        return "error", "対応する項目が見つかりません。" + detail
    if altered and mapping.get("meaning", "maps") == "maps":
        if mapping.get("description"):
            return "review", "変換・補足仕様を確認してください。" + detail
        other_contract = _contract(other, counterpart)
        source, target = (
            (other_contract, new_contract)
            if reverse
            else (new_contract, other_contract)
        )
        incompatible = _incompatibility(source, target)
        if incompatible:
            return "error", incompatible + "。" + detail
    return "warning", detail
