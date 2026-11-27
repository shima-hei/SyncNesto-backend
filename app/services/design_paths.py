"""設計の版・指摘・項目関連が共有する参照パス。"""

import json


def design_field(row, path: str) -> str | None:
    """項目UUIDを使ったパスを、現在の表示値へ解決する。"""
    if path in {"title", "summary"}:
        return getattr(row, path)
    parts = path.split("/")
    if parts[0] != "content":
        return None
    value = row.content
    for part in parts[1:]:
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list):
            value = next(
                (
                    item
                    for item in value
                    if isinstance(item, dict)
                    and str(item.get("id", item.get("x-syncnesto-id"))) == part
                ),
                None,
            )
            if value is None:
                return None
        else:
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
