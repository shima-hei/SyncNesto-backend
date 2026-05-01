from sqlalchemy.orm import Session

from app.repositories.user import UserRepository
from app.schemas.user import UserCreate


class UserService:
    def __init__(self, repository: UserRepository | None = None) -> None:
        self.repository = repository or UserRepository()

    def create_user(self, db: Session, user_in: UserCreate):
        return self.repository.create(db, user_in)
