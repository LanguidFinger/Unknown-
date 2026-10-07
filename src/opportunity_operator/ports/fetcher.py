from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str
    status: int
    content_type: str
    body: bytes
    retrieved_at: str
    sha256: str


class Fetcher(Protocol):
    def get(self, url: str) -> FetchResult: ...

    def post_json(self, url: str, body: Mapping[str, Any]) -> FetchResult: ...
