"""Project文書の入力、表示と関連付け契約。"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DocumentCreate(BaseModel):
    """文書の初期本文。"""

    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=100_000)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        """空白だけのタイトルを拒否する。"""
        if not value.strip():
            raise ValueError("タイトルを入力してください")
        return value.strip()


class DocumentUpdate(DocumentCreate):
    """本文全体と読み込んだ版を指定する更新。"""

    version: int = Field(ge=1)


class DocumentRead(BaseModel):
    """現在の本文と更新者。"""

    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    title: str
    body: str
    version: int
    created_by: int
    updated_by: int
    created_at: datetime
    updated_at: datetime


class DocumentListItem(BaseModel):
    """一覧では長い本文を返さない。"""

    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    version: int
    updated_at: datetime


class DocumentList(BaseModel):
    """ページングした文書一覧。"""

    items: list[DocumentListItem]
    total: int
    page: int
    page_size: int


class DocumentRevisionRead(BaseModel):
    """過去版の全文。"""

    model_config = ConfigDict(from_attributes=True)
    id: int
    number: int
    title: str
    body: str
    created_by: int
    created_at: datetime


class DocumentRevisionSummary(BaseModel):
    """版選択用の一覧。本文は個別取得する。"""

    model_config = ConfigDict(from_attributes=True)
    id: int
    number: int
    title: str
    created_at: datetime


class DocumentAttachmentRead(BaseModel):
    """S3 keyと署名URLを一覧レスポンスへ含めない。"""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    filename: str
    content_type: str
    byte_size: int
    created_at: datetime


class DocumentDownload(BaseModel):
    """認可済みの短期ダウンロードURL。"""

    url: str


class DocumentTargetType(StrEnum):
    """関連できる業務資源。"""

    REQUIREMENT = "requirement"
    TASK = "task"
    TEST_DESIGN = "test_design"


class DocumentLinkCreate(BaseModel):
    """対象の型と同一Project内のID。"""

    target_type: DocumentTargetType
    target_id: int = Field(ge=1)


class DocumentLinkTarget(BaseModel):
    """権限を確認して取得した参照先の表示情報。"""

    target_type: DocumentTargetType
    target_id: int
    title: str | None
    requirement_document_id: int | None = None


class DocumentLinkRead(DocumentLinkTarget):
    """登録した関連。削除された対象や閲覧不可対象はtitleがnull。"""

    id: int
