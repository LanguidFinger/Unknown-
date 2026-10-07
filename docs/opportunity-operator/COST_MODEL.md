# Opportunity Operator: cost model and provider recommendation (pre-live)

Nothing paid is enabled. This is the pre-decision summary requested before choosing a provider and an operating budget.

**Read this first.** Prices for Anthropic models are from Anthropic's official pricing page (retrieved 2026-10-07) and cross-checked against the claude-api reference table (cached 2026-09-25) for the input/output prices. Every other price (other LLM vendors, all search APIs) comes from third-party summaries because the vendor pages were blocked from the build sandbox: treat them as **unverified**. Token counts, funnel rates and thinking overheads are **assumptions to be replaced by measured values from `run_log`/`llm_call_log` in Phase 1**. Reproduce or change any number with `python scripts/cost_model.py`.

## 1. Recommended models

| Role | Recommendation | Why |
|---|---|---|
| Triage + first-pass extraction (bulk) | **Haiku 4.5**, Message Batches | $1 / $5 per MTok, 50% off in batch ($0.50 / $2.50). No always-on thinking, so cost is predictable. Supports structured outputs. |
| IP / data-rights / fees clause pass | **Sonnet 5.5**, batch, effort `low` | $2 / $10 ($1 / $5 batch). This is the pass where an extraction miss is costly, so it gets the stronger model, but only over keyword-hit windows. |
| Fit assessment + skeptic, and dossier drafting | **Opus 5.5**, effort set explicitly | $4 / $20. Used only on candidates that passed deterministic gates, and for approved items. Its default effort is `medium` and thinking cannot be disabled, so set effort `low` for routine judgment and raise it only where measurement shows a gain. |
| Not recommended | **Fable 5.1** | $10 / $50 (2.5x Opus 5.5). The comparison row shows about 5.9x the all-in cost of the lean setup, with no evidence yet that this task needs it. |

Model identifiers stay configuration, not code, so any of these can be swapped. A cheaper non-Anthropic model could undercut Haiku for triage (OpenAI GPT-5.4-nano and Gemini Flash-Lite tiers are reported at roughly $0.20-0.30 in / $1.25-2.50 out, **unverified**); the `LLMClient` port makes that a measured experiment in Phase 1, not a rewrite. Structured-output support for those vendors is also unverified here.

## 2. Web search: deferred (owner decision)

No general search vendor is enabled and none will be added yet (not Tavily, SerpAPI, Brave, Bing, Google or any other). The initial MVP discovers through authoritative sources only: Grants.gov, SBIR/STTR official sources, other authoritative government APIs and pages, and the curated official AI/cloud credit program pages. Once that pipeline works, search vendors will be compared on the *actual gaps* it leaves.

What is built: the `SearchProvider` port and a `GuardedSearch` wrapper, **disabled**. It refuses every query unless `search_enabled` is switched on in settings AND a provider is wired at the composition root; no vendor adapter, vendor host or vendor name exists anywhere in the code (a test scans for this). Enabling later is a deliberate, reviewable change.

Vendor pricing gathered earlier (all unverified, vendor pages were blocked from the sandbox) is kept in the research notes only and is not a recommendation.

## 3. Expected cost (assumption-based)

Funnel per 100 discovered candidates: deterministic prefilter keeps 60, cheap-model triage keeps 30% of those, so **18 are fully verified**, at most 5 reach the owner queue, and about 2 are approved and get a dossier.

| Scenario | Discover + triage, per 100 discovered | Per opportunity fully verified | Per completed dossier | All-in, per 100 discovered |
|---|---|---|---|---|
| **LEAN (recommended start)** | $0.04 | $0.122 | $0.40 | **$3.03** |
| BALANCED (Sonnet extraction, Opus dossiers) | $0.04 | $0.158 | $0.71 | $4.31 |
| PREMIUM (Opus everywhere except triage) | $0.04 | $0.265 | $0.71 | $6.24 |
| LEAN without deterministic windowing of terms | $0.04 | $0.133 | $0.40 | $3.24 |
| All Fable 5.1 (comparison only) | $0.04 | $0.762 | $1.98 | $17.73 |

Search is disabled, so discovery costs no search or query-expansion tokens; the discovery figure is triage only. (Adding a search vendor later would add a per-query cost; any vendor price is unverified today.)

Answers to the four numbers asked for, for the recommended LEAN setup: **about $0.04 per 100 discovered** (triage only; discovery uses free government sources), **about $0.12 per opportunity fully verified**, **about $0.40 per completed dossier**, and **about $3 all-in per 100 discovered**. At a cap of $5 per run a run has headroom of roughly 1.6x.

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

## 5. Approved budget policy (owner decision, 2026-10-07) and how it is enforced

| Rule | Enforcement |
|---|---|
| **Hard cap $5 per run** | `BudgetGuard` checks a conservative worst-case estimate (about 3 characters per token for input, plus the maximum output) against the run's remaining cap *before* a call may start. Exceeding it raises `BudgetExceeded` ("owner approval required to continue"); the run stops. |
| **Hard cap $50 per UTC month** (a ceiling, not a target) | The same pre-flight check against the month's total, summed from the append-only call log across all runs. Previous months do not count. |
| **Only the owner can raise a cap, and only for one run or one month** | Owner-recorded, append-only `budget_approval` rows (`opop budget-approve`, interactive confirmation). The agent cannot insert them: database authorizer and trigger both refuse. |
| **Usage and cost by pipeline stage** | Every model call is logged with its stage, model, tokens and cost, including calls whose output was then blocked. `opop usage` reports calls, tokens, cost and average cost per call by stage and model, and the share of the monthly cap used. The run total is written to `run_log`. Phase 1 adds outcomes per stage (candidates passed, elevated, approved) so each stage can be judged on whether it earns its cost. |
| **No silent fallback to a more expensive model** | Each stage has exactly one configured model and client. A missing stage, a client that is not the stage's configured model, or an unpriced model is refused. Provider errors propagate and are never retried on another model. If a response is served by a different model than configured (for example a provider-side fallback), the call is logged at the model's real price and then refused. Phase 1 adapters must not enable any provider-side fallback or refusal-rescue feature. |

At the lean setup's estimated all-in cost (about $3 per 100 discovered candidates) the monthly ceiling corresponds to roughly 1,650 discovered candidates per month, and the first live runs should use a deliberately small per-run cap well under $5 so the first measured numbers arrive cheaply.

Model split (configured in `Settings.stage_models`; prices in `Settings.model_prices`, both overridable from the data directory's `settings.yaml`): query expansion, triage and extraction on Haiku 4.5; terms/IP clause pass and dossier drafting on Sonnet 5.5; judgment (fit and skeptic) and dossier summary on Opus 5.5. Fable 5.1 is not in the split.

Keys are provided through the environment at runtime and are never stored in the repository or the data directory.
