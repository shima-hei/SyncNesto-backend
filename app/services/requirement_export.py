"""要件定義書出力サービスを定義するモジュール。"""

from collections import defaultdict
from datetime import date, datetime

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
from app.repositories.requirement import (
    RequirementChangeLogRepository,
    RequirementDocumentRepository,
    RequirementOpenIssueRepository,
    RequirementRepository,
    RequirementSectionRepository,
    RequirementTargetCommentRepository,
)
from app.schemas.requirement import (
    RequirementDocumentExportCreate,
    RequirementDocumentExportRead,
)
from app.services.requirement_change_log import (
    RequirementChangeLogAction,
    RequirementChangeLogService,
    RequirementChangeLogTargetType,
)


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
    ) -> RequirementDocumentExportRead:
        """要件定義書を指定形式で出力する。

        Args:
            db: DBセッション。
            project_id: 出力対象のプロジェクトID。
            document_id: 出力対象の要件定義書ID。
            export_in: 出力リクエスト。
            actor_id: 操作ユーザーID。

        Returns:
            要件定義書の出力結果。

        Raises:
            BadRequestError: 未対応の出力形式が指定された場合。
            NotFoundError: 要件定義書が存在しない、またはプロジェクトに属さない場合。
        """
        if export_in.format != "markdown":
            raise BadRequestError(error_messages.UNSUPPORTED_REQUIREMENT_EXPORT_FORMAT)

        document = self._get_document_in_project(
            db,
            project_id=project_id,
            document_id=document_id,
        )
        content = self._build_markdown(
            db,
            document=document,
            include_comments=export_in.include_comments,
            include_change_logs=export_in.include_change_logs,
        )
        self.change_log_service.record(
            db,
            document_id=document.id,
            target_type=RequirementChangeLogTargetType.DOCUMENT,
            target_id=document.id,
            action=RequirementChangeLogAction.EXPORTED,
            new_value={
                "format": export_in.format,
                "include_comments": export_in.include_comments,
                "include_change_logs": export_in.include_change_logs,
            },
            changed_by=actor_id,
        )
        return RequirementDocumentExportRead(format=export_in.format, content=content)

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
