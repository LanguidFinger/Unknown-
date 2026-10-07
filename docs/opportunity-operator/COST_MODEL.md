# Opportunity Operator: cost model and provider recommendation (pre-live)

Nothing paid is enabled. This is the pre-decision summary requested before choosing a provider and an operating budget.

**Read this first.** Prices for Anthropic models are from Anthropic's official pricing page (retrieved 2026-10-07) and cross-checked against the claude-api reference table (cached 2026-09-25) for the input/output prices. Every other price (other LLM vendors, all search APIs) comes from third-party summaries because the vendor pages were blocked from the build sandbox: treat them as **unverified**. Token counts, funnel rates and thinking overheads are **assumptions to be replaced by measured values from `run_log`/`llm_call_log` in Phase 1**. Reproduce or change any number with `python scripts/cost_model.py`.

## 1. Recommended models

| Role | Recommendation | Why |
|---|---|---|
| Triage + first-pass extraction (bulk) | **Haiku 4.5**, Message Batches | $1 / $5 per MTok, 50% off in batch ($0.50 / $2.50). No always-on thinking, so cost is predictable. Supports structured outputs. |
| IP / data-rights / fees clause pass | **Sonnet 5.5**, batch, effort `low` | $2 / $10 ($1 / $5 batch). This is the pass where an extraction miss is costly, so it gets the stronger model, but only over keyword-hit windows. |
| Fit assessment + skeptic, and dossier drafting | **Opus 5.5**, effort set explicitly | $4 / $20. Used only on candidates that passed deterministic gates, and for approved items. Its default effort is `medium` and thinking cannot be disabled, so set effort `low` for routine judgment and raise it only where measurement shows a gain. |
| Not recommended | **Fable 5.1** | $10 / $50 (2.5x Opus 5.5). The comparison row shows about 5.6x the all-in cost of the lean setup, with no evidence yet that this task needs it. |

Model identifiers stay configuration, not code, so any of these can be swapped. A cheaper non-Anthropic model could undercut Haiku for triage (OpenAI GPT-5.4-nano and Gemini Flash-Lite tiers are reported at roughly $0.20-0.30 in / $1.25-2.50 out, **unverified**); the `LLMClient` port makes that a measured experiment in Phase 1, not a rewrite. Structured-output support for those vendors is also unverified here.

## 2. Recommended search / retrieval

- **Federal funding core needs no search vendor.** Grants.gov and SBIR.gov expose public APIs that return structured fields (not yet verified: see `SOURCE_FEASIBILITY.md`). Their cost is $0 in model tokens.
- **Web search is supplementary** (state/local programs, credits, corporate programs): about 30 queries per 100 candidates, so roughly $0.15 per 100 discovered at the reported $5 per 1,000 queries.
- **Provisional pick: Brave Search API** (reported $5 per 1,000 queries, with a monthly credit; key required; all unverified). Chosen over Tavily and Exa because those return page content the pipeline does not need (it fetches primary sources itself) and cost more per query. Serper is reported cheapest per query but returns scraped Google snippets; terms on storing/caching results and on AI use were **not retrievable** for any vendor, so read each vendor's terms before storing results.
- Anthropic's server-side web search is $10 per 1,000 searches plus tokens for the results (official), so it is the most expensive option and is not recommended for bulk discovery.
- Google Custom Search is reported closed to new customers and discontinued 2027-01-01 (unverified): do not build on it. Bing Search API was reported retired 2025-08-11 (consistent across several sources, page not fetched).
- No search vendor needs to be chosen to start Phase 1; the first live run can use the structured APIs plus the curated credit list.

## 3. Expected cost (assumption-based)

Funnel per 100 discovered candidates: deterministic prefilter keeps 60, cheap-model triage keeps 30% of those, so **18 are fully verified**, at most 5 reach the owner queue, and about 2 are approved and get a dossier.

| Scenario | Discover + triage, per 100 discovered | Per opportunity fully verified | Per completed dossier | All-in, per 100 discovered |
|---|---|---|---|---|
| **LEAN (recommended start)** | $0.20 | $0.122 | $0.40 | **$3.18** |
| BALANCED (Sonnet extraction, Opus dossiers) | $0.20 | $0.158 | $0.71 | $4.46 |
| PREMIUM (Opus everywhere except triage) | $0.20 | $0.265 | $0.71 | $6.39 |
| LEAN without deterministic windowing of terms | $0.20 | $0.133 | $0.40 | $3.39 |
| All Fable 5.1 (comparison only) | $0.20 | $0.762 | $1.98 | $17.88 |

Answers to the four numbers asked for, for the recommended LEAN setup: **about $0.20 per 100 discovered** (discovery plus triage), **about $0.12 per opportunity fully verified**, **about $0.40 per completed dossier**, and **about $3 all-in per 100 discovered**. At a cap of $5 per run (the current default setting) a run has headroom of roughly 1.5x.

What drives the cost: in LEAN, the Opus judgment pass is about two-thirds of the per-verified cost because of the assumed 2,000 thinking tokens per call at `medium` effort. Levers, in order: run the judgment pass only on candidates that pass deterministic gates (about 12 of the 18, not all 18); use `low` effort for fit assessment; use Sonnet 5.5 for the skeptic until measurement shows Opus is needed; cache the shared system prompt and clause checklist (cache reads are $0.20 per MTok for Opus/Sonnet 5.5). The model also ignores prompt caching (conservative) and uses the 1.3x token multiplier the pricing page gives for the newer tokenizer.

## 4. Where deterministic or API retrieval replaces model calls

| Work | Done without a model by |
|---|---|
| Discovery, status, deadlines, award ceiling/floor, agency, eligibility codes | Grants.gov and SBIR.gov API fields. The JSON response is stored as the snapshot, so the verbatim-quote rule applies to API evidence too and needs no LLM. |
| Dedupe, renamed/renewed-program linking | Canonical key, alias table, normalized-name matching |
| Hard eligibility knockouts, scam patterns (fees, "guaranteed", no sponsor) | Rules in `gates.yaml` evaluated against profile flags kept local |
| HTML to text, quote verification, offsets | Code (already built) |
| Which parts of a terms document the clause pass reads | Keyword/regex windows around license, assign, patent, data, publication, exclusivity, fee, equity, repayment terms. Cuts input to the Sonnet pass by roughly 60% (6k vs 15k tokens in the model). |
| Re-checks of watched programs | Content-hash comparison. Unchanged hash means no extraction, so a recheck costs roughly nothing. |
| Scoring arithmetic, effort estimate, priority, queue cap | Code |
| Prerequisite blockers and unlocks | Graph logic (already built) |

A model is still needed for: interpreting unstructured eligibility and IP/terms text, fit assessment, the skeptic judgment, and dossier drafting.

## 5. Decision needed before any live run

1. Confirm the lean setup (Haiku / Sonnet / Opus split above) or ask for a different mix.
2. Pick a search provider or defer web search.
3. Set an operating budget: a per-run cap (default $5) and a monthly cap. The first measured run will replace these estimates, and the first live run should use a deliberately small cap.
4. Provide keys at runtime through the environment. They are never stored in the repository or the data directory.
