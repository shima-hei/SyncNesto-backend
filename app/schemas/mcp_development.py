"""開発用接続だけで利用する文書の操作。既存28入力とは分離する。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.document import DocumentCreate, DocumentLinkCreate, DocumentUpdate
from app.schemas.mcp_tools import PageInput, ToolInput, WriteInput
from app.schemas.project_objective import ObjectiveFields


class ProjectDocumentListInput(PageInput):
    """本文を返さず、必要な文書だけを検索する。"""

    q: str | None = Field(default=None, max_length=200)


class ProjectDocumentGetInput(ToolInput):
    """一覧で取得した文書のIDを指定する。"""

    document_id: int = Field(gt=0)


class ProjectDocumentContentInput(ProjectDocumentGetInput):
    """本文を上限付きで取得する。offsetはUnicode code point単位。"""

    mode: Literal["outline", "text", "search"] = "outline"
    version: int | None = Field(default=None, ge=1)
    offset: int = Field(default=0, ge=0)
    length: int = Field(default=2000, ge=1, le=8000)
    query: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_mode(self):
        """版をまたいだ分割読込と、無視される引数を拒否する。"""
        if (self.mode != "outline" or self.offset > 0) and self.version is None:
            raise ValueError("本文取得・検索には取得対象のversionを指定してください")
        if self.mode == "search":
            if self.query is None or "length" in self.model_fields_set:
                raise ValueError("検索文字列を指定し、lengthは指定しないでください")
        elif self.query is not None:
            raise ValueError("queryはsearchでのみ指定できます")
        if self.mode == "outline" and "length" in self.model_fields_set:
            raise ValueError("見出し索引ではlengthを指定しないでください")
        return self


class DocumentTextEdit(BaseModel):
    """取得した原文への一つの編集。範囲は元の版に対して指定する。"""

    model_config = ConfigDict(extra="forbid")
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    expected_text: str = Field(max_length=8000)
    replacement: str = Field(max_length=8000)

    @model_validator(mode="after")
    def validate_range(self):
        """空範囲は挿入、非空範囲は期待する原文の長さと一致させる。"""
        if self.end < self.start or self.end - self.start != len(self.expected_text):
            raise ValueError("範囲と期待する原文の文字数が一致しません")
        return self


class ProjectDocumentPatchInput(WriteInput):
    """本文の必要部分だけを送信する。既存の版・履歴・再送防止を維持する。"""

    document_id: int = Field(gt=0)
    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    edits: list[DocumentTextEdit] = Field(default_factory=list, max_length=20)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value):
        if value is not None and not value.strip():
            raise ValueError("タイトルを入力してください")
        return value.strip() if value is not None else value

    @model_validator(mode="after")
    def validate_patch(self):
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("タイトルは省略か文字列で指定してください")
        if not self.edits and self.title is None:
            raise ValueError("更新する箇所を指定してください")
        if sum(len(e.expected_text) + len(e.replacement) for e in self.edits) > 16000:
            raise ValueError("部分更新の文字数上限を超えています")
        return self


class ProjectDocumentCreateInput(WriteInput):
    """一般文書を作成し、テスト設計とは区別する。"""

    data: DocumentCreate


class ProjectDocumentUpdateInput(WriteInput):
    """現在のversionと本文全体で文書を更新する。"""

    document_id: int = Field(gt=0)
    data: DocumentUpdate


class ProjectDocumentLinkInput(WriteInput):
    """文書を同じProjectの要件・作業・テスト設計へ関連付ける。"""

    document_id: int = Field(gt=0)
    data: DocumentLinkCreate


class DevelopmentObjectiveUpdate(ObjectiveFields):
    """省略は維持、明示nullは解除。purposeや承認状態は受け付けない。"""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def require_change(self) -> "DevelopmentObjectiveUpdate":
        """空の変更を誤って更新扱いにしない。"""
        if not self.model_fields_set:
            raise ValueError("更新する目標項目を指定してください")
        return self


class RequirementObjectivesInput(WriteInput):
    """要件定義書の専用3項目だけを、版を確認して更新する。"""

    document_id: int = Field(gt=0)
    version: int = Field(ge=1)
    data: DevelopmentObjectiveUpdate
