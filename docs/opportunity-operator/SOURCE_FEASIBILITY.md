# Source and API feasibility (Phase 0)

Retrieved 2026-10-07 from the managed build sandbox. **Verdict: the Grants.gov and SBIR.gov checks could not be completed from here.** This is a blocker, not a pass. Nothing below about those APIs has been verified.

## What was run

`scripts/feasibility/check_sources.py` probes each endpoint through the project's own policy-enforced `HttpFetcher`. A separate research subagent probed the same hosts with `curl` before the script existed, and independently got the same result.

| Probe | Result via our fetcher |
|---|---|
| Grants.gov `POST api.grants.gov/v1/api/search2` | **BLOCKED**: proxy `403 Forbidden` on CONNECT |
| SBIR.gov solicitations API, awards API, `robots.txt`, solicitations page | **BLOCKED** (same) |
| Grants.gov `robots.txt` | **BLOCKED** (same) |
| DoD SBIR/STTR portal, NSF SBIR/STTR | **BLOCKED** (same) |
| Simpler.Grants.gov API host | **BLOCKED** (same) |
| Google for Startups Cloud Program page | OK (HTTP 200) |
| Microsoft for Startups page | OK (HTTP 200) |
| Anthropic startups page (redirects to claude.com program page) | OK (HTTP 200) |

The 403 comes from the sandbox's egress policy (an allow-list: even `example.com` was denied), not from the government sites. Nothing was done to get around it. The three credit-program pages fetched successfully, which also confirms the real fetcher works end to end against live sites, including redirects.

## What is known and unknown

Known (from the three reachable credit pages, observed 2026-10-07): the headline credit amounts appear in the static HTML (no JavaScript rendering was needed); eligibility conditions are mostly **absent** from the static text, with stage tiers at best; terms and IP language, where present, sit on linked pages or documents that must be fetched separately. The amounts are marketing copy and are not recorded as program facts; they must be re-fetched and quote-verified in Phase 1.

**Unverified (all of it):** whether `search2` works without a key; its request/response shape, pagination and filters; the `fetchOpportunity` detail shape (award ceiling/floor, eligibility, cost sharing, attachments); whether the SBIR.gov API is currently usable; rate limits, robots.txt and API terms for every government host; whether the agency portals are static HTML or JavaScript/login-gated; whether Simpler.Grants.gov needs a key. The request shapes in the probe script are hypotheses written from memory, not facts.

The other ~12 candidate credit programs could not be checked; the subagent's URLs for them were guesses and are unconfirmed.

## How to finish the check (one of)

1. **Allow the hosts in the cloud environment.** In the session's cloud environment menu choose Edit, then Network access, and add the hosts under Allowed domains (or choose a broader access level); steps are at https://code.claude.com/docs/en/cloud-environments#network-access. Hosts needed: `api.grants.gov`, `www.grants.gov`, `api.www.sbir.gov`, `www.sbir.gov`, `api.simpler.grants.gov`, `simpler.grants.gov`, `www.dodsbirsttr.mil`, `seedfund.nsf.gov`, `grants.nih.gov`, `science.osti.gov`, plus the credit-program hosts once their official URLs are confirmed (AWS, NVIDIA, Cloudflare, OpenAI, Databricks, Snowflake, Oracle, Modal, Together AI, Lambda, Hugging Face, Vercel, etc.). I would then re-run the probe and save small public samples as fixtures.
2. **Run the probe on your own machine** (no repo data involved): `uv run python scripts/feasibility/check_sources.py > report.json`, and send me the file. It prints only status, content type, size and top-level JSON keys.

Either way, Phase 1 adapters will be written against saved real responses (fixtures), not against assumed shapes.

## Exact live sources I intend to enable next (Phase 1), and the condition for each

| Source | Use | Condition before enabling |
|---|---|---|
| Grants.gov public search API (`api.grants.gov`: search and opportunity detail) | Federal discovery, deadlines, award sizes, eligibility, NOFO links | Reachable; request/response shape verified from real output; terms/rate limit read; fixtures saved |
| SBIR.gov public API (`api.www.sbir.gov`: solicitations; awards) | SBIR/STTR solicitations; awards history for probability priors | Same; if the API is unavailable, fall back to solicitation pages only if robots/terms allow |
| Official program pages for about 15 curated AI/cloud/API/compute credit programs | Credits | Confirmed official URL, reachability, and a quote-verified terms page for each; three are already reachable (Google, Microsoft, Anthropic startup programs) |
| Agency portals (NSF, DoD SBIR/STTR, NIH, DOE) | Solicitation detail documents linked from the above | Static or PDF content reachable; PDF extraction built first |

Not enabled in Phase 1: a web search vendor (needs your choice and budget), SAM.gov opportunities (separate procurement workflow later), any aggregator as an authoritative source, USAspending (optional later, for historical award statistics).
