"""The real, policy-enforced HTTP fetcher.

Read-only by construction: HTTPS only, port 443, no credentials or cookies, no userinfo in URLs,
no IP literals, no localhost/internal names, non-public DNS answers refused (direct mode),
code-hosting / private-document hosts blocked by default, redirects re-validated per hop, hard
size and content-type limits, per-host rate limiting. POST exists only for JSON search queries
to explicitly allow-listed API hosts and never follows redirects.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from ..config import Settings
from ..errors import FetchRefused
from ..ports.fetcher import FetchResult

USER_AGENT = "OpportunityOperator/0.0.1 (local research tool; read-only)"

# Where private repositories, documents and chats live. Blocked unless explicitly allowed.
DEFAULT_BLOCKED_SUFFIXES = (
    "github.com", "githubusercontent.com", "gitlab.com", "bitbucket.org", "sourcehut.org",
    "notion.so", "notion.site", "dropbox.com", "docs.google.com", "drive.google.com",
    "onedrive.live.com", "1drv.ms", "sharepoint.com", "box.com", "atlassian.net",
    "slack.com", "figma.com", "airtable.com", "evernote.com",
)
_INTERNAL_SUFFIXES = (".local", ".internal", ".lan", ".localdomain", ".home.arpa", ".corp", ".intranet")
_ALLOWED_TYPES = ("text/html", "application/xhtml+xml", "application/json", "text/plain", "application/xml", "text/xml", "application/pdf")
_REDIRECTS = {301, 302, 303, 307, 308}

Resolver = Callable[[str], list[str]]


def _system_resolver(host: str) -> list[str]:
    return sorted({ai[4][0] for ai in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)})


def _host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == s or host.endswith("." + s) for s in suffixes)


class HttpFetcher:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        resolver: Resolver | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        max_redirects: int = 3,
        timeout_s: float = 20.0,
    ) -> None:
        self._s = settings
        self._transport = transport
        self._resolver = resolver or _system_resolver
        self._sleep = sleep
        self._mono = monotonic
        self._max_redirects = max_redirects
        self._timeout = timeout_s
        self._last_hit: dict[str, float] = {}
        self._extra_allowed = tuple(h.lower() for h in settings.extra_allow_hosts)

    # ---- policy -------------------------------------------------------------
    def validate_url(self, url: str, *, api: bool = False) -> str:
        parts = urlsplit(url)
        if parts.scheme != "https":
            raise FetchRefused("only https URLs are allowed")
        if parts.username or parts.password or "@" in parts.netloc:
            raise FetchRefused("credentials in URL refused")
        if parts.port not in (None, 443):
            raise FetchRefused("non-standard port refused")
        host = (parts.hostname or "").lower().rstrip(".")
        if not host:
            raise FetchRefused("missing host")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise FetchRefused("IP-literal hosts refused")
        if "." not in host or host == "localhost" or host.endswith(_INTERNAL_SUFFIXES):
            raise FetchRefused("internal host name refused")
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise FetchRefused("invalid host name") from exc
        if api:
            if host not in self._s.api_allow_hosts:
                raise FetchRefused("host is not an allow-listed API host")
        elif _host_matches(host, DEFAULT_BLOCKED_SUFFIXES) and not _host_matches(host, self._extra_allowed):
            raise FetchRefused("code-hosting / private-document hosts are blocked by default")
        self._check_dns(host)
        return host

    def _check_dns(self, host: str) -> None:
        try:
            addrs = self._resolver(host)
        except OSError:
            if self._s.egress_mode == "direct":
                raise FetchRefused("host did not resolve") from None
            return  # proxy mode: the egress proxy resolves and filters
        for a in addrs:
            if not ipaddress.ip_address(a).is_global:
                raise FetchRefused("host resolves to a non-public address")

    def _throttle(self, host: str) -> None:
        wait = self._last_hit.get(host, -1e9) + self._s.min_host_interval_s - self._mono()
        if wait > 0:
            self._sleep(wait)
        self._last_hit[host] = self._mono()

    # ---- requests -----------------------------------------------------------
    def _client(self) -> httpx.Client:
        return httpx.Client(
            transport=self._transport, follow_redirects=False, timeout=self._timeout, trust_env=True,
            headers={"User-Agent": USER_AGENT}, cookies=None,
        )

    def _read(self, resp: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_bytes():
            total += len(chunk)
            if total > self._s.max_fetch_bytes:
                raise FetchRefused("response exceeds size cap")
            chunks.append(chunk)
        return b"".join(chunks)

    def get(self, url: str) -> FetchResult:
        current = url
        for _hop in range(self._max_redirects + 1):
            host = self.validate_url(current)
            self._throttle(host)
            with self._client() as client, client.stream("GET", current, headers={"Accept": "text/html,application/json,application/pdf,text/plain"}) as resp:
                if resp.status_code in _REDIRECTS and "location" in resp.headers:
                    current = urljoin(current, resp.headers["location"])
                    continue
                ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if resp.status_code < 400 and ctype not in _ALLOWED_TYPES:
                    raise FetchRefused(f"content type {ctype!r} not allowed")
                body = self._read(resp)
                return FetchResult(url, current, resp.status_code, ctype, body,
                                   datetime.now(UTC).isoformat(), hashlib.sha256(body).hexdigest())
        raise FetchRefused("too many redirects")

    def post_json(self, url: str, body: Mapping[str, Any]) -> FetchResult:
        host = self.validate_url(url, api=True)
        payload = json.dumps(body, separators=(",", ":")).encode()
        if len(payload) > 65_536:
            raise FetchRefused("API request body too large")
        self._throttle(host)
        with self._client() as client:
            resp = client.post(url, content=payload, headers={"Content-Type": "application/json", "Accept": "application/json"})
            if resp.status_code in _REDIRECTS:
                raise FetchRefused("API redirects are refused")
            ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            data = resp.content
            if len(data) > self._s.max_fetch_bytes:
                raise FetchRefused("response exceeds size cap")
            return FetchResult(url, url, resp.status_code, ctype, data,
                               datetime.now(UTC).isoformat(), hashlib.sha256(data).hexdigest())
