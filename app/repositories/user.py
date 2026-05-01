from sqlalchemy.orm import Session

from app.models.user import User
from app.schemas.user import UserCreate


class UserRepository:
    def create(self, db: Session, user_in: UserCreate) -> User:
        user = User(email=user_in.email, name=user_in.name)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
