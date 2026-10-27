"""要件出力の日本語幅と改ページの回帰を検証する。"""

import pytest

from app.services.requirement_export import RequirementExportService
from app.services.requirement_export_pdf import (
    FONT_SIZE,
    LINE_CHARACTERS,
    MARGIN,
    PAGE_LINES,
    PAGE_WIDTH,
    paginate,
    text_lines,
)

pytestmark = pytest.mark.no_db


def test_pdf_wraps_japanese_and_long_tokens_inside_page():
    """日本語・URL・補助面文字を切り捨てず、既存CID幅で用紙内に収める。"""
    content = "日本語の長文" * 30 + "https://example.test/" + "x" * 130 + "🧪" * 30
    lines = text_lines(content)
    assert "".join(lines) == content
    assert all(
        len(line.encode("utf-16-be")) // 2 * FONT_SIZE <= PAGE_WIDTH - 2 * MARGIN
        for line in lines
    )
    assert LINE_CHARACTERS < 72


def test_short_requirement_moves_together_at_page_boundary():
    """見出し・属性・説明・理由・受入条件を境界で分断しない。"""
    prefix = "\n".join(f"前段{i}" for i in range(PAGE_LINES - 2))
    requirement = (
        "#### REQ-001 見出し\n\n- 種別: functional\n\n説明\n\n"
        "理由:\n理由本文\n\n受け入れ条件:\n条件本文"
    )
    pages = paginate(prefix + "\n\n" + requirement)
    assert len(pages) == 2
    assert "REQ-001 見出し" not in pages[0]
    assert all(value in pages[1] for value in ["説明", "理由本文", "条件本文"])


def test_long_requirement_splits_without_losing_text_or_orphaning_labels():
    """一頁超のREQは分割し、受入条件ラベルを単独で頁末へ残さない。"""
    content = (
        "#### REQ-LONG\n\n"
        + "\n".join(f"本文{i}" for i in range(PAGE_LINES - 4))
        + "\n\n受け入れ条件:\n条件本文\n"
        + "長い続き" * 800
    )
    pages = paginate(content)
    assert len(pages) > 2
    assert all(len(page) <= PAGE_LINES for page in pages)
    assert all(page[-1] != "受け入れ条件:" for page in pages)
    expected = "".join(line for line in text_lines(content) if line)
    assert "".join(line for page in pages for line in page if line) == expected


def test_html_print_keeps_requirement_body_and_escapes_raw_html():
    """REQ単位の改ページ指定を持ち、本文HTMLを実行可能にしない。"""
    html = RequirementExportService()._build_html(
        "# 文書\n\n#### REQ-001\n\n<script>alert(1)</script>\n\n説明\n\n## 未決事項"
    )
    assert 'class="export-requirement"' in html
    assert html.index("説明") < html.index("</section>") < html.index("未決事項")
    assert "@media print" in html and "break-inside:avoid-page" in html
    assert "<script>" not in html and "&lt;script&gt;" in html
