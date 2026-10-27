"""提出・再確認・保全の契約。クライアントから保存キーは受け取らない。"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.requirement_verification import StrictInput


class EvidenceFile(StrictInput):
    kind: Literal["test_evidence", "document_attachment"]
    id: UUID


class EvidenceEntry(StrictInput):
    id: UUID
    row_id: UUID
    kind: Literal["execution", "manual", "not_applicable"]
    execution_id: UUID | None = None
    use: Literal["current_candidate", "historical_record"] = "current_candidate"
    reconfirmed: bool = False
    reason: str = Field(default="", max_length=4000)
    checked_content: str = Field(default="", max_length=4000)
    actual_result: str = Field(default="", max_length=8000)
    result: Literal["passed", "failed", "blocked", "not_applicable"] | None = None
    reported_at: datetime | None = None
    files: list[EvidenceFile] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def meaningful(self):
        if not self.reason.strip():
            raise ValueError("対応の再確認理由・根拠を入力してください")
        if self.kind == "execution":
            if any(f.kind != "test_evidence" for f in self.files):
                raise ValueError("実行にはその実行のエビデンスを選んでください")
            if not self.execution_id or not self.reconfirmed:
                raise ValueError("実行と固定要件との対応を今確認してください")
            if (
                self.result
                or self.reported_at
                or self.checked_content
                or self.actual_result
            ):
                raise ValueError("元の実行結果・実施者は変更できません")
        else:
            if self.execution_id or self.use != "current_candidate" or self.reconfirmed:
                raise ValueError("実行の指定はテスト証拠だけで利用できます")
            if self.kind == "manual" and (
                not self.checked_content.strip()
                or not self.actual_result.strip()
                or self.result not in {"passed", "failed", "blocked"}
                or self.reported_at is None
            ):
                raise ValueError("実際の確認内容・結果・状態・申告日時が必要です")
            if self.kind == "not_applicable" and (
                self.result not in {None, "not_applicable"}
                or self.checked_content
                or self.actual_result
            ):
                raise ValueError("対象外を合格に読み替えられません")
            if any(f.kind != "document_attachment" for f in self.files):
                raise ValueError("手動確認・対象外には文書添付を選んでください")
        if self.reported_at is not None and self.reported_at.utcoffset() is None:
            raise ValueError("確認日時にはタイムゾーンが必要です")
        return self


class EvidenceSelection(StrictInput):
    plan_version: int = Field(gt=0)
    entries: list[EvidenceEntry] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique(self):
        if len({x.id for x in self.entries}) != len(self.entries):
            raise ValueError("記録IDが重複しています")
        if len({(x.row_id, x.kind, x.execution_id) for x in self.entries}) != len(
            self.entries
        ):
            raise ValueError("同じ確認行・実行の記録が重複しています")
        if len({(f.kind, f.id) for x in self.entries for f in x.files}) > 20:
            raise ValueError("ファイルは重複を除き20件までです")
        return self


class EvidenceCreate(EvidenceSelection):
    request_id: UUID
    expected_preview_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class EvidenceVersion(StrictInput):
    version: int = Field(gt=0)


class EvidencePreview(StrictInput):
    preview_digest: str
    plan_version: int
    subject_digest: str
    checklist_digest: str
    entries: list[dict[str, Any]]
    files: list[dict[str, Any]]
    total_bytes: int
    warnings: list[str]


class EvidenceSummary(StrictInput):
    id: UUID
    plan_version: int
    status: Literal["preparing", "ready", "failed", "cancelled"]
    version: int
    actor_name: str
    created_at: datetime
    completed_at: datetime | None
    entry_count: int
    file_count: int
    verified_files: int
    safe_error: str | None = None
    can_operate: bool = False


class EvidencePage(StrictInput):
    enabled: bool
    state: Literal["ready", "disabled", "restricted"]
    can_submit: bool = False
    document_access_limited: bool = False
    items: list[EvidenceSummary] = Field(default_factory=list)
    next_cursor: str | None = None


class EvidenceDetail(EvidenceSummary):
    subject_digest: str
    checklist_digest: str
    manifest_digest: str | None
    comparison: dict[str, Any]
    entries: list[dict[str, Any]]
    files: list[dict[str, Any]]
    next_cursor: str | None = None


class EvidenceEntryDetail(StrictInput):
    id: UUID
    plan_version: int
    actor_name: str
    received_at: datetime
    entry: dict[str, Any]
    comparison: dict[str, Any]


class EvidenceDownload(StrictInput):
    url: str
    expires_at: datetime
    expires_in: int
