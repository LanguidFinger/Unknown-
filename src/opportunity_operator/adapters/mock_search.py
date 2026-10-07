from __future__ import annotations

from dataclasses import dataclass, field

from ..ports.search import SearchHit, SearchQuery


@dataclass
class MockSearch:
    results: dict[str, list[SearchHit]] = field(default_factory=dict)
    name: str = "mock-search"
    queries: list[str] = field(default_factory=list)

    def search(self, query: SearchQuery) -> list[SearchHit]:
        self.queries.append(query.text)
        return list(self.results.get(query.text, []))[: query.max_results]
