"""The feasibility probe must record what was asked for, safely: shape not values, redirects, limits, reasons."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx

from opportunity_operator.adapters.http_fetcher import HttpFetcher
from opportunity_operator.config import Settings

SPEC = importlib.util.spec_from_file_location("check_sources", Path(__file__).resolve().parents[2] / "scripts/feasibility/check_sources.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def fetcher(handler):
    return HttpFetcher(Settings(min_host_interval_s=0, egress_mode="proxy"), transport=httpx.MockTransport(handler),
                       resolver=lambda h: ["93.184.216.34"], sleep=lambda s: None)


def test_json_shape_records_keys_never_values():
    shape = probe.json_shape({"data": {"hits": [{"id": "SECRET-123", "title": "T"}], "total": 5}, "k": "v"})
    assert shape == {"data": {"hits": [{"id": "str", "title": "str"}], "total": "int"}, "k": "str"}
    assert "SECRET" not in repr(shape)


def test_redirect_chain_rate_limit_headers_and_content_are_recorded():
    def handler(req):
        if req.url.path == "/start":
            return httpx.Response(301, headers={"location": "/final"})
        return httpx.Response(200, headers={"content-type": "application/json", "x-ratelimit-limit": "60", "x-ratelimit-remaining": "59",
                                            "set-cookie": "sid=1", "server": "nginx"}, content=b'{"oppHits":[{"id":"x"}]}')

    r = probe.probe_once(fetcher(handler), "GET", "https://api.example.gov/start", None)
    assert r["result"] == "OK" and r["redirects"] == ["https://api.example.gov/start"] and r["final_url"].endswith("/final")
    assert r["rate_limit_headers"]["x-ratelimit-remaining"] == "59" and "set-cookie" not in r["rate_limit_headers"]
    assert r["json_shape"] == {"oppHits": [{"id": "str"}]} and r["auth_required"] == "none observed"


def test_auth_requirements_and_http_failures_are_reported_with_a_reason():
    r = probe.probe_once(fetcher(lambda req: httpx.Response(403, headers={"content-type": "application/json"}, content=b'{"message":"API key required"}')),
                         "POST", "https://api.grants.gov/v1/api/search2", {"rows": 1})
    assert r["result"] == "HTTP_403" and r["failure_reason"] == "HTTP 403" and "key" in r["auth_required"] and "API key" in r["body_preview"]
    c = probe.probe_once(fetcher(lambda req: httpx.Response(401, headers={"www-authenticate": 'Bearer realm="x"', "content-type": "text/plain"}, content=b"no")),
                         "GET", "https://api.example.gov/x", None)
    assert c["auth_required"].startswith("challenge:")


def test_proxy_denial_is_classified_as_blocked_not_as_a_site_failure():
    def handler(req):
        raise httpx.ProxyError("403 Forbidden")

    r = probe.probe_once(fetcher(handler), "GET", "https://www.sbir.gov/", None)
    assert r["result"] == "BLOCKED_OR_UNREACHABLE" and r["failure_reason"].startswith("PROXY_DENIED")


def test_policy_refusals_are_distinguished_from_network_blocks():
    r = probe.probe_once(fetcher(lambda req: httpx.Response(200)), "POST", "https://not-allowlisted.example.com/api", {})
    assert r["result"] == "REFUSED_BY_OUR_POLICY"


def test_robots_verdicts():
    cache: dict[str, str] = {}
    allow = fetcher(lambda req: httpx.Response(200, headers={"content-type": "text/plain"}, content=b"User-agent: *\nAllow: /"))
    assert probe.robots_verdict(allow, "a.example.gov", "/x", cache) == "allowed for this path"
    deny = fetcher(lambda req: httpx.Response(200, headers={"content-type": "text/plain"}, content=b"User-agent: *\nDisallow: /private"))
    assert probe.robots_verdict(deny, "b.example.gov", "/private/x", {}) == "DISALLOWED for this path"
    none = fetcher(lambda req: httpx.Response(404, headers={"content-type": "text/plain"}, content=b"nf"))
    assert probe.robots_verdict(none, "c.example.gov", "/x", {}) == "no robots.txt (404)"


def test_burst_is_small_and_stops_at_the_first_429():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        status = 429 if calls["n"] == 2 else 200
        return httpx.Response(status, headers={"content-type": "application/json", "retry-after": "30"}, content=b"{}")

    out = probe.burst(fetcher(handler), "GET", "https://api.example.gov/x", None, 5)
    assert out == {"requests": 2, "statuses": [200, 429], "stopped_early": True} and calls["n"] == 2


def test_markdown_lists_reachable_and_blocked_hosts():
    report = {"egress_mode": "proxy", "run_at": "t", "bursts": {}, "probes": {
        "A": {"host": "api.grants.gov", "method": "POST", "result": "OK", "status": 200, "redirects": [], "auth_required": "none observed",
              "rate_limit_headers": {}, "robots": "no robots.txt (404)"},
        "B": {"host": "www.sbir.gov", "method": "GET", "result": "BLOCKED_OR_UNREACHABLE", "failure_reason": "PROXY_DENIED (x)", "robots": "unreachable"}}}
    md = probe.markdown(report)
    assert "Hosts reachable (allowlisted and answering): api.grants.gov" in md and "Hosts blocked/unreachable: www.sbir.gov" in md
