"""HTML原本を隔離プレビューで描画できる範囲へ変換する。"""

import base64
import binascii
import html
import re
from html.parser import HTMLParser
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from app.schemas.design_content import HtmlMockup

ELEMENTS = frozenset(
    """
    address article aside b blockquote br button caption code col colgroup dd details
    div dl dt em fieldset figcaption figure footer h1 h2 h3 h4 h5 h6 header hr i img
    input
    label legend li main mark nav ol optgroup option p pre s section select small span
    strong sub summary sup table tbody td textarea tfoot th thead tr u ul
""".split()
)
VOID = {"br", "col", "hr", "img", "input"}
ATTRIBUTES = frozenset(
    """
    alt aria-hidden aria-label class colspan disabled for height id max maxlength min
    minlength multiple name open placeholder readonly required role rowspan scope
    selected size step title type value width
""".split()
)
RASTER_URL = re.compile(r"data:image/(png|jpeg|webp);base64,([a-zA-Z0-9+/=]+)\Z")
CSS_RESOURCE = re.compile(r"[@<>\\]|url\s*\(|image-set\s*\(|/\*", re.IGNORECASE)
CSP = (
    "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src data:; "
    "connect-src 'none'; font-src 'none'; frame-src 'none'; object-src 'none'; "
    "form-action 'none'; base-uri 'none'"
)


def safe_raster_url(value: str) -> bool:
    """小さな実画像のみを許可する。SVG・外部・内部オブジェクトURLは通さない。"""
    match = RASTER_URL.fullmatch(value)
    if match is None or len(match[2]) > 135000:
        return False
    try:
        with Image.open(BytesIO(base64.b64decode(match[2], validate=True))) as image:
            if (
                image.width * image.height > 4_000_000
                or image.format != match[1].upper()
            ):
                return False
            image.verify()
    except (
        UnidentifiedImageError,
        binascii.Error,
        OSError,
        SyntaxError,
        ValueError,
        Image.DecompressionBombError,
    ):
        return False
    return True


class MockupParser(HTMLParser):
    """許可する要素と属性からプレビュー本文を再構成する。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self.skip: str | None = None

    def handle_starttag(self, tag, attrs):
        if self.skip is not None:
            return
        if tag in {"script", "style", "iframe", "object", "template", "svg", "math"}:
            self.skip = tag
            return
        if tag not in ELEMENTS:
            return
        clean = []
        for key, value in attrs:
            value = value or ""
            if key in ATTRIBUTES or (
                tag == "img" and key == "src" and safe_raster_url(value)
            ):
                clean.append(f' {key}="{html.escape(value, quote=True)}"')
        self.output.append(f"<{tag}{''.join(clean)}>")

    def handle_endtag(self, tag):
        if self.skip is not None:
            if tag == self.skip:
                self.skip = None
            return
        if tag in ELEMENTS and tag not in VOID:
            self.output.append(f"</{tag}>")

    def handle_data(self, data):
        if self.skip is None:
            self.output.append(html.escape(data, quote=False))


def mockup_document(mockup: HtmlMockup) -> tuple[str, bool]:
    """スクリプト・ナビゲーション・通信を許可しない文書を生成する。"""
    parser = MockupParser()
    parser.feed(mockup.html)
    parser.close()
    css_blocked = bool(CSS_RESOURCE.search(mockup.css))
    style = "" if css_blocked else mockup.css
    return (
        f'<!doctype html><html><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
        f'<meta name="referrer" content="no-referrer"><style>{style}</style></head>'
        f"<body>{''.join(parser.output)}</body></html>",
        css_blocked,
    )
