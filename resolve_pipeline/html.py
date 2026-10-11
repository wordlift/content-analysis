"""HTML to clean text with a character offset map.

The map lets a caller take a mention found in the clean text and inject an
annotation at the right place in the original markup. Offsets come from the
parser's own source positions, so every mapped character points at the bytes
it was decoded from: an entity such as `&amp;` maps its one clean character to
the five source characters, text inside attributes, scripts, styles and
comments is never matched, and a span that cannot be placed is reported as
such rather than guessed.

Policy for the clean text:
- text nodes are kept as written; `&amp;`, `&#x27;` and friends are decoded;
- a whitespace-only run between elements becomes one space, mapped to its
  source, so `<span>Apple</span> <span>Rome</span>` reads "Apple Rome";
- a block element boundary adds one newline, which has no source position;
- the text is not trimmed: positions in it are the positions in the map.

Standard library only; no optional dependency.
"""
from __future__ import annotations

import html as _html
from dataclasses import dataclass
from html.parser import HTMLParser

BLOCK_TAGS = {
    "p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "td", "th", "tr", "blockquote", "section", "article", "ul", "ol", "table", "pre", "hr",
}
SKIP_TAGS = {"script", "style", "noscript", "title", "template", "svg", "math"}  # not `head`: its end tag is optional
VOID_TAGS = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr"}


@dataclass(frozen=True)
class OffsetMapping:
    clean_start: int
    clean_end: int
    html_start: int
    html_end: int

    @property
    def one_to_one(self) -> bool:
        return self.clean_end - self.clean_start == self.html_end - self.html_start


class _Extractor(HTMLParser):
    def __init__(self, source: str):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.parts: list[str] = []
        self.mappings: list[OffsetMapping] = []
        self.clean = 0
        self.skipping = 0
        self.line_starts = [0]
        for i, ch in enumerate(source):
            if ch == "\n":
                self.line_starts.append(i + 1)

    def _pos(self) -> int:
        line, col = self.getpos()
        return self.line_starts[line - 1] + col

    def _emit(self, text: str, html_start: int, html_end: int) -> None:
        self.mappings.append(OffsetMapping(self.clean, self.clean + len(text), html_start, html_end))
        self.parts.append(text)
        self.clean += len(text)

    def _block(self) -> None:
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")
            self.clean += 1

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skipping += 1
        if tag in BLOCK_TAGS:
            self._block()

    def handle_startendtag(self, tag, attrs):
        if tag in BLOCK_TAGS:
            self._block()

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self.skipping:
            self.skipping -= 1
        if tag in BLOCK_TAGS and tag not in VOID_TAGS:
            self._block()

    def handle_data(self, data):
        if self.skipping or not data:
            return
        start = self._pos()
        if self.source[start:start + len(data)] != data:
            # Should not happen with convert_charrefs=False; refuse to guess.
            return
        if not data.strip():
            if self.parts and not self.parts[-1][-1].isspace():
                self._emit(" ", start, start + len(data))
            return
        self._emit(data, start, start + len(data))

    def _ref(self, raw: str) -> None:
        if self.skipping:
            return
        start = self._pos()
        end = start + len(raw)
        if self.source[end:end + 1] == ";":
            end += 1
        # Decode the reference as written: an unknown name such as the `&T` of
        # "AT&T" stays as it is, never gains a `;`.
        self._emit(_html.unescape(self.source[start:end]), start, end)

    def handle_entityref(self, name):
        self._ref("&" + name)

    def handle_charref(self, name):
        self._ref("&#" + name)


def extract_text_with_offsets(source: str) -> tuple[str, list[OffsetMapping]]:
    """Clean text and the map from its positions to the markup's."""
    p = _Extractor(source)
    p.feed(source)
    p.close()
    return "".join(p.parts), p.mappings


def to_html_span(clean_start: int, clean_end: int, mappings: list[OffsetMapping]) -> tuple[int, int] | None:
    """The markup span behind a clean-text span, or None when it cannot be placed.

    The span may run across several text nodes and entities as long as every
    character in it has a source position; a span that crosses a synthetic
    newline (a block boundary) is unmappable.
    """
    if clean_end <= clean_start:
        return None
    first = next((m for m in mappings if m.clean_start <= clean_start < m.clean_end), None)
    last = next((m for m in mappings if m.clean_start < clean_end <= m.clean_end), None)
    if first is None or last is None:
        return None
    covered = clean_start
    for m in mappings:
        if m.clean_start <= covered < m.clean_end:
            covered = m.clean_end
        if covered >= clean_end:
            break
    if covered < clean_end:
        return None
    html_start = first.html_start + (clean_start - first.clean_start) if first.one_to_one else first.html_start
    html_end = last.html_start + (clean_end - last.clean_start) if last.one_to_one else last.html_end
    return html_start, html_end
