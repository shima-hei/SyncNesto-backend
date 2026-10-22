"""開発用接続だけで利用する文書の操作。既存28入力とは分離する。"""

from pydantic import ConfigDict, Field, model_validator

from app.schemas.document import DocumentCreate, DocumentLinkCreate, DocumentUpdate
from app.schemas.mcp_tools import PageInput, ToolInput, WriteInput
from app.schemas.project_objective import ObjectiveFields


class ProjectDocumentListInput(PageInput):
    """本文を返さず、必要な文書だけを検索する。"""

    q: str | None = Field(default=None, max_length=200)


class ProjectDocumentGetInput(ToolInput):
    """一覧で取得した文書のIDを指定する。"""

    document_id: int = Field(gt=0)


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
