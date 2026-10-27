"""既存の簡易PDFに、用紙幅と本文のまとまりを考慮した行配置を提供する。"""

from dataclasses import dataclass
from textwrap import wrap

PAGE_WIDTH = 595
PAGE_HEIGHT = 842
MARGIN = 50
TEXT_TOP = 790
FONT_SIZE = 10
LINE_HEIGHT = 14
PAGE_LINES = (TEXT_TOP - MARGIN) // LINE_HEIGHT + 1
# 既存CIDフォントはW指定なしの既定幅1000を使用する。
LINE_CHARACTERS = (PAGE_WIDTH - 2 * MARGIN) // FONT_SIZE


@dataclass(frozen=True)
class PdfLine:
    """本文と、直後の行を同頁に残す必要性を保持する。"""

    text: str
    keep_next: bool = False


def text_groups(markdown: str) -> list[list[PdfLine]]:
    """見出しから次の見出しまでのまとまりを、用紙内に収まる行へ変換する。"""
    groups: list[list[PdfLine]] = []
    group: list[PdfLine] = []
    for raw in markdown.splitlines():
        text = raw.strip()
        heading = (
            text.startswith("#") and " " in text and set(text.split(" ", 1)[0]) == {"#"}
        )
        if heading and group:
            groups.append(group)
            group = []
        if heading:
            text = text.split(" ", 1)[1].strip()
        metadata = text.startswith("- ")
        if metadata:
            text = f"・{text[2:]}"
        keep_next = heading or metadata or text.endswith((":", "："))
        for line in wrap(text, width=LINE_CHARACTERS) or [""]:
            part = ""
            units = 0
            for character in line:
                width = len(character.encode("utf-16-be")) // 2
                if units + width > LINE_CHARACTERS:
                    group.append(PdfLine(part, keep_next))
                    part, units = "", 0
                part += character
                units += width
            group.append(PdfLine(part, keep_next))
    if group:
        groups.append(group)
    return groups


def text_lines(markdown: str) -> list[str]:
    """既存の検証用呼び出しと同じ形式で、折返し済みの行を返す。"""
    return [line.text for group in text_groups(markdown) for line in group]


def paginate(markdown: str) -> list[list[str]]:
    """短い要件を同頁に残し、長い本文は段落境界を優先して分割する。"""
    pages: list[list[str]] = []
    page: list[str] = []

    def finish_page() -> None:
        while page and not page[-1]:
            page.pop()
        if page:
            pages.append(page.copy())
        page.clear()

    for source in text_groups(markdown):
        remaining = source.copy()
        while remaining and not remaining[-1].text:
            remaining.pop()
        while remaining and not remaining[0].text:
            remaining.pop(0)
        if len(remaining) <= PAGE_LINES and len(page) + len(remaining) > PAGE_LINES:
            finish_page()
        while remaining:
            room = PAGE_LINES - len(page)
            if len(remaining) <= room:
                page.extend(line.text for line in remaining)
                remaining = []
                if len(page) < PAGE_LINES:
                    page.append("")
                continue
            end = room
            boundaries = [
                index
                for index in range(room)
                if not remaining[index].text and index >= room // 2
            ]
            if boundaries:
                end = boundaries[-1]
            while end and (not remaining[end - 1].text or remaining[end - 1].keep_next):
                end -= 1
            if not end:
                if page:
                    finish_page()
                    continue
                # 用紙より長い見出し/属性列でも本文を失わず前へ進める。
                end = room
            page.extend(line.text for line in remaining[:end])
            remaining = remaining[end:]
            while remaining and not remaining[0].text:
                remaining.pop(0)
            finish_page()
    finish_page()
    return pages or [[]]
