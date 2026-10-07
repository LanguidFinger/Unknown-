"""Text canonicalisation and verbatim-quote verification.

Every fact the system records must carry a quote that is found *verbatim* (modulo whitespace,
quote-style and dash-style differences) in the stored snapshot text. Offsets into the canonical
text are stored so an auditor can re-verify later without trusting the original run.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser

_ZERO_WIDTH = dict.fromkeys(map(ord, "­​‌‍⁠﻿"), None)
_PUNCT = {
    ord("“"): '"', ord("”"): '"', ord("„"): '"',
    ord("‘"): "'", ord("’"): "'", ord("‚"): "'",
    ord("–"): "-", ord("—"): "-", ord("−"): "-",
    ord("…"): "...", ord(" "): " ",
}
_WS = re.compile(r"\s+")

MIN_QUOTE_CHARS = 15


def canonical_text(text: str) -> str:
    """NFKC, strip zero-width chars, unify quotes/dashes, collapse whitespace."""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_ZERO_WIDTH).translate(_PUNCT)
    return _WS.sub(" ", text).strip()


@dataclass(frozen=True)
class QuoteMatch:
    start: int
    end: int


def find_quote(canonical_snapshot: str, quote: str, *, min_chars: int = MIN_QUOTE_CHARS) -> QuoteMatch | None:
    """Return offsets of `quote` in the canonical snapshot text, or None.

    The quote must be contiguous (no ellipsis stitching) and at least `min_chars` long after
    canonicalisation, so trivially short or fabricated fragments cannot "verify".
    """
    q = canonical_text(quote)
    if len(q) < min_chars or "..." in q:
        return None
    idx = canonical_snapshot.find(q)
    if idx < 0:
        return None
    return QuoteMatch(idx, idx + len(q))


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template", "head"}
    _BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return "".join(parser.parts)
