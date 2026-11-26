"""閲覧できる成果物を共通の参照情報にする。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User, UserType
from app.presenters.user import build_comment_author_avatar_url
from app.repositories.search import SearchRepository
from app.schemas.smart_link import (
    SmartLinkGroup,
    SmartLinkPerson,
    SmartLinkRead,
    SmartLinkResolve,
)
from app.services.storage import StorageService


class SmartLinkService:
    def __init__(self) -> None:
        self.repository = SearchRepository()

    def find(
        self,
        db: Session,
        user: User,
        *,
        q: str,
        group: SmartLinkGroup | None,
        preferred_project: int | None,
    ) -> list[SmartLinkRead]:
        if user.user_type != UserType.INTERNAL:
            return []
        rows = self.repository.find_links(
            db,
            tenant_id=db.info["tenant_id"],
            user_id=user.id,
            q=q,
            group=group,
            preferred_project=preferred_project,
        )
        return [SmartLinkRead.model_validate(row) for row in rows]

    def resolve(
        self, db: Session, user: User, request: SmartLinkResolve
    ) -> list[SmartLinkRead]:
        if user.user_type != UserType.INTERNAL:
            return []
        rows = self.repository.resolve_links(
            db,
            tenant_id=db.info["tenant_id"],
            user_id=user.id,
            references=request.references,
            preview=request.preview,
        )
        if not request.preview:
            return [SmartLinkRead.model_validate(row) for row in rows]

        ids = {row["assignee_id"] for row in rows if row["assignee_id"] is not None}
        people = {}
        if ids:
            storage = StorageService().for_demo(db)
            people = {
                person.id: SmartLinkPerson(
                    name=person.name,
                    avatar_url=build_comment_author_avatar_url(person, storage),
                )
                for person in db.scalars(select(User).where(User.id.in_(ids)))
            }
        return [
            SmartLinkRead.model_validate(
                {**row, "preview": {**row, "assignee": people.get(row["assignee_id"])}}
            )
            for row in rows
        ]
