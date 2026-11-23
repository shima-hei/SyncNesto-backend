"""標準タスク種別の表示名と自動採番の接頭辞。"""

DEFAULT_TASK_CODE_PREFIX = "TASK"
_STANDARD_TASK_TYPE_DEFINITIONS = {
    "other": ("タスク", DEFAULT_TASK_CODE_PREFIX),
    "bug": ("不具合", "ISSUE"),
    "frontend": ("フロントエンド", "FE"),
    "backend": ("バックエンド", "BE"),
    "database": ("データベース", "DB"),
    "infrastructure": ("インフラ", "INFRA"),
    "security": ("セキュリティ", "SEC"),
    "test": ("テスト", "TEST"),
    "review": ("レビュー", "REVIEW"),
    "investigation": ("調査", "INV"),
    "documentation": ("ドキュメント", "DOC"),
}

STANDARD_TASK_TYPES = {
    key: name for key, (name, _) in _STANDARD_TASK_TYPE_DEFINITIONS.items()
}
TASK_CODE_PREFIX_BY_TYPE = {
    key: prefix for key, (_, prefix) in _STANDARD_TASK_TYPE_DEFINITIONS.items()
}
