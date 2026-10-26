"""選択済みの構造化データから、文字を埋め込むPDFだけを描画する。"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Sequence
from html import escape
from io import BytesIO
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, cast

from markdown_it.token import Token
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfdoc import PDFDocument, PDFStream
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    HRFlowable,
    KeepTogether,
    LongTable,
    PageTemplate,
    Paragraph,
    TableStyle,
)
from reportlab.platypus.doctemplate import LayoutError

from app.core.exceptions import RequirementPdfExportError
from app.services.requirement_export_markdown import export_markdown_parser
from app.services.requirement_export_pdf import (
    FONT_SIZE,
    LINE_HEIGHT,
    MARGIN,
    PAGE_HEIGHT,
    PAGE_WIDTH,
)

if TYPE_CHECKING:
    from app.models.requirement import Requirement
    from app.services.requirement_export import RequirementExportData

BODY_WIDTH = PAGE_WIDTH - MARGIN * 2
BODY_HEIGHT = PAGE_HEIGHT - MARGIN * 2
MAX_PDF_BYTES = 3_000_000  # base64/JSONを含め、Functionの4.5 MB以内に余裕を残す。
FONT_REGULAR = "SyncnestoBIZRegular"
FONT_BOLD = "SyncnestoBIZBold"
_FONT_DIRECTORY = Path(__file__).resolve().parents[1] / "assets/fonts/biz-ud-gothic"
_RENDER_LOCK = RLock()


class UnicodeTrueTypeFont(TTFont):
    """5.1.0の補助面CMapを、PDF仕様のUTF-16BEサロゲート列へ修正する。"""

    def addObjects(self, doc: PDFDocument) -> None:
        """この書体が追加したCMapだけを直し、ライブラリ全体を変更しない。"""
        existing = set(doc.idToObject)
        super().addObjects(doc)
        for name in set(doc.idToObject) - existing:
            if name.startswith("toUnicodeCMap:"):
                stream = cast(PDFStream, doc.idToObject[name])
                assert isinstance(stream.content, str)
                stream.content = re.sub(
                    r"(<[0-9A-F]{2}> )<([0-9A-F]{5,6})>",
                    lambda m: (
                        m[1]
                        + "<"
                        + chr(int(m[2], 16)).encode("utf-16-be").hex().upper()
                        + ">"
                    ),
                    stream.content,
                )


class LiteralParagraph(Paragraph):
    """codeの空白を畳まず、通常ParagraphのCJK折返しと分割を利用する。"""

    def _setup(self, text, style, bulletText, frags, cleaner):
        """利用者の空白をUnicode別字へ置換せず、そのまま残す。"""
        getattr(super(), "_setup")(text, style, bulletText, frags, lambda value: value)


def _register_fonts() -> frozenset[int]:
    """同梱した実書体だけを登録し、両方で描画可能な文字を返す。"""
    for name, weight in [(FONT_REGULAR, "Regular"), (FONT_BOLD, "Bold")]:
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(
                UnicodeTrueTypeFont(
                    name, str(_FONT_DIRECTORY / f"BIZUDGothic-{weight}.ttf")
                )
            )
    pdfmetrics.registerFontFamily(
        FONT_REGULAR,
        normal=FONT_REGULAR,
        bold=FONT_BOLD,
        italic=FONT_REGULAR,
        boldItalic=FONT_BOLD,
    )
    return frozenset(
        cp
        for cp, glyph in cast(
            TTFont, pdfmetrics.getFont(FONT_REGULAR)
        ).face.charToGlyph.items()
        if glyph
    ) & frozenset(
        cp
        for cp, glyph in cast(
            TTFont, pdfmetrics.getFont(FONT_BOLD)
        ).face.charToGlyph.items()
        if glyph
    )


class PdfBuilder:
    """モデルの境界と原文位置を保ち、外部資源を使わずFlowableへ変換する。"""

    def __init__(self) -> None:
        """呼出元の描画ロック内で、書体とリクエスト専用の状態を準備する。"""
        self.coverage = _register_fonts()
        self.parser = export_markdown_parser()
        self.style = ParagraphStyle(
            "RequirementBody",
            fontName=FONT_REGULAR,
            fontSize=FONT_SIZE,
            leading=LINE_HEIGHT,
            wordWrap="CJK",
            spaceAfter=6,
            allowWidows=0,
            allowOrphans=0,
        )

    def validate(self, value: str, location: str) -> None:
        """欠字や未対応の結合列を、変換前のUnicode code point位置で拒否する。"""
        for offset, char in enumerate(value):
            cp = ord(char)
            if char in "\n\r\t":
                continue
            unsupported = (
                cp not in self.coverage
                or unicodedata.category(char).startswith("M")
                or cp == 0x200D
                or 0xFE00 <= cp <= 0xFE0F
                or 0xE0100 <= cp <= 0xE01EF
            )
            if unsupported:
                line = value.count("\n", 0, offset) + 1
                raise RequirementPdfExportError(
                    f"{location} / line:{line} / offset:{offset}",
                    f"未対応の文字/結合列 U+{cp:04X}",
                )

    def paragraph(
        self,
        markup: str,
        *,
        heading: int = 0,
        keep: bool = False,
        indent: int = 0,
        literal: bool = False,
    ) -> Paragraph:
        """信頼した固定markupだけを、CJK折返しの段落へ渡す。"""
        style = ParagraphStyle(
            "RequirementParagraph",
            parent=self.style,
            fontName=FONT_BOLD if heading else FONT_REGULAR,
            fontSize=FONT_SIZE + max(0, 5 - heading) if heading else FONT_SIZE,
            leading=LINE_HEIGHT + max(0, 5 - heading) if heading else LINE_HEIGHT,
            keepWithNext=keep or bool(heading),
            leftIndent=indent,
            spaceBefore=6 if heading else 0,
        )
        paragraph_class = LiteralParagraph if literal else Paragraph
        return paragraph_class(markup or "<br/>", style)

    def plain(
        self, value: str, location: str, *, heading: int = 0, keep: bool = False
    ) -> Paragraph:
        """属性/タイトルはMarkdownとして解釈せず、元の位置で検査する。"""
        self.validate(value, location)
        return self.paragraph(
            escape(value).replace("\n", "<br/>").replace("\t", "    "),
            heading=heading,
            keep=keep,
        )

    def inline(self, tokens: Sequence[Token]) -> str:
        """利用者の属性を渡さず、解析済みの許可tokenだけを固定markupへ写す。"""
        parts: list[str] = []
        links: list[str] = []
        for token in tokens:
            if token.type in {"text", "code_inline"}:
                parts.append(escape(token.content))
            elif token.type in {"softbreak", "hardbreak"}:
                parts.append("<br/>")
            elif token.type == "strong_open":
                parts.append("<b>")
            elif token.type == "strong_close":
                parts.append("</b>")
            elif token.type in {"em_open", "em_close"}:
                # 実Italicを採用していないため、元の強調記号を可読文字として残す。
                parts.append(escape(token.markup))
            elif token.type == "link_open":
                links.append(str(token.attrGet("href") or ""))
            elif token.type == "link_close":
                parts.append(f" ({escape(links.pop())})")
            elif token.type == "image":
                title = token.attrGet("title")
                suffix = f' "{title}"' if title else ""
                literal = f"![{token.content}]({token.attrGet('src') or ''}{suffix})"
                parts.append(escape(literal))
            else:
                parts.append(escape(token.content or token.markup))
        return "".join(parts).replace("\t", "    ")

    def table(self, tokens: Sequence[Token], location: str) -> LongTable:
        """行列の順を保持し、可読幅と分割可能なヘッダーを満たす表を作る。"""
        rows: list[list[Paragraph]] = []
        row: list[Paragraph] = []
        header = True
        for token in tokens:
            if token.type == "tbody_open":
                header = False
            elif token.type == "tr_open":
                row = []
            elif token.type == "inline":
                content = self.inline(token.children or [])
                row.append(self.paragraph(f"<b>{content}</b>" if header else content))
            elif token.type == "tr_close":
                rows.append(row)
        columns = max(map(len, rows), default=0)
        # 全角4文字＋左右4ptの余白を可読幅とし、文字サイズは自動縮小しない。
        minimum = pdfmetrics.stringWidth("日本語表", FONT_REGULAR, FONT_SIZE) + 8
        if not columns or columns * minimum > BODY_WIDTH:
            raise RequirementPdfExportError(location, "表が本文幅に収まりません")
        width = BODY_WIDTH / columns
        for cell in rows[0]:
            if cell.wrap(width - 8, BODY_HEIGHT)[1] + 30 > BODY_HEIGHT:
                raise RequirementPdfExportError(location, "表のヘッダーが長すぎます")
        table = LongTable(
            rows,
            colWidths=[width] * columns,
            repeatRows=1,
            splitByRow=1,
            splitInRow=1,
            hAlign="LEFT",
            spaceAfter=8,
            style=TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            ),
        )
        return table

    def markdown(self, value: str | None, location: str) -> list[Flowable]:
        """構文を同じparserで読み、ラベル保持を妨げない描画要素へ変換する。"""
        if not value:
            return [self.plain("-", location)]
        self.validate(value, location)
        tokens = self.parser.parse(value)
        result: list[Flowable] = []
        index = 0
        heading = 0
        lists: list[int | None] = []
        prefix = ""
        table_number = 0
        while index < len(tokens):
            token = tokens[index]
            if token.type == "table_open":
                end = index + 1
                while tokens[end].type != "table_close":
                    end += 1
                table_number += 1
                result.append(
                    self.table(tokens[index:end], f"{location} / table:{table_number}")
                )
                index = end
            elif token.type == "heading_open":
                heading = int(token.tag[1:])
            elif token.type == "heading_close":
                heading = 0
            elif token.type in {"bullet_list_open", "ordered_list_open"}:
                start = token.attrGet("start")
                lists.append(
                    int(start or 1) if token.type == "ordered_list_open" else None
                )
            elif token.type in {"bullet_list_close", "ordered_list_close"}:
                lists.pop()
            elif token.type == "list_item_open":
                number = lists[-1]
                prefix = "・" if number is None else f"{number}. "
                if number is not None:
                    lists[-1] = number + 1
            elif token.type == "inline":
                result.append(
                    self.paragraph(
                        prefix + self.inline(token.children or []),
                        heading=heading,
                        indent=max(0, len(lists) - 1) * 12,
                        literal=any(
                            t.type == "code_inline" for t in token.children or []
                        ),
                    )
                )
                prefix = ""
            elif token.type in {"fence", "code_block"}:
                result.append(
                    self.paragraph(
                        escape(token.content).replace("\n", "<br/>"), literal=True
                    )
                )
            elif token.type == "hr":
                result.append(HRFlowable(width="100%", thickness=0.4, spaceAfter=6))
            index += 1
        return result

    def fields(
        self,
        values: Sequence[tuple[str, object | None]],
        location: str,
        display: Callable[[object | None], str],
    ) -> list[Flowable]:
        """構造化属性を順番どおりに、通常文字列として描く。"""
        return [
            self.plain(f"{label}: {display(value)}", f"{location} / {label}")
            for label, value in values
        ]

    def requirements(
        self,
        items: list[Requirement],
        location: str,
        display: Callable[[object | None], str],
    ) -> list[Flowable]:
        """モデルID単位で短いREQだけをまとめ、長文と表は分割可能にする。"""
        if not items:
            return [self.plain("要件はありません。", location)]
        result: list[Flowable] = []
        for item in items:
            source = f"{location} / requirement:{item.id}"
            group: list[Flowable] = [
                self.plain(f"{item.requirement_code} {item.title}", source, heading=4),
                *self.fields(
                    [
                        ("種別", item.requirement_type),
                        ("カテゴリ", item.category),
                        ("優先度", item.priority),
                        ("ステータス", item.status),
                        ("オーナーID", item.owner_id),
                    ],
                    source,
                    display,
                ),
                *self.markdown(item.description, f"{source} / description"),
            ]
            for field, label, value in [
                ("rationale", "理由:", item.rationale),
                ("acceptance_criteria", "受け入れ条件:", item.acceptance_criteria),
            ]:
                if value and (field != "rationale" or value.strip()):
                    group.extend(
                        [
                            self.plain(label, source, keep=True),
                            *self.markdown(value, f"{source} / {field}"),
                        ]
                    )
            height = sum(
                part.wrap(BODY_WIDTH, BODY_HEIGHT)[1]
                + part.getSpaceBefore()
                + part.getSpaceAfter()
                for part in group
            )
            result.extend([KeepTogether(group)] if height <= BODY_HEIGHT else group)
        return result

    def document(
        self, data: RequirementExportData, display: Callable[[object | None], str]
    ) -> list[Flowable]:
        """文書/章/REQ/未決事項/任意情報を、既存と同じ条件・順序で組み立てる。"""
        document = data.document
        source = f"document:{document.id}"
        story: list[Flowable] = [
            self.plain(document.title, f"{source} / title", heading=1),
            self.plain("文書情報", source, heading=2),
            *self.fields(
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
                ],
                source,
                display,
            ),
            self.plain("セクション", source, heading=2),
        ]
        if not data.sections:
            story.append(self.plain("セクションはありません。", source))
        for section in data.sections:
            target = f"{source} / section:{section.id}"
            story.extend(
                [
                    self.plain(section.title, target, heading=3),
                    *self.fields(
                        [
                            ("種別", section.section_type),
                            ("ステータス", section.status),
                        ],
                        target,
                        display,
                    ),
                    *(
                        self.markdown(section.content, f"{target} / content")
                        if section.content
                        else []
                    ),
                    *self.requirements(
                        data.requirements_by_section.get(section.id, []),
                        target,
                        display,
                    ),
                ]
            )
        unassigned = data.requirements_by_section.get(None, [])
        if unassigned:
            story.extend(
                [
                    self.plain("未分類要件", source, heading=2),
                    *self.requirements(unassigned, source, display),
                ]
            )
        story.append(self.plain("未決事項", source, heading=2))
        if not data.open_issues:
            story.append(self.plain("未決事項はありません。", source))
        for issue in data.open_issues:
            target = f"{source} / issue:{issue.id}"
            story.extend(
                [
                    self.plain(f"{issue.issue_code} {issue.title}", target, heading=3),
                    *self.fields(
                        [
                            ("ステータス", issue.status),
                            ("担当者ID", issue.assignee_id),
                            ("期限", issue.due_date),
                            ("影響範囲", issue.impact_scope),
                        ],
                        target,
                        display,
                    ),
                    *self.markdown(issue.description, f"{target} / description"),
                ]
            )
            if issue.resolution:
                story.extend(
                    [
                        self.plain("解決内容:", target, keep=True),
                        *self.markdown(issue.resolution, f"{target} / resolution"),
                    ]
                )
        if data.include_comments:
            story.append(self.plain("コメント", source, heading=2))
            if not data.comments:
                story.append(self.plain("コメントはありません。", source))
            for comment in data.comments:
                target = f"{source} / comment:{comment.id}"
                status = "resolved" if comment.is_resolved else "open"
                story.extend(
                    [
                        self.plain(
                            f"[{status}] {comment.target_type}:{comment.target_id} "
                            f"author={comment.author_id}",
                            target,
                            keep=True,
                        ),
                        *self.markdown(comment.body, f"{target} / body"),
                    ]
                )
        if data.include_change_logs:
            story.append(self.plain("変更履歴", source, heading=2))
            if not data.change_logs:
                story.append(self.plain("変更履歴はありません。", source))
            for change in data.change_logs:
                story.append(
                    self.plain(
                        f"{display(change.changed_at)} {change.action} "
                        f"{change.target_type}:{change.target_id}",
                        f"{source} / change:{change.id}",
                    )
                )
        return story


def render_document(
    data: RequirementExportData, display: Callable[[object | None], str]
) -> bytes:
    """リクエストごとのPDFを生成し、履歴更新を行わずバイト列だけを返す。"""
    # ReportLabの共有font登録とsubset状態を同時に変更しない。
    with _RENDER_LOCK:
        builder = PdfBuilder()
        story = builder.document(data, display)
        buffer = BytesIO()
        document = BaseDocTemplate(
            buffer,
            pagesize=(PAGE_WIDTH, PAGE_HEIGHT),
            leftMargin=MARGIN,
            rightMargin=MARGIN,
            topMargin=MARGIN,
            bottomMargin=MARGIN,
            title="Requirement Document",
            author="",
            creator="Syncnesto",
            subject="BIZ UDGothic (SIL OFL 1.1); licenses: THIRD_PARTY_NOTICES.md",
        )
        frame = Frame(
            MARGIN,
            MARGIN,
            BODY_WIDTH,
            BODY_HEIGHT,
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
        )
        document.addPageTemplates([PageTemplate(id="requirements", frames=[frame])])
        try:
            document.build(story)
        except LayoutError:
            raise RequirementPdfExportError(
                f"document:{data.document.id}", "内容を欠落させず改ページできません"
            ) from None
        result = buffer.getvalue()
        if len(result) > MAX_PDF_BYTES:
            raise RequirementPdfExportError(
                f"document:{data.document.id}", "PDFが応答サイズの上限を超えます"
            )
        return result
