from pydantic import BaseModel, EmailStr


class UserBase(BaseModel):
    email: EmailStr
    name: str


class UserCreate(UserBase):
    pass


class UserRead(UserBase):
    id: int

    model_config = {"from_attributes": True}
