"""Deny-term tripwire (defense in depth; NOT the primary isolation control).

The primary control is architectural: the system has no way to read restricted material at all.
This filter is a last-line check that blocks outbound prompts, search queries, URLs, API bodies
and model outputs that contain an owner-supplied deny-term. Matching is intentionally
aggressive (case/diacritics/leetspeak/separator-insensitive); false positives block safely.
It is a tripwire, not a guarantee: paraphrase and encoding tricks can evade any text filter.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from collections.abc import Iterable
from typing import Protocol
from urllib.parse import unquote

from ..errors import DisclosureBlocked

_LEET = str.maketrans({"0": "o", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
MIN_TERM_LEN = 4


def squash(text: str) -> str:
    for _ in range(2):
        text = unquote(text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.casefold().translate(_LEET)
    return _NON_ALNUM.sub("", text)


def term_hash(term: str) -> str:
    return hashlib.sha256(squash(term).encode()).hexdigest()


class IncidentRecorder(Protocol):
    def incident(self, channel: str, term_hash: str, context_sha: str) -> None: ...


class DisclosureFilter:
    def __init__(self, terms: Iterable[str] = ()) -> None:
        self._by_squash: dict[str, str] = {}
        for t in terms:
            s = squash(t)
            if len(s) < MIN_TERM_LEN:
                raise ValueError(f"deny-term too short/ambiguous (min {MIN_TERM_LEN} alphanumerics)")
            self._by_squash[s] = hashlib.sha256(s.encode()).hexdigest()

    @classmethod
    def from_connection(cls, context_conn: sqlite3.Connection) -> DisclosureFilter:
        terms: list[str] = []
        for row in context_conn.execute("SELECT deny_terms FROM restricted_stub"):
            terms.extend(json.loads(row["deny_terms"]))
        return cls(terms)

    def __len__(self) -> int:
        return len(self._by_squash)

    def hits(self, text: str) -> frozenset[str]:
        """Hashes of every deny-term present in `text` (never the matched text itself)."""
        if not self._by_squash:
            return frozenset()
        s = squash(text)
        return frozenset(h for term, h in self._by_squash.items() if term in s)

    def check(
        self,
        text: str,
        *,
        channel: str,
        halt: bool = True,
        ignore: frozenset[str] = frozenset(),
        recorder: IncidentRecorder | None = None,
    ) -> None:
        found = self.hits(text) - ignore
        if not found:
            return
        first = sorted(found)[0]
        if recorder is not None:
            recorder.incident(channel, first, hashlib.sha256(text.encode()).hexdigest())
        raise DisclosureBlocked(channel, first, halt=halt)
