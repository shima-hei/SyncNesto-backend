"""API実装へ依存せず、追加読取ツールの範囲を共有する。"""

READ_CONTEXT_TOOLS = frozenset(
    {"search_project_items", "get_requirement_context", "get_project_brief"}
)
