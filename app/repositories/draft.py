"""下書きRepositoryを定義するモジュール。"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.models.draft import Draft


class DraftRepository:
    """Draftテーブルへのデータアクセス処理を提供する。"""

    def get_by_id(self, db: Session, draft_id: int) -> Draft | None:
        """idに一致する下書きを取得する。"""
        return db.query(Draft).filter(Draft.id == draft_id).first()

    def get_by_owner_scope(
        self,
        db: Session,
        *,
        owner_user_id: int,
        scope_key: str,
    ) -> Draft | None:
        """所有者とscope_keyに一致する下書きを取得する。"""
        return (
            db.query(Draft)
            .filter(
                Draft.owner_user_id == owner_user_id,
                Draft.scope_key == scope_key,
            )
            .first()
        )

    def list_by_owner(
        self,
        db: Session,
        *,
        owner_user_id: int,
        resource_type: str | None = None,
        project_id: int | None = None,
    ) -> list[Draft]:
        """所有者の下書き一覧を取得する。"""
        query = db.query(Draft).filter(Draft.owner_user_id == owner_user_id)
        if resource_type is not None:
            query = query.filter(Draft.resource_type == resource_type)
        if project_id is not None:
            query = query.filter(Draft.project_id == project_id)
        return query.order_by(Draft.updated_at.desc(), Draft.id.desc()).all()

    def delete_expired(self, db: Session, *, now: datetime) -> int:
        """期限切れ下書きを削除する。"""
        deleted_count = db.query(Draft).filter(Draft.expires_at <= now).delete(
            synchronize_session=False,
        )
        db.commit()
        return deleted_count

    def create(self, db: Session, *, draft: Draft) -> Draft:
        """下書きを作成する。"""
        db.add(draft)
        db.commit()
        db.refresh(draft)
        return draft

    def delete(self, db: Session, *, draft: Draft) -> None:
        """下書きを削除する。"""
        db.delete(draft)
        db.commit()
