"""権限内の業務データを横断する検索契約。"""

from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints

SearchQuery = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
SearchCategory = Literal["requirement", "task", "test", "document"]
SearchKind = Literal[
    "requirement_document",
    "requirement",
    "task",
    "test_design",
    "test_item",
    "test_case",
    "document",
]


class SearchItem(BaseModel):
    """本文を全件転送せず、識別情報と短い一致箇所を返す。"""

    kind: SearchKind
    category: SearchCategory
    id: str
    project_id: int
    project_name: str
    project_code: str
    container_id: int | None
    title: str
    code: str
    excerpt: str


class SearchCounts(BaseModel):
    """種類の絞り込み前の、閲覧可能な検索結果件数。"""

    requirement: int = 0
    task: int = 0
    test: int = 0
    document: int = 0


class SearchRead(BaseModel):
    """現在のページと、同じ権限条件から求めた件数。"""

    items: list[SearchItem]
    total: int
    counts: SearchCounts
    page: int
    page_size: int


class SearchProject(BaseModel):
    """検索対象にできる参加プロジェクトの候補。"""

    id: int
    name: str
    project_code: str


class SearchProjectsRead(BaseModel):
    """検索できる案件候補をページ単位で取得する。"""

    items: list[SearchProject]
    total: int
    selected: SearchProject | None
