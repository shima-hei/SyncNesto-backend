"""プロジェクトschemaを定義するモジュール。"""

from datetime import date, datetime

from pydantic import BaseModel

from app.schemas.user import RoleRead


class ProjectBase(BaseModel):
    """プロジェクトschemaの共通フィールドを定義する基底schema。"""

    project_code: str
    name: str
    description: str | None = None
    status: str = "active"
    start_date: date | None = None
    end_date: date | None = None


class ProjectCreate(ProjectBase):
    """プロジェクト作成リクエストで受け取るschema。"""


class ProjectUpdate(BaseModel):
    """プロジェクト更新リクエストで受け取るschema。"""

    version: int
    project_code: str | None = None
    name: str | None = None
    description: str | None = None
    status: str | None = None
    start_date: date | None = None
    end_date: date | None = None


class ProjectRead(ProjectBase):
    """プロジェクト読み取り時に返すschema。"""

    id: int
    version: int
    created_by: int | None = None
    updated_by: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectListItem(ProjectBase):
    """プロジェクト一覧で返す軽量schema。"""

    id: int
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectListResponse(BaseModel):
    """プロジェクト一覧レスポンスschema。"""

    items: list[ProjectListItem]
    total: int
    page: int
    page_size: int


class ProjectMemberCreate(BaseModel):
    """プロジェクトメンバー追加リクエストで受け取るschema。"""

    user_id: int
    role_key: str


class ProjectMemberUpdate(BaseModel):
    """プロジェクトメンバー更新リクエストで受け取るschema。"""

    version: int
    role_key: str


class ProjectMemberRead(BaseModel):
    """プロジェクトメンバー読み取り時に返すschema。"""

    id: int
    project_id: int
    user_id: int
    role: RoleRead
    version: int
