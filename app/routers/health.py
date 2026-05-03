from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/")
async def read_root() -> dict[str, str]:
    return {"message": "health check OK"}
