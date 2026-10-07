"""Source/API feasibility probe. Read-only; no credentials; goes through the same policy-enforced HttpFetcher.

Run it IN THE ENVIRONMENT THE AGENT WILL OPERATE IN (the cloud environment). A result from any other machine is a
secondary diagnostic only and must not be recorded as availability for that runtime.

    python scripts/feasibility/check_sources.py --markdown > feasibility.md
    python scripts/feasibility/check_sources.py > feasibility.json

Per endpoint it records: host, method, URL, outcome, failure reason (classified), HTTP status, content type, size,
every redirect, authentication hints, rate-limit/retry headers, robots.txt verdict, and (for JSON) only the SHAPE of
the response (key names, never values). It asserts nothing about response semantics: those are checked by hand from
the saved output and turned into fixtures + parsers in Phase 1. The request shapes below are HYPOTHESES to verify.

Politeness: every host is rate limited by the fetcher (>= 1 s between requests); the optional burst is small and stops at
the first 429/403/5xx. This is a feasibility probe, not a load test.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from opportunity_operator.adapters.http_fetcher import USER_AGENT, HttpFetcher
from opportunity_operator.config import Settings
from opportunity_operator.errors import FetchRefused

Probe = tuple[str, str, str, dict[str, Any] | None]
# (label, method, url, json body). Grants.gov / SBIR request shapes are hypotheses.
PROBES: list[Probe] = [
    ("grants.gov search2 (public search API)", "POST", "https://api.grants.gov/v1/api/search2",
     {"rows": 2, "keyword": "artificial intelligence small business", "oppStatuses": "forecasted|posted"}),
    ("sbir.gov solicitations API", "GET", "https://api.www.sbir.gov/public/api/solicitations?keyword=artificial+intelligence&open=1&rows=2", None),
    ("sbir.gov awards API", "GET", "https://api.www.sbir.gov/public/api/awards?keyword=artificial+intelligence&rows=2", None),
    ("sbir.gov solicitations page", "GET", "https://www.sbir.gov/solicitations", None),
    ("grants.gov home", "GET", "https://www.grants.gov/", None),
    ("DoD SBIR/STTR portal", "GET", "https://www.dodsbirsttr.mil/", None),
    ("NSF SBIR/STTR", "GET", "https://seedfund.nsf.gov/", None),
    ("NIH grants", "GET", "https://grants.nih.gov/", None),
    ("DOE Office of Science", "GET", "https://science.osti.gov/", None),
    ("simpler.grants.gov", "GET", "https://simpler.grants.gov/", None),
    ("simpler.grants.gov API host", "GET", "https://api.simpler.grants.gov/", None),
]
BURST_TARGETS = {"grants.gov search2 (public search API)", "sbir.gov solicitations API"}


def classify_error(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {str(exc)[:160]}"
    if isinstance(exc, httpx.ProxyError):
        return f"PROXY_DENIED ({text})"
    if isinstance(exc, httpx.ConnectTimeout | httpx.ReadTimeout | httpx.PoolTimeout):
        return f"TIMEOUT ({text})"
    if isinstance(exc, httpx.ConnectError):
        return f"CONNECT_ERROR ({text})"
    return f"NETWORK_ERROR ({text})"


def json_shape(obj: Any, depth: int = 6) -> Any:
    """Key names only, never values."""
    if depth == 0:
        return type(obj).__name__
    if isinstance(obj, dict):
        return {k: json_shape(v, depth - 1) for k, v in list(obj.items())[:25]}
    if isinstance(obj, list):
        return [json_shape(obj[0], depth - 1)] if obj else []
    return type(obj).__name__


def auth_hint(status: int, headers: dict[str, str], body: bytes) -> str:
    if "www-authenticate" in headers:
        return f"challenge: {headers['www-authenticate'][:80]}"
    snippet = body[:400].decode("utf-8", "replace").lower()
    if status in (401, 403) or any(w in snippet for w in ("api key", "api_key", "apikey", "unauthorized", "forbidden")):
        return "possible: key/auth required (see body_preview)"
    return "none observed"


def robots_verdict(fetcher: HttpFetcher, host: str, path: str, cache: dict[str, str]) -> str:
    if host in cache:
        return cache[host]
    try:
        res = fetcher.get(f"https://{host}/robots.txt")
    except (FetchRefused, httpx.HTTPError) as exc:
        cache[host] = f"unreachable ({type(exc).__name__})"
        return cache[host]
    if res.status == 404:
        cache[host] = "no robots.txt (404)"
    elif res.status >= 400:
        cache[host] = f"robots.txt HTTP {res.status}"
    else:
        rp = RobotFileParser()
        rp.parse(res.body.decode("utf-8", "replace").splitlines())
        cache[host] = "allowed for this path" if rp.can_fetch(USER_AGENT, f"https://{host}{path}") else "DISALLOWED for this path"
    return cache[host]


def probe_once(fetcher: HttpFetcher, method: str, url: str, body: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {"method": method, "url": url, "host": urlsplit(url).hostname}
    started = time.monotonic()
    try:
        res = fetcher.post_json(url, body or {}) if method == "POST" else fetcher.get(url)
    except FetchRefused as exc:
        out["result"] = "REFUSED_BY_OUR_POLICY"
        out["failure_reason"] = str(exc)
        return out
    except httpx.HTTPError as exc:
        out["result"] = "BLOCKED_OR_UNREACHABLE"
        out["failure_reason"] = classify_error(exc)
        return out
    headers = dict(res.headers)
    out.update(
        result="OK" if res.status < 400 else f"HTTP_{res.status}",
        status=res.status, content_type=res.content_type, bytes=len(res.body), latency_s=round(time.monotonic() - started, 2),
        redirects=list(res.redirects), final_url=res.final_url, rate_limit_headers=headers,
        auth_required=auth_hint(res.status, headers, res.body),
    )
    if res.status >= 400:
        out["body_preview"] = res.body[:300].decode("utf-8", "replace")
        out["failure_reason"] = f"HTTP {res.status}"
    if "json" in res.content_type:
        try:
            out["json_shape"] = json_shape(json.loads(res.body))
        except ValueError:
            out["json_shape"] = "unparseable"
    return out


def burst(fetcher: HttpFetcher, method: str, url: str, body: dict[str, Any] | None, n: int) -> dict[str, Any]:
    """n sequential requests (fetcher enforces >= 1 s spacing). Stops at the first 429/403/5xx."""
    seen: list[Any] = []
    for _ in range(n):
        r = probe_once(fetcher, method, url, body)
        seen.append(r.get("status", r.get("result")))
        if not isinstance(seen[-1], int) or seen[-1] in (403, 429) or seen[-1] >= 500:
            break
    return {"requests": len(seen), "statuses": seen, "stopped_early": len(seen) < n}


def markdown(report: dict[str, Any]) -> str:
    lines = [f"Egress mode: `{report['egress_mode']}`; run at {report['run_at']}", "",
             "| Source | Method | Result | Status | Redirects | Auth | Rate-limit headers | Robots | Failure reason |",
             "|---|---|---|---|---|---|---|---|---|"]
    for label, r in report["probes"].items():
        rl = ", ".join(f"{k}={v}" for k, v in r.get("rate_limit_headers", {}).items() if k.startswith(("x-ratelimit", "ratelimit", "retry-after")))
        lines.append(f"| {label} (`{r['host']}`) | {r['method']} | {r['result']} | {r.get('status', '-')} | "
                     f"{' -> '.join(r.get('redirects', [])) or 'none'} | {r.get('auth_required', '-')} | {rl or 'none seen'} | "
                     f"{r.get('robots', '-')} | {r.get('failure_reason', '-')} |")
    reachable = sorted({r["host"] for r in report["probes"].values() if r["result"] in {"OK"} or r["result"].startswith("HTTP_")})
    blocked = sorted({r["host"] for r in report["probes"].values() if r["result"] == "BLOCKED_OR_UNREACHABLE"})
    lines += ["", f"Hosts reachable (allowlisted and answering): {', '.join(reachable) or 'none'}",
              f"Hosts blocked/unreachable: {', '.join(blocked) or 'none'}"]
    if report.get("bursts"):
        lines += ["", "Burst observations: " + "; ".join(f"{k}: {v['requests']} req, statuses {v['statuses']}" for k, v in report["bursts"].items())]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--burst", type=int, default=3, help="sequential requests per API target for rate-limit observation (0 = skip)")
    args = ap.parse_args()
    proxied = bool(os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"))
    settings = Settings(egress_mode="proxy" if proxied else "direct", min_host_interval_s=1.0)
    fetcher = HttpFetcher(settings)
    robots_cache: dict[str, str] = {}
    report: dict[str, Any] = {"egress_mode": settings.egress_mode, "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                              "probes": {}, "bursts": {}}
    for label, method, url, body in PROBES:
        r = probe_once(fetcher, method, url, body)
        r["robots"] = robots_verdict(fetcher, r["host"], urlsplit(url).path or "/", robots_cache)
        report["probes"][label] = r
        if args.burst > 1 and label in BURST_TARGETS and r.get("result") == "OK":
            report["bursts"][label] = burst(fetcher, method, url, body, args.burst)
    print(markdown(report) if args.markdown else json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
