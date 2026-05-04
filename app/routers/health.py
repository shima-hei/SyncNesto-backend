"""ヘルスチェックAPIのルーティングを定義するモジュール。"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/")
async def read_root() -> dict[str, str]:
    """ヘルスチェック結果を返す。

    Returns:
        ヘルスチェック結果。
    """
    return {"message": "health check OK"}
