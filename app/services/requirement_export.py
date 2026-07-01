"""要件定義書出力サービスを定義するモジュール。"""

import base64
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from html import escape
from textwrap import wrap

from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import BadRequestError, NotFoundError
from app.models.requirement import (
    Requirement,
    RequirementChangeLog,
    RequirementDocument,
    RequirementOpenIssue,
    RequirementSection,
    RequirementTargetComment,
)
from app.repositories.requirement_change_log import RequirementChangeLogRepository
from app.repositories.requirement_document import RequirementDocumentRepository
from app.repositories.requirement_item import RequirementRepository
from app.repositories.requirement_open_issue import RequirementOpenIssueRepository
from app.repositories.requirement_section import RequirementSectionRepository
from app.repositories.requirement_target_comment import (
    RequirementTargetCommentRepository,
)
from app.schemas.requirement import RequirementDocumentExportCreate
from app.services.requirement_change_log import (
    RequirementChangeLogAction,
    RequirementChangeLogService,
    RequirementChangeLogTargetType,
)


@dataclass(frozen=True)
class RequirementDocumentExportResult:
    """要件定義書出力処理の結果値。"""

    format: str
    content: str


class RequirementExportService:
    """要件定義書の出力に関するビジネスロジックを提供する。"""

    def __init__(
        self,
        document_repository: RequirementDocumentRepository | None = None,
        section_repository: RequirementSectionRepository | None = None,
        requirement_repository: RequirementRepository | None = None,
        open_issue_repository: RequirementOpenIssueRepository | None = None,
        target_comment_repository: RequirementTargetCommentRepository | None = None,
        change_log_repository: RequirementChangeLogRepository | None = None,
        change_log_service: RequirementChangeLogService | None = None,
    ) -> None:
        """RequirementExportServiceを初期化する。

        Args:
            document_repository: 要件定義書Repository。
            section_repository: 要件定義セクションRepository。
            requirement_repository: 要件Repository。
            open_issue_repository: 未決事項Repository。
            target_comment_repository: 汎用コメントRepository。
            change_log_repository: 変更履歴Repository。
            change_log_service: 変更履歴Service。
        """
        self.document_repository = (
            document_repository or RequirementDocumentRepository()
        )
        self.section_repository = section_repository or RequirementSectionRepository()
        self.requirement_repository = (
            requirement_repository or RequirementRepository()
        )
        self.open_issue_repository = (
            open_issue_repository or RequirementOpenIssueRepository()
        )
        self.target_comment_repository = (
            target_comment_repository or RequirementTargetCommentRepository()
        )
        self.change_log_repository = (
            change_log_repository or RequirementChangeLogRepository()
        )
        self.change_log_service = change_log_service or RequirementChangeLogService()

    def export_document(
        self,
        db: Session,
        *,
        project_id: int,
        document_id: int,
        export_in: RequirementDocumentExportCreate,
        actor_id: int | None = None,
    ) -> RequirementDocumentExportResult:
        """要件定義書を指定形式で出力する。

        Args:
            db: DBセッション。
            project_id: 出力対象のプロジェクトID。
            document_id: 出力対象の要件定義書ID。
            export_in: 出力リクエスト。
            actor_id: 操作ユーザーID。

        Returns:
            要件定義書の出力結果値。

        Raises:
            BadRequestError: 未対応の出力形式が指定された場合。
            NotFoundError: 要件定義書が存在しない、またはプロジェクトに属さない場合。
        """
        if export_in.format not in {"markdown", "html", "pdf"}:
            raise BadRequestError(error_messages.UNSUPPORTED_REQUIREMENT_EXPORT_FORMAT)

        document = self._get_document_in_project(
            db,
            project_id=project_id,
            document_id=document_id,
        )
        markdown_content = self._build_markdown(
            db,
            document=document,
            include_comments=export_in.include_comments,
            include_change_logs=export_in.include_change_logs,
            section_ids=export_in.section_ids,
        )
        content = self._build_export_content(markdown_content, export_in.format)
        change_log_new_value = {
            "format": export_in.format,
            "include_comments": export_in.include_comments,
            "include_change_logs": export_in.include_change_logs,
        }
        if export_in.section_ids is not None:
            change_log_new_value["section_ids"] = export_in.section_ids
        self.change_log_service.record(
            db,
            document_id=document.id,
            target_type=RequirementChangeLogTargetType.DOCUMENT,
            target_id=document.id,
            action=RequirementChangeLogAction.EXPORTED,
            new_value=change_log_new_value,
            changed_by=actor_id,
        )
        return RequirementDocumentExportResult(
            format=export_in.format,
            content=content,
        )

    def _build_export_content(self, markdown_content: str, export_format: str) -> str:
        """指定形式の出力本文を生成する。"""
        if export_format == "html":
            return self._build_html(markdown_content)
        if export_format == "pdf":
            return self._build_pdf_base64(markdown_content)
        return markdown_content

    def _build_pdf_base64(self, markdown_content: str) -> str:
        """Markdown本文から最小構成のPDFを生成しbase64文字列で返す。"""
        pdf_bytes = self._build_pdf_bytes(markdown_content)
        return base64.b64encode(pdf_bytes).decode("ascii")

    def _build_pdf_bytes(self, markdown_content: str) -> bytes:
        """Markdown本文を簡易PDFバイト列へ変換する。"""
        lines = self._build_pdf_text_lines(markdown_content)
        pages = [lines[index : index + 48] for index in range(0, len(lines), 48)]
        if not pages:
            pages = [[]]

        objects: list[bytes] = []
        page_object_numbers: list[int] = []
        for _ in pages:
            page_object_numbers.append(len(objects) + 5)
            objects.append(b"")
            objects.append(b"")

        objects.insert(
            0,
            b"<< /Type /Catalog /Pages 2 0 R >>",
        )
        page_refs = b" ".join(
            f"{number} 0 R".encode("ascii") for number in page_object_numbers
        )
        objects.insert(
            1,
            b"<< /Type /Pages /Kids ["
            + page_refs
            + b"] /Count "
            + str(len(page_object_numbers)).encode("ascii")
            + b" >>",
        )
        objects.insert(
            2,
            b"<< /Type /Font /Subtype /Type0 /BaseFont /HeiseiKakuGo-W5 "
            b"/Encoding /UniJIS-UCS2-H /DescendantFonts [4 0 R] >>",
        )
        objects.insert(
            3,
            b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /HeiseiKakuGo-W5 "
            b"/CIDSystemInfo << /Registry (Adobe) /Ordering (Japan1) "
            b"/Supplement 6 >> >>",
        )

        for index, page_lines in enumerate(pages):
            page_number = 5 + index * 2
            content_number = page_number + 1
            objects[page_number - 1] = (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                b"/Resources << /Font << /F1 3 0 R >> >> /Contents "
                + f"{content_number} 0 R".encode("ascii")
                + b" >>"
            )
            content = self._build_pdf_content_stream(page_lines)
            objects[content_number - 1] = (
                b"<< /Length "
                + str(len(content)).encode("ascii")
                + b" >>\nstream\n"
                + content
                + b"\nendstream"
            )

        return self._serialize_pdf_objects(objects)

    def _build_pdf_text_lines(self, markdown_content: str) -> list[str]:
        """PDF表示用のテキスト行を作成する。"""
        lines: list[str] = []
        for raw_line in markdown_content.splitlines():
            line = raw_line.strip()
            if not line:
                lines.append("")
                continue
            line = line.lstrip("#").strip()
            if line.startswith("- "):
                line = f"・{line[2:]}"
            lines.extend(wrap(line, width=72) or [""])
        return lines

    def _build_pdf_content_stream(self, lines: list[str]) -> bytes:
        """PDFページの本文streamを作成する。"""
        stream_lines = ["BT", "/F1 10 Tf", "50 790 Td", "14 TL"]
        for line in lines:
            encoded = "FEFF" + line.encode("utf-16-be").hex().upper()
            stream_lines.append(f"<{encoded}> Tj")
            stream_lines.append("T*")
        stream_lines.append("ET")
        return "\n".join(stream_lines).encode("ascii")

    def _serialize_pdf_objects(self, objects: list[bytes]) -> bytes:
        """PDFオブジェクトをxref付きで直列化する。"""
        chunks = [b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"]
        offsets = [0]
        for index, payload in enumerate(objects, start=1):
            offsets.append(sum(len(chunk) for chunk in chunks))
            chunks.append(
                f"{index} 0 obj\n".encode("ascii")
                + payload
                + b"\nendobj\n"
            )
        xref_offset = sum(len(chunk) for chunk in chunks)
        xref_lines = [
            b"xref\n",
            f"0 {len(objects) + 1}\n".encode("ascii"),
            b"0000000000 65535 f \n",
        ]
        xref_lines.extend(
            f"{offset:010d} 00000 n \n".encode("ascii") for offset in offsets[1:]
        )
        chunks.extend(
            [
                *xref_lines,
                b"trailer\n",
                b"<< /Size "
                + str(len(objects) + 1).encode("ascii")
                + b" /Root 1 0 R >>\n",
                b"startxref\n",
                str(xref_offset).encode("ascii") + b"\n",
                b"%%EOF\n",
            ]
        )
        return b"".join(chunks)

    def _build_html(self, markdown_content: str) -> str:
        """Markdown本文を簡易HTMLへ変換する。"""
        lines = [
            "<!doctype html>",
            '<html lang="ja">',
            "<head>",
            '<meta charset="utf-8">',
            "<title>Requirement Document</title>",
            "</head>",
            "<body>",
        ]
        in_list = False
        in_paragraph = False

        for raw_line in markdown_content.splitlines():
            line = raw_line.strip()
            if not line:
                if in_paragraph:
                    lines.append("</p>")
                    in_paragraph = False
                if in_list:
                    lines.append("</ul>")
                    in_list = False
                continue

            if line.startswith("#"):
                if in_paragraph:
                    lines.append("</p>")
                    in_paragraph = False
                if in_list:
                    lines.append("</ul>")
                    in_list = False
                level = min(len(line) - len(line.lstrip("#")), 6)
                text = line[level:].strip()
                lines.append(f"<h{level}>{escape(text)}</h{level}>")
                continue

            if line.startswith("- "):
                if in_paragraph:
                    lines.append("</p>")
                    in_paragraph = False
                if not in_list:
                    lines.append("<ul>")
                    in_list = True
                lines.append(f"<li>{escape(line[2:])}</li>")
                continue

            if in_list:
                lines.append("</ul>")
                in_list = False
            if not in_paragraph:
                lines.append("<p>")
                in_paragraph = True
            lines.append(f"{escape(line)}<br>")

        if in_paragraph:
            lines.append("</p>")
        if in_list:
            lines.append("</ul>")
        lines.extend(["</body>", "</html>"])
        return "\n".join(lines) + "\n"

    def _get_document_in_project(
        self,
        db: Session,
        *,
        project_id: int,
        document_id: int,
    ) -> RequirementDocument:
        """プロジェクト内の要件定義書を取得する。"""
        document = self.document_repository.get_by_id(db, document_id)
        if document is None or document.project_id != project_id:
            raise NotFoundError(error_messages.REQUIREMENT_DOCUMENT_NOT_FOUND)
        return document

    def _build_markdown(
        self,
        db: Session,
        *,
        document: RequirementDocument,
        include_comments: bool,
        include_change_logs: bool,
        section_ids: list[int] | None,
    ) -> str:
        """要件定義書のMarkdown本文を生成する。"""
        sections = self.section_repository.list_by_document(db, document.id)
        requirements = self.requirement_repository.list_by_document(db, document.id)
        open_issues = self.open_issue_repository.list_by_document(db, document.id)
        comments = (
            self.target_comment_repository.list_by_document(db, document.id)
            if include_comments
            else []
        )
        change_logs = (
            self.change_log_repository.list_by_document(db, document.id)
            if include_change_logs
            else []
        )
        if section_ids is not None:
            sections, requirements, open_issues, comments, change_logs = (
                self._filter_export_targets(
                    document=document,
                    sections=sections,
                    requirements=requirements,
                    open_issues=open_issues,
                    comments=comments,
                    change_logs=change_logs,
                    section_ids=section_ids,
                )
            )

        requirements_by_section = self._group_requirements_by_section(requirements)
        lines: list[str] = [
            f"# {document.title}",
            "",
            "## 文書情報",
            "",
            f"- 文書コード: {document.document_code}",
            f"- バージョン: {document.version}",
            f"- ステータス: {document.status}",
            f"- 目的: {self._value(document.purpose)}",
            f"- クライアント: {self._value(document.client_name)}",
            f"- ベンダー: {self._value(document.vendor_name)}",
            f"- 承認日時: {self._format_value(document.approved_at)}",
            "",
            "## セクション",
            "",
        ]

        if sections:
            for section in sections:
                lines.extend(
                    self._build_section_lines(
                        section,
                        requirements_by_section.get(section.id, []),
                    )
                )
        else:
            lines.extend(["セクションはありません。", ""])

        unassigned_requirements = requirements_by_section.get(None, [])
        if unassigned_requirements:
            lines.extend(["## 未分類要件", ""])
            lines.extend(self._build_requirement_lines(unassigned_requirements))

        lines.extend(self._build_open_issue_lines(open_issues))

        if include_comments:
            lines.extend(self._build_comment_lines(comments))
        if include_change_logs:
            lines.extend(self._build_change_log_lines(change_logs))

        return "\n".join(lines).rstrip() + "\n"

    def _filter_export_targets(
        self,
        *,
        document: RequirementDocument,
        sections: list[RequirementSection],
        requirements: list[Requirement],
        open_issues: list[RequirementOpenIssue],
        comments: list[RequirementTargetComment],
        change_logs: list[RequirementChangeLog],
        section_ids: list[int],
    ) -> tuple[
        list[RequirementSection],
        list[Requirement],
        list[RequirementOpenIssue],
        list[RequirementTargetComment],
        list[RequirementChangeLog],
    ]:
        """セクション指定出力の対象データへ絞り込む。"""
        requested_ids = set(section_ids)
        sections_by_id = {section.id: section for section in sections}
        if requested_ids.difference(sections_by_id):
            raise NotFoundError(error_messages.REQUIREMENT_SECTION_NOT_FOUND)

        selected_sections = [
            section for section in sections if section.id in requested_ids
        ]
        selected_requirements = [
            requirement
            for requirement in requirements
            if requirement.section_id in requested_ids
        ]
        selected_requirement_ids = {
            requirement.id for requirement in selected_requirements
        }
        selected_open_issues = [
            issue
            for issue in open_issues
            if issue.related_requirement_id in selected_requirement_ids
        ]
        selected_comments = [
            comment
            for comment in comments
            if (
                comment.target_type == "section"
                and comment.target_id in requested_ids
            )
            or (
                comment.target_type == "requirement"
                and comment.target_id in selected_requirement_ids
            )
        ]
        selected_change_logs = [
            change_log
            for change_log in change_logs
            if self._is_change_log_in_export_selection(
                change_log,
                document=document,
                section_ids=requested_ids,
                requirement_ids=selected_requirement_ids,
            )
        ]
        return (
            selected_sections,
            selected_requirements,
            selected_open_issues,
            selected_comments,
            selected_change_logs,
        )

    def _is_change_log_in_export_selection(
        self,
        change_log: RequirementChangeLog,
        *,
        document: RequirementDocument,
        section_ids: set[int],
        requirement_ids: set[int],
    ) -> bool:
        """変更履歴がセクション指定出力の対象か判定する。"""
        if change_log.target_type == RequirementChangeLogTargetType.SECTION:
            return change_log.target_id in section_ids
        if change_log.target_type == RequirementChangeLogTargetType.REQUIREMENT_ITEM:
            return change_log.target_id in requirement_ids
        return (
            change_log.target_type == RequirementChangeLogTargetType.DOCUMENT
            and change_log.target_id == document.id
        )

    def _group_requirements_by_section(
        self,
        requirements: list[Requirement],
    ) -> dict[int | None, list[Requirement]]:
        """要件をセクションIDごとにまとめる。"""
        grouped: dict[int | None, list[Requirement]] = defaultdict(list)
        for requirement in requirements:
            grouped[requirement.section_id].append(requirement)
        return grouped

    def _build_section_lines(
        self,
        section: RequirementSection,
        requirements: list[Requirement],
    ) -> list[str]:
        """セクションのMarkdown行を生成する。"""
        lines = [
            f"### {section.title}",
            "",
            f"- 種別: {section.section_type}",
            f"- ステータス: {section.status}",
            "",
        ]
        if section.content:
            lines.extend([section.content, ""])
        lines.extend(self._build_requirement_lines(requirements))
        return lines

    def _build_requirement_lines(
        self,
        requirements: list[Requirement],
    ) -> list[str]:
        """要件一覧のMarkdown行を生成する。"""
        if not requirements:
            return ["要件はありません。", ""]

        lines: list[str] = []
        for requirement in requirements:
            lines.extend(
                [
                    f"#### {requirement.requirement_code} {requirement.title}",
                    "",
                    f"- 種別: {requirement.requirement_type}",
                    f"- カテゴリ: {self._value(requirement.category)}",
                    f"- 優先度: {requirement.priority}",
                    f"- ステータス: {requirement.status}",
                    f"- オーナーID: {self._format_value(requirement.owner_id)}",
                    "",
                    self._value(requirement.description),
                    "",
                ]
            )
            if requirement.acceptance_criteria:
                lines.extend(
                    [
                        "受け入れ条件:",
                        requirement.acceptance_criteria,
                        "",
                    ]
                )
        return lines

    def _build_open_issue_lines(
        self,
        open_issues: list[RequirementOpenIssue],
    ) -> list[str]:
        """未決事項一覧のMarkdown行を生成する。"""
        lines = ["## 未決事項", ""]
        if not open_issues:
            return [*lines, "未決事項はありません。", ""]

        for issue in open_issues:
            lines.extend(
                [
                    f"### {issue.issue_code} {issue.title}",
                    "",
                    f"- ステータス: {issue.status}",
                    f"- 担当者ID: {self._format_value(issue.assignee_id)}",
                    f"- 期限: {self._format_value(issue.due_date)}",
                    f"- 影響範囲: {self._value(issue.impact_scope)}",
                    "",
                    self._value(issue.description),
                    "",
                ]
            )
            if issue.resolution:
                lines.extend(["解決内容:", issue.resolution, ""])
        return lines

    def _build_comment_lines(
        self,
        comments: list[RequirementTargetComment],
    ) -> list[str]:
        """コメント一覧のMarkdown行を生成する。"""
        lines = ["## コメント", ""]
        if not comments:
            return [*lines, "コメントはありません。", ""]

        for comment in comments:
            status = "resolved" if comment.is_resolved else "open"
            lines.extend(
                [
                    f"- [{status}] {comment.target_type}:{comment.target_id} "
                    f"author={comment.author_id}",
                    f"  - {comment.body}",
                ]
            )
        lines.append("")
        return lines

    def _build_change_log_lines(
        self,
        change_logs: list[RequirementChangeLog],
    ) -> list[str]:
        """変更履歴一覧のMarkdown行を生成する。"""
        lines = ["## 変更履歴", ""]
        if not change_logs:
            return [*lines, "変更履歴はありません。", ""]

        for change_log in change_logs:
            lines.append(
                f"- {self._format_value(change_log.changed_at)} "
                f"{change_log.action} "
                f"{change_log.target_type}:{change_log.target_id}"
            )
        lines.append("")
        return lines

    def _value(self, value: object | None) -> str:
        """Noneを含む値をMarkdown表示用文字列へ変換する。"""
        if value is None or value == "":
            return "-"
        return str(value)

    def _format_value(self, value: object | None) -> str:
        """日付を含む値をMarkdown表示用文字列へ変換する。"""
        if isinstance(value, datetime | date):
            return value.isoformat()
        return self._value(value)
