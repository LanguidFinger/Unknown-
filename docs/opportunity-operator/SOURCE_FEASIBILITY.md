# Source and API feasibility

**Rule.** The only evidence of availability is a result obtained in the runtime the agent will operate in: the cloud environment. A result from any other machine is a *secondary diagnostic* and does not change a source's status for the cloud runtime. A source that works elsewhere but fails in the cloud stays **UNAVAILABLE (cloud)** until resolved there.

## Current status (cloud environment, run 2026-10-07 11:36 UTC)

**Every government source is UNAVAILABLE in the cloud runtime. The allow-list has not been changed yet, so this is not a finding about the sites.** The environment's network allow-list is a setting only the account owner can change; I have no tool for it and did not try to work around the proxy.

| Source tested | Host | Method | Result | Redirects | Auth requirement | Rate-limit observations | Failure reason |
|---|---|---|---|---|---|---|---|
| Grants.gov search API (`/v1/api/search2`, hypothesised request) | `api.grants.gov` | POST | BLOCKED | none | unknown (never reached) | none (never reached) | `PROXY_DENIED`: proxy answered CONNECT with 403 Forbidden |
| SBIR.gov solicitations API | `api.www.sbir.gov` | GET | BLOCKED | none | unknown | none | `PROXY_DENIED` (403) |
| SBIR.gov awards API | `api.www.sbir.gov` | GET | BLOCKED | none | unknown | none | `PROXY_DENIED` (403) |
| SBIR.gov solicitations page | `www.sbir.gov` | GET | BLOCKED | none | unknown | none | `PROXY_DENIED` (403) |
| Grants.gov home | `www.grants.gov` | GET | BLOCKED | none | unknown | none | `PROXY_DENIED` (403) |
| DoD SBIR/STTR portal | `www.dodsbirsttr.mil` | GET | BLOCKED | none | unknown | none | `PROXY_DENIED` (403) |
| NSF SBIR/STTR | `seedfund.nsf.gov` | GET | BLOCKED | none | unknown | none | `PROXY_DENIED` (403) |
| NIH grants | `grants.nih.gov` | GET | BLOCKED | none | unknown | none | `PROXY_DENIED` (403) |
| DOE Office of Science | `science.osti.gov` | GET | BLOCKED | none | unknown | none | `PROXY_DENIED` (403) |
| Simpler.Grants.gov | `simpler.grants.gov` | GET | BLOCKED | none | unknown | none | `PROXY_DENIED` (403) |
| Simpler.Grants.gov API host | `api.simpler.grants.gov` | GET | BLOCKED | none | unknown | none | `PROXY_DENIED` (403) |

robots.txt verdicts could not be read for any host (the same denial). Domains allowlisted so far: **none of the above**. (Credit-program pages for Google, Microsoft and Anthropic startup programs were reachable in an earlier run, which shows the fetcher works end to end including redirects.)

Nothing is known about response shapes, keys, rate limits or terms for any government source. The request shapes in the probe are hypotheses.

## Hosts to add to the cloud environment's Allowed domains (individual domains, no wildcards)

In the session's cloud environment menu choose Edit, then Network access (steps: https://code.claude.com/docs/en/cloud-environments#network-access), and add exactly these ten hosts.

**Required for the first verification (Grants.gov and SBIR.gov):**
`api.grants.gov`, `www.grants.gov`, `api.www.sbir.gov`, `www.sbir.gov`

**SBIR/STTR official agency sources and other authoritative government pages:**
`www.dodsbirsttr.mil`, `seedfund.nsf.gov`, `grants.nih.gov`, `science.osti.gov`, `simpler.grants.gov`, `api.simpler.grants.gov`

If the sites redirect to hosts not on this list (for example attachment or document hosts), the probe records each redirect target. I will list those for you and will not assume they are allowed.

## How the check is run and recorded

After the hosts are allowed, run in the cloud environment:

```
python scripts/feasibility/check_sources.py --markdown      # human-readable table
python scripts/feasibility/check_sources.py > report.json   # full detail
```

For each endpoint it records: host, method, URL, outcome, classified failure reason (`PROXY_DENIED`, `TIMEOUT`, `CONNECT_ERROR`, `REFUSED_BY_OUR_POLICY`, `HTTP_nnn`), status, content type, size, latency, every redirect, authentication hints (challenge header, or a body that mentions a key), rate-limit and retry headers, a robots.txt verdict, and for JSON only the response *shape* (key names, never values). For the two API targets it also makes a small sequential burst (3 requests, at least one second apart, stopping at the first 429/403/5xx) to observe limit headers. It is not a load test.

The probe's behaviour is covered by tests (`tests/unit/test_feasibility_probe.py`): shape-not-values, redirect and header capture, auth hints, proxy denial classified as a block rather than a site failure, robots verdicts, and the burst stopping at the first 429.

## Secondary diagnostic (only if a source fails in the cloud after being allowed)

Run the same script on a local machine and compare. A local success explains nothing about the cloud runtime and does not mark the source available there; it only helps tell a network-policy problem from a site or API problem.

## What is known from the reachable pages (earlier cloud run)

The headline credit amounts appear in the static HTML on the three reachable credit-program pages (no JavaScript needed); eligibility conditions are mostly absent from the static text; terms and IP language sit on linked pages that must be fetched separately. The amounts are marketing copy and are not recorded as program facts; they must be re-fetched and quote-verified in Phase 1. The other ~12 candidate credit programs have not been checked (their URLs were guesses).

## Live sources intended for Phase 1 and the condition for each

Web search is **not** part of this list and stays disabled (see `COST_MODEL.md`).

| Source | Use | Condition before enabling |
|---|---|---|
| Grants.gov public search/detail API | Federal discovery, deadlines, award sizes, eligibility, NOFO links | Status AVAILABLE (cloud) in this document; request/response shape verified from real output; terms and rate limits read; fixtures saved |
| SBIR.gov public API (solicitations; awards history) | SBIR/STTR solicitations; award history for probability priors | Same; if the API is unavailable, fall back to solicitation pages only if robots and terms allow |
| Agency portals (NSF, DoD SBIR/STTR, NIH, DOE) | Solicitation detail documents linked from the above | Static or PDF content reachable in the cloud; PDF extraction built first |
| Official pages of about 15 curated AI/cloud/API/compute credit programs | Credits | Confirmed official URL, reachable in the cloud, and a quote-verifiable terms page for each |

Not enabled: any general web-search vendor, SAM.gov opportunities (separate procurement workflow later), any aggregator as an authoritative source, USAspending.
