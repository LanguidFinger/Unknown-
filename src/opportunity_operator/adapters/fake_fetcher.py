from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..ports.fetcher import FetchResult


@dataclass
class FakeFetcher:
    pages: dict[str, tuple[str, bytes]] = field(default_factory=dict)  # url -> (content_type, body)
    requested: list[str] = field(default_factory=list)

    def _result(self, url: str, ctype: str, body: bytes) -> FetchResult:
        return FetchResult(url, url, 200, ctype, body, datetime.now(UTC).isoformat(), hashlib.sha256(body).hexdigest())

    def get(self, url: str) -> FetchResult:
        self.requested.append(url)
        ctype, body = self.pages[url]
        return self._result(url, ctype, body)

    def post_json(self, url: str, body: Mapping[str, Any]) -> FetchResult:
        self.requested.append(url + " " + json.dumps(body, sort_keys=True))
        ctype, resp = self.pages[url]
        return self._result(url, ctype, resp)
