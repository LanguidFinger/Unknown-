"""Source/API feasibility probe. Read-only; no credentials; goes through the same policy-enforced HttpFetcher.

Run it from the machine that will run live discovery (a managed sandbox's egress proxy may block hosts):

    uv run python scripts/feasibility/check_sources.py > feasibility-report.json

It records, per endpoint: status, content type, size, whether the body parses as JSON and its top-level
keys. It asserts NOTHING about response semantics; those are verified by hand from the saved output and
turned into fixtures + parsers in Phase 1. The request shapes below are HYPOTHESES to be checked.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import httpx

from opportunity_operator.adapters.http_fetcher import HttpFetcher
from opportunity_operator.config import Settings
from opportunity_operator.errors import FetchRefused

Probe = tuple[str, str, str, dict[str, Any] | None]
PROBES: list[Probe] = [
    (
        "grants.gov search2 (public search API)",
        "POST",
        "https://api.grants.gov/v1/api/search2",
        {"rows": 2, "keyword": "artificial intelligence small business", "oppStatuses": "forecasted|posted"},
    ),
    ("sbir.gov solicitations API", "GET", "https://api.www.sbir.gov/public/api/solicitations?keyword=artificial+intelligence&open=1&rows=2", None),
    ("sbir.gov awards API", "GET", "https://api.www.sbir.gov/public/api/awards?keyword=artificial+intelligence&rows=2", None),
    ("sbir.gov robots.txt", "GET", "https://www.sbir.gov/robots.txt", None),
    ("grants.gov robots.txt", "GET", "https://www.grants.gov/robots.txt", None),
    ("sbir.gov solicitations page", "GET", "https://www.sbir.gov/solicitations", None),
    ("DoD SBIR/STTR portal", "GET", "https://www.dodsbirsttr.mil/", None),
    ("NSF SBIR/STTR", "GET", "https://seedfund.nsf.gov/", None),
    ("simpler.grants.gov API host", "GET", "https://api.simpler.grants.gov/", None),
    ("Google for Startups Cloud", "GET", "https://cloud.google.com/startup", None),
    ("Microsoft for Startups", "GET", "https://www.microsoft.com/en-us/startups", None),
    ("Anthropic startups", "GET", "https://www.anthropic.com/startups", None),
]


def probe(fetcher: HttpFetcher, method: str, url: str, body: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {"method": method, "url": url}
    try:
        res = fetcher.post_json(url, body or {}) if method == "POST" else fetcher.get(url)
    except FetchRefused as exc:
        out["result"] = f"REFUSED_BY_OUR_POLICY: {exc}"
        return out
    except httpx.HTTPError as exc:
        out["result"] = f"NETWORK_ERROR: {type(exc).__name__}: {str(exc)[:160]}"
        return out
    out.update(
        result="OK" if res.status < 400 else f"HTTP_{res.status}",
        status=res.status,
        content_type=res.content_type,
        bytes=len(res.body),
        final_url=res.final_url,
    )
    if "json" in res.content_type:
        try:
            parsed = json.loads(res.body)
            out["json_top_level_keys"] = sorted(parsed)[:20] if isinstance(parsed, dict) else f"<{type(parsed).__name__}>"
        except ValueError:
            out["json_top_level_keys"] = "<unparseable>"
    return out


def main() -> int:
    proxied = bool(os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"))
    settings = Settings(egress_mode="proxy" if proxied else "direct", min_host_interval_s=1.0)
    fetcher = HttpFetcher(settings)
    report: dict[str, Any] = {"egress_mode": settings.egress_mode, "probes": {}}
    for label, method, url, body in PROBES:
        report["probes"][label] = probe(fetcher, method, url, body)
    json.dump(report, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
