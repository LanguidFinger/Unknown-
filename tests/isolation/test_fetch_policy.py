"""The network surface is read-only, public-internet-only and credential-free."""

from __future__ import annotations

import httpx
import pytest

from opportunity_operator.adapters.http_fetcher import HttpFetcher
from opportunity_operator.config import Settings
from opportunity_operator.errors import FetchRefused

PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731


def make(handler=None, *, resolver=PUBLIC, sleep=lambda s: None, mono=None, **settings_kw):
    seen: list[httpx.Request] = []

    def default(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=b"<p>ok</p>")

    def wrapped(request):
        seen.append(request)
        return (handler or default)(request) if handler else httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>ok</p>")

    s = Settings(min_host_interval_s=0, **settings_kw)
    kw = {"monotonic": mono} if mono else {}
    return HttpFetcher(s, transport=httpx.MockTransport(wrapped), resolver=resolver, sleep=sleep, **kw), seen


@pytest.mark.parametrize("url", [
    "http://programs.example.gov/a", "file:///etc/passwd", "ftp://programs.example.gov/a", "gopher://x.example.gov/",
    "https://localhost/a", "https://127.0.0.1/a", "https://[::1]/a", "https://10.0.0.5/a", "https://192.168.1.1/a",
    "https://169.254.169.254/latest/meta-data", "https://2130706433/", "https://user:pw@programs.example.gov/a",
    "https://programs.example.gov:8443/a", "https://printer.local/a", "https://intranet/a", "https://svc.internal/a",
    "https://github.com/org/private-repo", "https://api.github.com/repos/org/private/contents", "https://raw.githubusercontent.com/o/r/main/x",
    "https://gitlab.com/o/r", "https://bitbucket.org/o/r", "https://docs.google.com/document/d/abc", "https://drive.google.com/file/d/x",
    "https://www.notion.so/page", "https://acme.atlassian.net/wiki", "https://acme.sharepoint.com/x", "https://www.dropbox.com/s/x",
    "https://sub.docs.google.com/x", "", "https:///nohost",
])
def test_urls_refused_without_any_request(url):
    f, seen = make()
    with pytest.raises(FetchRefused):
        f.get(url)
    assert seen == []


def test_explicit_extra_allow_overrides_default_block():
    f, seen = make(extra_allow_hosts=("github.com",))
    assert f.get("https://github.com/org/public-page").status == 200 and seen


@pytest.mark.parametrize("addrs", [["10.1.2.3"], ["127.0.0.1"], ["169.254.169.254"], ["::1"], ["93.184.216.34", "10.0.0.9"], ["fc00::1"]])
def test_hosts_resolving_to_non_public_addresses_refused(addrs):
    f, seen = make(resolver=lambda h: addrs)
    with pytest.raises(FetchRefused):
        f.get("https://innocent.example.gov/a")
    assert seen == []


def test_unresolvable_host_refused_in_direct_mode_but_delegated_in_proxy_mode():
    def boom(host):
        raise OSError("no dns")

    f, _ = make(resolver=boom)
    with pytest.raises(FetchRefused):
        f.get("https://programs.example.gov/a")
    f2, seen = make(resolver=boom, egress_mode="proxy")
    assert f2.get("https://programs.example.gov/a").status == 200 and seen


def test_redirects_are_revalidated_each_hop():
    for target in ("http://programs.example.gov/x", "https://127.0.0.1/x", "https://github.com/o/r", "file:///etc/passwd"):
        f, seen = make(lambda req, t=target: httpx.Response(302, headers={"location": t}))
        with pytest.raises(FetchRefused):
            f.get("https://programs.example.gov/start")
        assert len(seen) == 1  # the forbidden hop was never requested


def test_redirect_to_a_public_page_is_followed_and_loops_are_bounded():
    def handler(req):
        if req.url.path == "/start":
            return httpx.Response(301, headers={"location": "/final"})
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>done</p>")

    f, _ = make(handler)
    r = f.get("https://programs.example.gov/start")
    assert r.final_url.endswith("/final") and r.body == b"<p>done</p>"
    loop, seen = make(lambda req: httpx.Response(302, headers={"location": "/again"}))
    with pytest.raises(FetchRefused, match="too many redirects"):
        loop.get("https://programs.example.gov/start")
    assert len(seen) == 4


def test_no_credentials_or_cookies_are_ever_sent(monkeypatch):
    monkeypatch.setenv("HTTP_AUTHORIZATION", "Bearer SECRET")
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, headers={"content-type": "text/html", "set-cookie": "sid=abc123; Path=/"}, content=b"<p>x</p>")

    f, _ = make(handler)
    f.get("https://programs.example.gov/a")
    f.get("https://programs.example.gov/b")
    for req in calls:
        assert "authorization" not in req.headers and "cookie" not in req.headers
        assert req.headers["user-agent"].startswith("OpportunityOperator/")


def test_size_cap_and_content_type_allowlist():
    big, _ = make(lambda r: httpx.Response(200, headers={"content-type": "text/html"}, content=b"x" * 5000), max_fetch_bytes=1000)
    with pytest.raises(FetchRefused, match="size"):
        big.get("https://programs.example.gov/a")
    for ctype in ("application/zip", "application/octet-stream", "application/x-msdownload", "image/png", ""):
        f, _ = make(lambda r, c=ctype: httpx.Response(200, headers={"content-type": c}, content=b"MZ"))
        with pytest.raises(FetchRefused, match="content type"):
            f.get("https://programs.example.gov/a")


def test_post_json_only_to_allow_listed_api_hosts_and_never_follows_redirects():
    f, seen = make(lambda r: httpx.Response(200, headers={"content-type": "application/json"}, content=b"{}"))
    assert f.post_json("https://api.grants.gov/v1/api/search2", {"keyword": "ai"}).status == 200
    assert seen[0].method == "POST" and "authorization" not in seen[0].headers
    for url in ("https://programs.example.gov/api", "https://evil.example.com/collect", "http://api.grants.gov/x", "https://github.com/api"):
        with pytest.raises(FetchRefused):
            f.post_json(url, {"k": "v"})
    redirecting, _ = make(lambda r: httpx.Response(307, headers={"location": "https://evil.example.com/"}))
    with pytest.raises(FetchRefused, match="redirect"):
        redirecting.post_json("https://api.grants.gov/v1/api/search2", {"k": "v"})
    with pytest.raises(FetchRefused, match="too large"):
        f.post_json("https://api.grants.gov/v1/api/search2", {"k": "x" * 70_000})


def test_get_requests_never_carry_a_body():
    f, seen = make()
    f.get("https://programs.example.gov/a")
    assert seen[0].method == "GET" and seen[0].content == b""


def test_per_host_rate_limit():
    sleeps: list[float] = []
    clock = {"t": 100.0}
    s = Settings(min_host_interval_s=2.0)
    f = HttpFetcher(s, transport=httpx.MockTransport(lambda r: httpx.Response(200, headers={"content-type": "text/html"}, content=b"x")),
                    resolver=PUBLIC, sleep=lambda d: (sleeps.append(d), clock.__setitem__("t", clock["t"] + d)), monotonic=lambda: clock["t"])
    f.get("https://programs.example.gov/a")
    f.get("https://programs.example.gov/b")
    assert sleeps and sleeps[0] == pytest.approx(2.0)
    n = len(sleeps)
    f.get("https://other.example.gov/a")
    assert len(sleeps) == n  # different host: no wait
