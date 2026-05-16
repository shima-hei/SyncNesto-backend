"""プロジェクトschemaを定義するモジュール。"""

from pydantic import BaseModel


class ProjectBase(BaseModel):
    """プロジェクトschemaの共通フィールドを定義する基底schema。"""

    name: str
    description: str | None = None


class ProjectCreate(ProjectBase):
    """プロジェクト作成リクエストで受け取るschema。"""


class ProjectUpdate(BaseModel):
    """プロジェクト更新リクエストで受け取るschema。"""

    version: int
    name: str | None = None
    description: str | None = None


class ProjectRead(ProjectBase):
    """プロジェクト読み取り時に返すschema。"""

    id: int
    version: int

    model_config = {"from_attributes": True}


class ProjectMemberCreate(BaseModel):
    """プロジェクトメンバー追加リクエストで受け取るschema。"""

    user_id: int
    role_id: int


class ProjectMemberUpdate(BaseModel):
    """プロジェクトメンバー更新リクエストで受け取るschema。"""

    version: int
    role_id: int


class ProjectMemberRead(BaseModel):
    """プロジェクトメンバー読み取り時に返すschema。"""

    id: int
    project_id: int
    user_id: int
    role_id: int
    version: int

    model_config = {"from_attributes": True}
