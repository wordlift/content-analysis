"""HTML to clean text with a character offset map.

The map lets a caller take a mention found in the clean text and inject an
annotation at the right place in the original markup. Requires
`pip install ".[html]"`.
"""
from __future__ import annotations

from dataclasses import dataclass

BLOCK_TAGS = {
    "p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "td", "th", "blockquote", "section", "article",
}


@dataclass(frozen=True)
class OffsetMapping:
    clean_start: int
    clean_end: int
    html_start: int
    html_end: int


def extract_text_with_offsets(html: str) -> tuple[str, list[OffsetMapping]]:
    from bs4 import BeautifulSoup, NavigableString

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style", "noscript", "head"]):
        tag.decompose()

    parts: list[str] = []
    mappings: list[OffsetMapping] = []
    clean_pos = 0
    html_cursor = 0

    def walk(element) -> None:
        nonlocal clean_pos, html_cursor
        if isinstance(element, NavigableString):
            text = str(element)
            if not text.strip():
                return
            html_start = html.find(text, html_cursor)
            if html_start == -1:
                html_start = html_cursor
            html_end = html_start + len(text)
            html_cursor = html_end
            mappings.append(OffsetMapping(clean_pos, clean_pos + len(text), html_start, html_end))
            parts.append(text)
            clean_pos += len(text)
            return
        for child in getattr(element, "children", ()):
            walk(child)
        if getattr(element, "name", None) in BLOCK_TAGS and parts and not parts[-1].endswith("\n"):
            parts.append("\n")
            clean_pos += 1

    walk(soup)
    return "".join(parts).strip(), mappings


def to_html_span(clean_start: int, clean_end: int, mappings: list[OffsetMapping]) -> tuple[int, int] | None:
    """Map a clean-text span back to HTML offsets when it lies inside one text node."""
    for m in mappings:
        if m.clean_start <= clean_start and clean_end <= m.clean_end:
            delta = m.html_start - m.clean_start
            return clean_start + delta, clean_end + delta
    return None
