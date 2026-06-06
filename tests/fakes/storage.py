"""StorageServiceのテスト用fake。"""


class FakeStorageService:
    """テスト用StorageService。"""

    def __init__(self) -> None:
        """FakeStorageServiceを初期化する。"""
        self.deleted_keys: list[str] = []

    def upload_user_avatar(
        self,
        *,
        user_id: int,
        content: bytes,
        content_type: str | None,
    ) -> str:
        """固定のavatar keyを返す。"""
        return f"users/{user_id}.png"

    def generate_presigned_url(self, avatar_key: str | None) -> str | None:
        """固定の署名付きURLを返す。"""
        if avatar_key is None:
            return None

        return f"https://example.com/{avatar_key}?signature=test"

    def delete_object(self, key: str) -> None:
        """削除対象keyを保持する。"""
        self.deleted_keys.append(key)
