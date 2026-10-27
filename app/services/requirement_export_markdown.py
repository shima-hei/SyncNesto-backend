"""HTMLとPDFが共有する、外部資源を取得しないMarkdownの解析設定。"""

from collections.abc import Callable

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML, RendererProtocol


def export_markdown_parser(
    renderer_cls: Callable[[MarkdownIt], RendererProtocol] = RendererHTML,
) -> MarkdownIt:
    """作業100の解析契約を変更せず、描画先だけを選べるようにする。"""
    parser = MarkdownIt(
        "commonmark", {"html": False, "breaks": True}, renderer_cls=renderer_cls
    ).enable("table")
    parser.disable("reference")
    return parser
