"""プロジェクトschemaを定義するモジュール。"""

from datetime import date, datetime
from typing import Literal, get_args

from pydantic import BaseModel, Field, model_validator

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


ProjectNavigationKey = Literal[
    "overview", "requirements", "design", "tasks", "planning", "tests", "documents"
]


class ProjectNavigationItem(BaseModel):
    """案件内メニューの順序と表示位置。"""

    model_config = {"extra": "forbid"}

    key: ProjectNavigationKey
    placement: Literal["primary", "overflow", "hidden"]


class ProjectNavigation(BaseModel):
    """案件共通のナビゲーション設定。"""

    model_config = {"extra": "forbid"}

    items: list[ProjectNavigationItem] = Field(min_length=7, max_length=7)
    requirements_view: Literal["requirements", "fixed-approvals"] = "requirements"
    tests_view: Literal["test-designs", "test-cases"] = "test-designs"

    @model_validator(mode="after")
    def validate_items(self):
        """全項目を一度ずつ指定し、概要を先頭に固定する。"""
        if {item.key for item in self.items} != set(get_args(ProjectNavigationKey)):
            raise ValueError("全メニューを1回ずつ指定してください。")
        first = self.items[0]
        if first.key != "overview" or first.placement != "primary":
            raise ValueError("概要は先頭に表示してください。")
        return self


class ProjectUpdate(BaseModel):
    """プロジェクト更新リクエストで受け取るschema。"""

    version: int
    navigation: ProjectNavigation | None = None
    requirement_review_required: bool | None = None
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
    navigation: ProjectNavigation | None = None
    requirement_review_required: bool = True
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


class CurrentProjectRoleRead(BaseModel):
    """現在のログインユーザーのプロジェクトロール読み取りschema。"""

    project_id: int
    role: RoleRead | None
    is_system_admin: bool
