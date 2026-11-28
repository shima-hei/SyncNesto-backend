"""設計の版・指摘・項目関連が共有する参照パス。"""

import json
import re
from hashlib import sha256

MISSING = object()
WIREFRAME_AREA = re.compile(
    r"^(content/artboards/[^/]+)/area/([0-9]{1,4})/([0-9]{1,4})/([0-9]{1,4})/([0-9]{1,4})$"
)


def design_value(row, path: str):
    """項目UUIDとエスケープ済みキーを、生の設計値へ解決する。"""
    if path in {"title", "summary"}:
        return getattr(row, path, MISSING)
    parts = path.split("/")
    if parts[0] != "content":
        return MISSING
    value = row.content
    for index, token in enumerate(parts[1:], start=1):
        part = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict):
            if part in value or token in value:
                value = value[part] if part in value else value[token]
            elif parts[index - 1] == "properties":
                value = next(
                    (
                        item
                        for item in value.values()
                        if isinstance(item, dict) and item.get("x-syncnesto-id") == part
                    ),
                    MISSING,
                )
            else:
                return MISSING
        elif isinstance(value, list):
            value = next(
                (
                    item
                    for item in value
                    if isinstance(item, dict)
                    and str(item.get("id", item.get("x-syncnesto-id"))) == part
                ),
                MISSING,
            )
        else:
            return MISSING
        if value is MISSING:
            return MISSING
    return value


def design_field(row, path: str) -> str | None:
    """指摘・項目対応が参照するスカラー値を返す。"""
    value = design_value(row, path)
    if value is MISSING:
        return None
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return None
    return (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, sort_keys=True)
    )


def review_snapshot(row, path: str) -> str | None:
    """アートボード上の領域を検証し、対象版と照合できる指摘値を返す。"""
    area = WIREFRAME_AREA.fullmatch(path)
    if area is None:
        return design_field(row, path)
    board = design_value(row, area[1])
    if not isinstance(board, dict):
        return None
    x, y, width, height = map(int, area.groups()[1:])
    if (
        width < 1
        or height < 1
        or x + width > board.get("width", 1024)
        or y + height > board.get("height", 650)
    ):
        return None
    body = json.dumps(board, ensure_ascii=False, sort_keys=True).encode()
    digest = sha256(body).hexdigest()
    return f"{board.get('name', '')}（X {x}・Y {y}、{width} × {height}）\n{digest}"
