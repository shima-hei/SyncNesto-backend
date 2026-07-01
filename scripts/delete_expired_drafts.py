"""期限切れ下書きを削除するスクリプト。"""

from app.db.session import SessionLocal
from app.services.draft import DraftService


def main() -> None:
    """期限切れ下書きを削除して件数を出力する。"""
    with SessionLocal() as db:
        deleted_count = DraftService().delete_expired_drafts(db)
    print(f"deleted_expired_drafts={deleted_count}")


if __name__ == "__main__":
    main()
