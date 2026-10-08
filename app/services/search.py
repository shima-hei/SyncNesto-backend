"""検証済みの組織Contextから検索結果を構築する。"""

from sqlalchemy.orm import Session

from app.models.user import User
from app.repositories.search import SearchRepository
from app.schemas.search import SearchCategory, SearchProjectsRead, SearchRead


class SearchService:
    """既存の認証・所属とDB検索を接続する読み取り専用Service。"""

    def __init__(self) -> None:
        """専用Repositoryを用意する。"""
        self.repository = SearchRepository()

    def search(
        self,
        db: Session,
        user: User,
        *,
        q: str,
        category: SearchCategory | None,
        project_id: int | None,
        page: int,
        page_size: int,
    ) -> SearchRead:
        """現在組織の権限内に限った結果と件数を返す。"""
        counts, total, items = self.repository.search(
            db,
            tenant_id=db.info["tenant_id"],
            user_id=user.id,
            q=q,
            category=category,
            project_id=project_id,
            page=page,
            page_size=page_size,
        )
        return SearchRead.model_validate(
            dict(
                items=items,
                total=total,
                counts=counts,
                page=page,
                page_size=page_size,
            )
        )

    def projects(
        self,
        db: Session,
        user: User,
        *,
        q: str,
        page: int,
        page_size: int,
        selected_id: int | None,
    ) -> SearchProjectsRead:
        """候補に出す名前にも検索と同じ所属・権限条件を適用する。"""
        total, items, selected = self.repository.projects(
            db,
            tenant_id=db.info["tenant_id"],
            user_id=user.id,
            q=q,
            page=page,
            page_size=page_size,
            selected_id=selected_id,
        )
        return SearchProjectsRead.model_validate(
            dict(
                total=total,
                items=items,
                selected=selected,
            )
        )
