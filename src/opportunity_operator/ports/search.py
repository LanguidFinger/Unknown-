from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SearchQuery:
    text: str
    max_results: int = 10


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    snippet: str
    rank: int


class SearchProvider(Protocol):
    """Web search only. There is deliberately no local-corpus/retrieval variant of this port."""

    name: str

    def search(self, query: SearchQuery) -> list[SearchHit]: ...
