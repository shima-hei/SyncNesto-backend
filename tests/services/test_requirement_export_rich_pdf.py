"""新PDFの描画境界・停止時の履歴・既存出力の契約を検証する。"""

import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from reportlab.pdfbase.pdfdoc import PDFDocument
from reportlab.platypus import KeepTogether, LongTable, Paragraph

from app.core.config import settings
from app.core.exceptions import RequirementPdfExportError
from app.services import requirement_export_rich_pdf as pdf
from tests.services.test_requirement_export_html import (
    LEGACY_SHA256,
    export,
    make_service,
)

pytestmark = pytest.mark.no_db


@pytest.fixture
def saved(monkeypatch):
    """既存HTML契約と同じ保存済み入力を新PDF経路で使う。"""
    monkeypatch.setattr(settings, "requirement_rich_pdf_enabled", True)
    return make_service(monkeypatch)


def test_default_flag_is_off():
    """本番設定を変えず、PDFを有効化しない初期値を維持する。"""
    from app.core.config import Settings

    assert (
        Settings.__dataclass_fields__["requirement_rich_pdf_enabled"].default is False
    )


@pytest.mark.parametrize("kind", ["markdown", "html", "pdf"])
def test_disabled_pdf_preserves_frozen_legacy_bytes(monkeypatch, kind):
    """新PDFがOFFなら、固定済みのMarkdown/HTML/PDFを一切変えない。"""
    monkeypatch.setattr(settings, "requirement_rich_pdf_enabled", False)
    monkeypatch.setattr(settings, "requirement_rich_html_enabled", False)
    service, _, _, _ = make_service(monkeypatch)
    content = export(service, kind, include_comments=True, include_change_logs=True)
    assert hashlib.sha256(content.encode()).hexdigest() == LEGACY_SHA256[kind]


def test_embedded_real_regular_and_bold_and_success_history(saved):
    """両書体とToUnicodeを埋め込み、成功後だけ従来の出力履歴を追加する。"""
    service, _, _, record = saved
    content = base64.b64decode(
        export(service, "pdf", include_comments=True, include_change_logs=True)
    )
    assert content.startswith(b"%PDF-")
    assert b"BIZUDGothic-Regular" in content and b"BIZUDGothic-Bold" in content
    assert b"/FontFile2" in content and b"/ToUnicode" in content
    assert b"/JavaScript" not in content and b"/URI" not in content
    record.assert_called_once()
    assert record.call_args.kwargs["new_value"]["format"] == "pdf"


def test_supplementary_character_uses_utf16_pair_in_to_unicode():
    """字形だけでなくコピー/検索用CMapも原文の補助面文字へ対応させる。"""
    doc = PDFDocument()
    font = pdf.UnicodeTrueTypeFont(
        "SupplementaryCheck", str(pdf._FONT_DIRECTORY / "BIZUDGothic-Regular.ttf")
    )
    font.splitString("𠮟", doc)
    font.addObjects(doc)
    maps = [
        vars(v)["content"]
        for k, v in doc.idToObject.items()
        if k.startswith("toUnicodeCMap:")
    ]
    assert any("<D842DF9F>" in value for value in maps)
    assert not any("<20B9F>" in value for value in maps)


@pytest.mark.parametrize("bad", ["𠮷", "😀", "e\u0301", "葛\U000e0100", "\x00"])
def test_missing_or_unshaped_character_is_not_substituted(saved, bad):
    """cmap欠字・結合/IVSは原文位置を返し、本文や成功履歴を漏らさない。"""
    service, _, items, record = saved
    items[1].description = "非公開本文" + bad
    with pytest.raises(RequirementPdfExportError) as exc:
        export(service, "pdf")
    assert "requirement:3 / description" in exc.value.message
    assert "offset:" in exc.value.message and "U+" in exc.value.message
    assert "非公開本文" not in exc.value.message
    record.assert_not_called()


def test_wide_table_is_rejected_without_changing_orientation(saved):
    """幅超過を縮小/列分割/旧PDFへのfallbackで隠さない。"""
    service, _, items, record = saved
    items[1].description = "|" + " 項目 |" * 12 + "\n|" + " --- |" * 12
    with pytest.raises(RequirementPdfExportError, match="表が本文幅"):
        export(service, "pdf")
    record.assert_not_called()


