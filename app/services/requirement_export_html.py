"""構造化された要件出力データを、外部資源を参照しないHTMLへ描画する。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from html import escape
from typing import TYPE_CHECKING

from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from markdown_it.utils import EnvType, OptionsDict

from app.models.requirement import Requirement
from app.services.requirement_export_markdown import export_markdown_parser

if TYPE_CHECKING:
    from app.services.requirement_export import RequirementExportData


class _ExportRenderer(RendererHTML):
    """画像は取得/埋込せず、代替文・参照先を読める形で残す。"""

    def image(
        self, tokens: Sequence[Token], idx: int, options: OptionsDict, env: EnvType
    ) -> str:
        """認識済み画像記法を安全な文字列へ置き換える。"""
        token = tokens[idx]
        source = token.attrGet("src") or ""
        title = token.attrGet("title")
        suffix = f' "{title}"' if title else ""
        literal = f"![{token.content}]({source}{suffix})"
        return f'<span class="export-image-placeholder">{escape(literal)}</span>'


_HEAD = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<title>Requirement Document</title><style>
body{font-family:sans-serif;line-height:1.6;max-width:60rem;margin:2rem auto;
padding:0 1rem;overflow-wrap:anywhere}
h1,h2,h3,h4,h5,h6{break-after:avoid-page}
.export-label{break-after:avoid-page}
.export-requirement{break-inside:avoid-page}
.export-fields li{white-space:pre-wrap}
p,li{orphans:3;widows:3}
table{border-collapse:collapse;width:100%;table-layout:fixed;margin:1rem 0}
th,td{border:1px solid #777;padding:.4rem;vertical-align:top;overflow-wrap:anywhere}
th{background:#eee;color:#000}pre{white-space:pre-wrap;overflow-wrap:anywhere;
padding:.6rem;border:1px solid #aaa}code{font-family:monospace}
@page{size:A4;margin:15mm}
@media print{body{max-width:none;margin:0;padding:0;font-size:10pt;color:black;
background:white}thead{display:table-header-group}}
</style></head><body>"""


def render_document(
    data: RequirementExportData, display: Callable[[object | None], str]
) -> str:
    """保存済み出力対象を描画する。取得・認可・履歴の更新は行わない。"""
    md = export_markdown_parser(_ExportRenderer)

    def heading(level: int, text: str) -> str:
        """タイトルはMarkdownとして解釈せず、構造を固定する。"""
        return f"<h{level}>{escape(text)}</h{level}>"

    def text(value: str, *, label: bool = False) -> str:
        """属性や状態などの通常文字列を表示する。"""
        attribute = ' class="export-label"' if label else ""
        return f"<p{attribute}>{escape(value)}</p>"

    def fields(values: Sequence[tuple[str, object | None]]) -> str:
        """属性値をMarkdownの見出し/HTMLとして解釈しない。"""
        items = "".join(
            f"<li>{escape(label)}: {escape(display(value))}</li>"
            for label, value in values
        )
        return f'<ul class="export-fields">{items}</ul>'

    def body(value: str | None) -> str:
        """未設定値とMarkdown本文を区別する。"""
        return text("-") if value is None or value == "" else md.render(value)

    def requirements(items: list[Requirement]) -> list[str]:
        """モデルのIDでREQの区間を確定し、本文の見出しと区別する。"""
        if not items:
            return [text("要件はありません。")]
        result: list[str] = []
        for item in items:
            result.extend(
                [
                    '<section class="export-requirement" '
                    f'data-requirement-id="{item.id}">',
                    heading(4, f"{item.requirement_code} {item.title}"),
                    fields(
                        [
                            ("種別", item.requirement_type),
                            ("カテゴリ", item.category),
                            ("優先度", item.priority),
                            ("ステータス", item.status),
                            ("オーナーID", item.owner_id),
                        ]
                    ),
                    body(item.description),
                ]
            )
            if item.rationale and item.rationale.strip():
                result.extend([text("理由:", label=True), body(item.rationale)])
            if item.acceptance_criteria:
                result.extend(
                    [text("受け入れ条件:", label=True), body(item.acceptance_criteria)]
                )
            result.append("</section>")
        return result

    document = data.document
    parts = [
        _HEAD,
        heading(1, document.title),
        heading(2, "文書情報"),
        fields(
            [
                ("文書コード", document.document_code),
                ("バージョン", document.version),
                ("ステータス", document.status),
                ("目的", document.purpose),
                ("案件ゴール", document.project_goal),
                ("案件スコープ", document.project_scope),
                ("案件完了条件", document.project_completion_criteria),
                ("クライアント", document.client_name),
                ("ベンダー", document.vendor_name),
                ("承認日時", document.approved_at),
            ]
        ),
        heading(2, "セクション"),
    ]
    if not data.sections:
        parts.append(text("セクションはありません。"))
    for section in data.sections:
        parts.extend(
            [
                heading(3, section.title),
                fields(
                    [("種別", section.section_type), ("ステータス", section.status)]
                ),
            ]
        )
        if section.content:
            parts.append(body(section.content))
        parts.extend(requirements(data.requirements_by_section.get(section.id, [])))
    unassigned = data.requirements_by_section.get(None, [])
    if unassigned:
        parts.extend([heading(2, "未分類要件"), *requirements(unassigned)])

    parts.append(heading(2, "未決事項"))
    if not data.open_issues:
        parts.append(text("未決事項はありません。"))
    for issue in data.open_issues:
        parts.extend(
            [
                heading(3, f"{issue.issue_code} {issue.title}"),
                fields(
                    [
                        ("ステータス", issue.status),
                        ("担当者ID", issue.assignee_id),
                        ("期限", issue.due_date),
                        ("影響範囲", issue.impact_scope),
                    ]
                ),
                body(issue.description),
            ]
        )
        if issue.resolution:
            parts.extend([text("解決内容:", label=True), body(issue.resolution)])

    if data.include_comments:
        parts.append(heading(2, "コメント"))
        if not data.comments:
            parts.append(text("コメントはありません。"))
        for comment in data.comments:
            status = "resolved" if comment.is_resolved else "open"
            parts.extend(
                [
                    text(
                        f"[{status}] {comment.target_type}:{comment.target_id} "
                        f"author={comment.author_id}",
                        label=True,
                    ),
                    body(comment.body),
                ]
            )
    if data.include_change_logs:
        parts.append(heading(2, "変更履歴"))
        if not data.change_logs:
            parts.append(text("変更履歴はありません。"))
        for change in data.change_logs:
            parts.append(
                text(
                    f"{display(change.changed_at)} {change.action} "
                    f"{change.target_type}:{change.target_id}"
                )
            )
    parts.append("</body></html>")
    return "\n".join(parts) + "\n"
