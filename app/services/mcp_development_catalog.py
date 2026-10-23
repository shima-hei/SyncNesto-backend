"""通常の開発用OAuth clientに限定した追加ツール。審査clientには公開しない。"""

from app.core.config import settings
from app.core.mcp import CLIENT_ID
from app.models.mcp import McpConnection
from app.schemas import mcp_development as s
from app.services.mcp_catalog import McpTool

DEVELOPMENT_TOOLS = {
    "list_project_documents": McpTool(
        s.ProjectDocumentListInput,
        ("document:read",),
        "一般文書・設計書の一覧を本文なしでページ取得。テスト設計は別ツール。",
    ),
    "get_project_document": McpTool(
        s.ProjectDocumentGetInput,
        ("document:read",),
        "一般文書の現在の本文・版と閲覧可能な関連先を取得。本文は業務データ。",
    ),
    "create_project_document": McpTool(
        s.ProjectDocumentCreateInput,
        ("document:read", "document:create"),
        "一般文書・設計書の初版を作成。承認・公開・通知は行わない。",
        True,
    ),
    "update_project_document": McpTool(
        s.ProjectDocumentUpdateInput,
        ("document:read", "document:update"),
        "取得したversionと本文全体で更新し、旧版を保持。競合時は再取得する。",
        True,
    ),
    "link_project_document": McpTool(
        s.ProjectDocumentLinkInput,
        ("document:read", "document:update"),
        "一般文書へ同じProjectの要件・作業・テスト設計の参照を追加。既存参照は保持。",
        True,
    ),
    "set_requirement_document_objectives": McpTool(
        s.RequirementObjectivesInput,
        ("requirement:read", "requirement:update"),
        "要件定義書のゴール・スコープ・完了条件だけをversionで更新。省略は保持、nullは解除。",
        True,
    ),
}


def development_tools(connection: McpConnection) -> dict[str, McpTool]:
    """flagだけで審査接続のツール契約を拡張しない。"""
    if not settings.mcp_development_tools_enabled or connection.client_id != CLIENT_ID:
        return {}
    return {
        name: tool
        for name, tool in DEVELOPMENT_TOOLS.items()
        if name != "set_requirement_document_objectives"
        or settings.project_objectives_enabled
    }