@pytest.mark.parametrize("columns", [2, 4, 6])
def test_standard_table_retains_all_columns_and_repeat_header(columns):
    """標準表は順序/strong/codeを保持し、再掲と行内継続を明示する。"""
    builder = pdf.PdfBuilder()
    content = "|" + " 見出し |" * columns + "\n|" + " --- |" * columns
    content += "\n|" + " **太字** と `a\\|b` |" * columns
    table = builder.markdown(content, "requirement:1 / description")[0]
    assert isinstance(table, LongTable)
    assert table.repeatRows == 1 and table.splitInRow == 1
    rows = vars(table)["_cellvalues"]
    assert len(rows) == 2 and len(rows[0]) == columns
    assert rows[1][0].getPlainText() == "太字 と a|b"
    assert pdf.FONT_BOLD in [f.fontName for f in rows[1][0].frags]
    assert table.wrap(pdf.BODY_WIDTH, pdf.BODY_HEIGHT)[0] <= pdf.BODY_WIDTH


def test_raw_markup_resources_and_unsupported_syntax_remain_inert():
    """Paragraphへ利用者XMLを渡さず、画像/Mermaid/未対応参照も文字で残す。"""
    builder = pdf.PdfBuilder()
    source = (
        '<font color="[[1/0]]">raw</font>\n\n'
        '![画像](file:///private/data "題")\n\n'
        "```mermaid\ngraph TD; A-->B;\n```\n\n"
        "[ref]: https://example.test\n*斜体記号*"
    )
    paragraphs = builder.markdown(source, "document:1")
    text = "".join(p.getPlainText() for p in paragraphs if isinstance(p, Paragraph))
    assert '<font color="[[1/0]]">raw</font>' in text
    assert '![画像](file:///private/data "題")' in text
    assert "graph TD; A-->B;" in text and "[ref]:" in text and "*斜体記号*" in text


def test_code_keeps_indentation_and_repeated_spaces_without_unicode_replacement():
    """コードの空白をNBSPや通常段落の空白圧縮へ置き換えない。"""
    builder = pdf.PdfBuilder()
    fenced = builder.markdown("```python\n  indent  spaces\n```", "document:1")
    assert isinstance(fenced[0], Paragraph)
    assert "  indent  spaces" in fenced[0].getPlainText()
    inline = builder.markdown("code `a  b` end", "document:1")
    assert isinstance(inline[0], Paragraph)
    assert "a  b" in inline[0].getPlainText()


def test_short_requirement_keeps_model_boundary_and_long_requirement_splits(saved):
    """本文h4に境界を委ねず、短いREQだけを同頁保持の対象にする。"""
    service, _, items, _ = saved
    builder = pdf.PdfBuilder()
    short = builder.requirements([items[1]], "document:1", service._format_value)
    assert isinstance(short[0], KeepTogether)
    items[1].description = "長い本文。" * 2500
    long = builder.requirements([items[1]], "document:1", service._format_value)
    assert not any(isinstance(p, KeepTogether) for p in long)


def test_oversized_row_can_continue_and_repeated_requests_do_not_share_state(saved):
    """一頁超行の実PDF生成と、同時要求間の文書内容分離を確認する。"""
    service, _, items, _ = saved
    items[1].description = (
        "| 見出し |\n| --- |\n| " + "長いセル。" * 1000 + " TALL-END |"
    )
    data = service._get_export_data(
        None,
        document=service.document_repository.get_by_id(None, 1),
        include_comments=False,
        include_change_logs=False,
        section_ids=None,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        contents = list(
            pool.map(
                lambda _: pdf.render_document(data, service._format_value), range(2)
            )
        )
    assert all(content.count(b"/Type /Page\n") > 2 for content in contents)


def test_payload_and_unbreakable_header_fail_before_success_history(saved, monkeypatch):
    """サイズ上限と分割不能なヘッダーを成功扱いにしない。"""
    service, _, items, record = saved
    monkeypatch.setattr(pdf, "MAX_PDF_BYTES", 1)
    with pytest.raises(RequirementPdfExportError, match="応答サイズ"):
        export(service, "pdf")
    record.assert_not_called()
    items[1].description = "| " + "長い見出し" * 1000 + " |\n| --- |\n| 本文 |"
    with pytest.raises(RequirementPdfExportError, match="ヘッダー"):
        export(service, "pdf")
    record.assert_not_called()
