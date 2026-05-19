"""SQLAlchemyモデルのDBコメント文字列を定義するモジュール。"""


def db_comment(logical_name: str, description: str) -> str:
    """DBコメント文字列を作成する。

    Args:
        logical_name: 日本語の論理名。
        description: 説明。

    Returns:
        DBに設定するコメント文字列。
    """
    return f"{logical_name}: {description}"
