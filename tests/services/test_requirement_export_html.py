"""HTMLの構造・外部資源・旧出力の互換契約を検証する。"""

import hashlib
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from typing import cast
from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.requirement import (
    Requirement,
    RequirementChangeLog,
    RequirementDocument,
    RequirementOpenIssue,
    RequirementSection,
    RequirementTargetComment,
)
from app.schemas.requirement import RequirementDocumentExportCreate
from app.services.requirement_export import RequirementExportService

pytestmark = pytest.mark.no_db


class HtmlContent(HTMLParser):
    """実際のタグ/属性とREQ区間内の文字列を読む。"""

    def __init__(self, content: str):
        super().__init__()
        self.tags: list[str] = []
        self.attributes: list[tuple[str, str | None]] = []
        self.text: list[str] = []
        self.requirements: dict[int, list[str]] = {}
        self.current: int | None = None
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes.extend(attrs)
        if tag == "section":
            identifier = dict(attrs)["data-requirement-id"]
            assert identifier is not None
            self.current = int(identifier)
            self.requirements[self.current] = []

    def handle_endtag(self, tag):
        if tag == "section":
            self.current = None

    def handle_data(self, data):
        self.text.append(data)
        if self.current is not None:
            self.requirements[self.current].append(data)


def make_service(monkeypatch, service_class=RequirementExportService):
    """DBなしで同一入力の旧/新出力を比較するための保存済みデータを作る。"""
    document = RequirementDocument(
        id=1,
        project_id=7,
        document_code="RD-HTML",
        version=3,
        title="要件<冊> **タイトル**",
        status="draft",
        purpose="目的\n### 属性の文字",
        project_goal="案件ゴール",
        project_scope="案件スコープ",
        project_completion_criteria="完了条件",
        client_name="顧客",
        vendor_name="担当",
        approved_at=datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc),
    )
    sections = [
        RequirementSection(
            id=10,
            document_id=1,
            title="章A",
            section_type="business",
            status="draft",
            sort_order=10,
            content="章の**強調**",
        ),
        RequirementSection(
            id=20,
            document_id=1,
            title="章B",
            section_type="system",
            status="draft",
            sort_order=20,
            content="別の章",
        ),
    ]
    requirements = [
        Requirement(
            id=2,
            document_id=1,
            section_id=20,
            requirement_code="REQ-020",
            title="対象外の要件",
            requirement_type="functional",
            priority="medium",
            status="draft",
            description="別の本文",
        ),
        Requirement(
            id=3,
            document_id=1,
            section_id=10,
            requirement_code="REQ-010",
            title="表のある要件",
            requirement_type="functional",
            priority="high",
            status="draft",
            category="分類",
            owner_id=1,
            description="#### 本文内の見出し\n**太字** と *斜体*\n次の行\n\n"
            "| 項目 | 説明 |\n| --- | --- |\n| **値** | `a\\|b` |\n\n"
            "```text\n**コード内は記号**\n```\n",
            rationale="理由の**強調**",
            acceptance_criteria="確認条件",
        ),
        Requirement(
            id=4,
            document_id=1,
            section_id=10,
            requirement_code="REQ-005",
            title="先に掲載する要件",
            requirement_type="business",
            priority="low",
            status="draft",
            description="先の本文",
        ),
        Requirement(
            id=5,
            document_id=1,
            section_id=None,
            requirement_code="REQ-999",
            title="未所属",
            requirement_type="business",
            priority="low",
            status="draft",
            description="未所属本文",
        ),
    ]
    issues = [
        RequirementOpenIssue(
            id=8,
            document_id=1,
            issue_code="ISSUE-1",
            title="未決事項",
            status="open",
            related_requirement_id=3,
            assignee_id=1,
            due_date=date(2026, 2, 3),
            impact_scope="影響",
            description="課題本文",
            resolution="解決本文",
        )
    ]
    comments = [
        RequirementTargetComment(
            id=11,
            document_id=1,
            author_id=1,
            target_type="section",
            target_id=10,
            body="選択章のコメント",
            is_resolved=False,
        ),
        RequirementTargetComment(
            id=12,
            document_id=1,
            author_id=1,
            target_type="requirement",
            target_id=2,
            body="対象外コメント",
            is_resolved=True,
        ),
    ]
    changes = [
        RequirementChangeLog(
            id=13,
            document_id=1,
            target_type="document",
            target_id=1,
            action="created",
            changed_by=1,
            changed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    ]
    service = service_class()
    monkeypatch.setattr(service.document_repository, "get_by_id", lambda *_: document)
    for repo, rows in [
        (service.section_repository, sections),
        (service.requirement_repository, requirements),
        (service.open_issue_repository, issues),
        (service.target_comment_repository, comments),
        (service.change_log_repository, changes),
    ]:
        monkeypatch.setattr(repo, "list_by_document", lambda *_, values=rows: values)
    record = Mock()
    monkeypatch.setattr(service.change_log_service, "record", record)
    return service, document, requirements, record


def export(service, export_format="html", **options):
    """通常の出力サービス経路を使う。"""
    return service.export_document(
        cast(Session, None),
        project_id=7,
        document_id=1,
        actor_id=1,
        export_in=RequirementDocumentExportCreate(format=export_format, **options),
    ).content


# 実装前の6f4b7f0から、上の同一入力/同一選択条件で取得した値。
LEGACY_SHA256 = {
    "markdown": "d30597f5dec8791f47a1662886730129a360951c88a42a1c5dadf54277287f9d",
    "html": "c553b0f560b2de5193c28b7f1778e0da5d58b25b4a5f2d6b040ee14e21ba9453",
    "pdf": "3d0810b54bdb2f7ce2c95b6bef7df94e7ea77c0f61508e4adbc624e9d00a8a9c",
}


@pytest.mark.parametrize(
    "export_format,enabled",
    [
        ("markdown", False),
        ("markdown", True),
        ("html", False),
        ("pdf", False),
        ("pdf", True),
    ],
)
def test_legacy_bytes_and_html_only_flag(monkeypatch, export_format, enabled):
    """flagがMarkdown/PDFに影響せず、OFFのHTMLも旧出力のままとなる。"""
    monkeypatch.setattr(settings, "requirement_rich_html_enabled", enabled)
    service, _, _, _ = make_service(monkeypatch)
    content = export(
        service, export_format, include_comments=True, include_change_logs=True
    )
    assert hashlib.sha256(content.encode()).hexdigest() == LEGACY_SHA256[export_format]


def test_rich_markup_keeps_structural_boundaries_and_metadata(monkeypatch):
    """本文のh4はREQを分割せず、属性・順番・理由・未所属を残す。"""
    monkeypatch.setattr(settings, "requirement_rich_html_enabled", True)
    service, _, _, record = make_service(monkeypatch)
    content = export(service, include_comments=True, include_change_logs=True)
    parsed = HtmlContent(content)
    assert all(tag in parsed.tags for tag in ["table", "strong", "em", "code"])
    assert list(parsed.requirements) == [4, 3, 2, 5]
    assert "本文内の見出し" in "".join(parsed.requirements[3])
    assert "**コード内は記号**" in "".join(parsed.requirements[3])
    assert "a|b" in "".join(parsed.requirements[3])
    visible = "".join(parsed.text)
    for value in [
        "要件<冊> **タイトル**",
        "### 属性の文字",
        "案件ゴール",
        "分類",
        "理由の",
        "確認条件",
        "未所属本文",
        "課題本文",
        "解決本文",
        "選択章のコメント",
        "対象外コメント",
        "created document:1",
    ]:
        assert value in visible
    assert "break-inside:avoid-page" in content and "@page{size:A4" in content
    record.assert_called_once()


def test_raw_html_images_mermaid_and_footnotes_are_visible_without_resources(
    monkeypatch,
):
    """未対応構文を落とさず、画像/HTMLで外部・ローカル資源を読み込まない。"""
    monkeypatch.setattr(settings, "requirement_rich_html_enabled", True)
    service, _, items, _ = make_service(monkeypatch)
    items[1].description = (
        '<script>alert(1)</script>\n<iframe src="https://example.test"></iframe>\n\n'
        '![代替文](https://example.test/image.png "画像名")\n\n'
        "![ローカル](file:///etc/passwd)\n\n"
        "![埋込](data:image/png;base64,AAAA)\n\n"
        "```mermaid\ngraph TD; A-->B;\n```\n\n"
        "[^note]: https://example.test/footnote\n\n"
        "[危険](javascript:alert(1))"
    )
    parsed = HtmlContent(export(service))
    assert not {"img", "script", "iframe", "object", "link", "base"}.intersection(
        parsed.tags
    )
    assert not any(name == "src" for name, _ in parsed.attributes)
    assert not any(
        value and value.startswith(("javascript:", "file:", "data:"))
        for name, value in parsed.attributes
        if name == "href"
    )
    text = "".join(parsed.text)
    for value in [
        "<script>",
        "代替文",
        "画像名",
        "https://example.test/image.png",
        "file:///etc/passwd",
        "data:image/png;base64,AAAA",
        "graph TD; A-->B;",
        "[^note]: https://example.test/footnote",
    ]:
        assert value in text


@pytest.mark.parametrize(
    "comments,history", [(False, False), (True, False), (False, True), (True, True)]
)
def test_selection_and_optional_sections_are_shared(monkeypatch, comments, history):
    """新HTMLも既存の章選択・コメント/履歴指定を使い、対象外を混ぜない。"""
    monkeypatch.setattr(settings, "requirement_rich_html_enabled", True)
    service, _, _, _ = make_service(monkeypatch)
    content = export(
        service,
        section_ids=[10],
        include_comments=comments,
        include_change_logs=history,
    )
    parsed = HtmlContent(content)
    assert list(parsed.requirements) == [4, 3]
    assert "未所属本文" not in content and "対象外コメント" not in content
    assert "章B" not in content
    assert ("選択章のコメント" in content) == comments
    assert ("created document:1" in content) == history


def test_render_failure_does_not_record_success_and_flag_off_recovers(monkeypatch):
    """描画失敗前に成功履歴を作らず、OFFで旧HTMLへ戻せる。"""
    monkeypatch.setattr(settings, "requirement_rich_html_enabled", True)
    service, _, _, record = make_service(monkeypatch)

    def fail(*_):
        raise RuntimeError("renderer failed")

    monkeypatch.setattr("app.services.requirement_export_html.render_document", fail)
    with pytest.raises(RuntimeError, match="renderer failed"):
        export(service)
    record.assert_not_called()
    monkeypatch.setattr(settings, "requirement_rich_html_enabled", False)
    assert "<!doctype html>" in export(service)
    record.assert_called_once()
