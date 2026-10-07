# Phase 0 report: foundations and controls

Status: **Phase 0 controls are implemented and demonstrated by tests. Phase 0 is not complete: the Grants.gov / SBIR.gov feasibility result in the cloud environment is still "UNAVAILABLE (not yet allow-listed)". That is the one open gate, and it needs an action only you can take (section 5).** No live discovery run has happened and none will until that gate is resolved.

Reproduce: `uv venv .venv && uv pip install -e ".[dev]" && .venv/bin/python -m pytest -o addopts=""`.

## What changed since the first Phase 0 report (your three decisions)

1. **Government source probe, cloud first.** The probe now records exactly what you asked for (domains tested and which answered, method, result, every redirect, authentication hints, rate-limit and retry headers, a small polite burst, robots.txt verdict, classified failure reason) and is covered by 8 tests. It was run in the cloud environment on 2026-10-07 11:36 UTC: all ten government hosts are blocked (`PROXY_DENIED`, proxy 403 on CONNECT). I cannot change the environment's Allowed domains, so none have been added. The exact ten individual domains, no wildcards, are in `SOURCE_FEASIBILITY.md`. Every source stays **UNAVAILABLE (cloud)**, and a local run will be only a secondary diagnostic.
2. **Budget and models.** Hard $5 per run and $50 per UTC month, enforced before each call by a conservative worst-case estimate; only owner-recorded, append-only approvals can raise either cap for one run or month. Per-stage model/token/cost logging (`opop usage`). One configured model per stage and no silent fallback. Web search is disabled and no vendor exists in the code.
3. **History scrub.** Done and verified locally and on a fresh mirror clone (section 5, item 2 has the one limit I could not fix).

## 1. What is implemented

| Area | Where |
|---|---|
| Schema (3 migrations), 59 triggers, frozen 14-state lifecycle, append-only provenance | `store/migrations/0001..0003`, `scripts/gen_guards.py` |
| Role-based DB access (agent, system, owner, context, migrator); startup tamper detection | `store/db.py`, `store/migrator.py`, `app.py` |
| Single route from profile data to any model; access level x destination x purpose; owner-verified and sensitivity rules | `policy/access.py`, `context_builder.py`, `prompt.py` |
| Guarded LLM/search/fetch wrappers with audit logging | `policy/guarded.py` |
| **Budget policy**: per-run and monthly hard caps, owner-only overrides, ledger from the call log | `policy/budget_guard.py`, `owner.py`, migration 0003 |
| **Per-stage usage/cost reporting** | `usage.py`, `opop usage`, `run_log` rollup |
| **Stage-to-model policy, no fallback** | `config.py` (`stage_models`, `model_prices`), `GuardedLLM` |
| **Search port, disabled** | `ports/search.py`, `GuardedSearch`, `search_enabled=False` |
| Owner authority (typed confirmation, TTY only) | `owner.py`, `cli.py` |
| Restricted-project isolation by architectural absence | `safe_fs.py`, `config.py`, `cli.py`, `tests/isolation/` |
| Provenance: verbatim quotes with offsets, re-verification audit, `explain` | `store/repo.py`, `text.py`, `audit.py` |
| Real HTTP fetcher with redirect/header capture | `adapters/http_fetcher.py` |
| New-entity prerequisite queue; placeholder project | `prerequisites.py`, `seeds/` |
| Feasibility probe, cost model | `scripts/feasibility/check_sources.py`, `scripts/cost_model.py` |

Not implemented (Phase 1+): discovery adapters, extraction prompts, gates, scoring, skeptic, dedupe/registry logic, PDF extraction, dossier generation, dashboard, recheck, any live provider adapter.

## 2. Tests

**646 passed, 0 failed (about 16 s).** `ruff` clean; `mypy --strict` clean on the 14 policy/store/`safe_fs`/`text` files.

| Suite | Tests | Covers |
|---|---|---|
| `tests/db` | 435 | Exhaustive lifecycle matrix (392 cases) against an independent oracle; forged/borrowed/reused decisions; append-only; 15 schema-tampering statements; raw-connection fail-closed; randomized agent walk; profile tables owner-only; `RESTRICTED` unstorable; positive control showing an unguarded DB lets an agent self-approve |
| `tests/isolation` | 75 | Architecture scans with positive controls; 45 fetch-policy tests; `DataDir` confinement; full hostile run observed with audit hooks |
| `tests/policy` | 48 | Context matrix; canary leakage; guarded wrappers; **19 budget/model tests**: run cap and owner approval, monthly cap across runs and months, approvals owner-only and append-only, per-stage cost rows and run rollup, no retry on another model, unconfigured stage, wrong-model client, unpriced model, a call served by a different model logged at its real price then refused, search disabled by default, no search vendor anywhere in the code |
| `tests/provenance` | 11 | Verified vs fabricated quotes, supersession, tampering, audit, `explain` |
| `tests/unit` | 77 | Quote rules, deny-term variants, budgets and access matrix, prerequisites, CLI/owner/seed, repo hygiene, **8 probe tests** |

Limit: I wrote both code and tests. The lifecycle oracle is independent of the seeded table and several tests carry positive controls, but a reviewer should read the tests.

## 3. Security boundaries demonstrated

| Boundary | Demonstrated by | Not demonstrated |
|---|---|---|
| Agent cannot approve, submit, accept, certify, pay, mark prerequisites done, or raise a spending cap | No such code path exists (scans); triggers + authorizer reject forged or borrowed attempts (matrix, randomized walk, budget-approval tests) | A process that opens the SQLite file with its own code (shared OS user); startup verification detects dropped guards but cannot prevent them |
| Spending cannot exceed the caps without an owner record | Pre-flight worst-case check against run and month ceilings; cross-run month total; approvals only via the owner role | The estimate is conservative but not exact; a call can still land slightly over before the next call is refused |
| No silent cost escalation | One model per stage; mismatches, unpriced models and wrong-model responses refused and logged at real cost | Adapters do not exist yet; Phase 1 adapters must not enable any provider-side fallback (written into the design) |
| Restricted material cannot be stored, ingested or read | `RESTRICTED` rejected by schema; no ingest command/setting; data dir refused in any git worktree; hostile page and hostile model produced zero opens/listings of a synthetic restricted repo (audit hook) | OS-level boundary: the process still has your user's file permissions (container/separate user is Phase 3) |
| Profile data reaches a model only via `ContextBuilder` | Matrix, canary sweep, AST scans | Same-process access to private attributes (e.g. `app.llm._inner`) cannot be prevented in Python |
| Deny-list tripwire | Variant matching; blocks query, prompt, body, URL, output; hash-only incidents | Paraphrase/encoding evasion |
| Every fact traces to a source and a checked quote | Quote check with offsets; re-verification audit; tamper detection | The DB cannot read files; a lying writer is caught by the audit afterward (tested) |
| Network is read-only, public-only, credential-free | 45 fetch-policy tests | DNS rebinding in `direct` mode (validated address not pinned); only live use so far was in `proxy` mode |
| Real prompt-injection resistance | Mock hostile page and model | No real model tested yet |

## 4. Differences from DESIGN.md

All in `DESIGN.md` section 19 (items 13-17 are new in this round): budget policy, no silent fallback, search disabled, cloud-evidence rule, history rewrite.

## 5. Open items

1. **Cloud allow-list (the gate).** Add these ten individual domains to the cloud environment's Allowed domains (Edit, then Network access; https://code.claude.com/docs/en/cloud-environments#network-access): `api.grants.gov`, `www.grants.gov`, `api.www.sbir.gov`, `www.sbir.gov`, `www.dodsbirsttr.mil`, `seedfund.nsf.gov`, `grants.nih.gov`, `science.osti.gov`, `simpler.grants.gov`, `api.simpler.grants.gov`. Then I run the probe in the cloud and record the results. If a source still fails there it stays unavailable for the cloud runtime, and I would use a local run only to diagnose why.
2. **History scrub: complete locally and in all refs, with one limit.** The rewritten branch (4 commits) was force-pushed. Verified in the working tree, in every reachable commit (file contents, paths, messages, author fields), in every local object after pruning, and in a fresh mirror clone of the remote: zero occurrences; no tags; the remote has one branch ref. **However, the hosting service still serves the pre-rewrite commits when asked for them by full SHA**, so the identifier is still retrievable from the host by anyone who has those SHAs. I cannot purge that. To remove it you would need to file a sensitive-data removal request with the host's support, or delete and recreate the repository (I can then push the rewritten history to it). Details without the identifier are in `HISTORY_NOTE.md`.
3. Pricing for non-Anthropic models is unverified (vendor pages blocked); irrelevant to the approved split but relevant if a cheaper triage model is tried later.
4. Hardening deferred to Phase 3: container or separate OS user for the agent, pinned DNS addresses, data-at-rest encryption, hash-chained event log.
5. Phase 1 needs a PDF extraction decision (bytes in, text out, no file access).

## 6. Live sources intended next

Unchanged and still conditional on item 1: Grants.gov public API; SBIR.gov public API (solicitations and awards history); official pages for about 15 curated AI/cloud credit programs (Google, Microsoft and Anthropic startup pages already reachable; the rest need confirmed official URLs); linked solicitation and terms documents from agency portals. **No web search vendor.** Conditions per source are in `SOURCE_FEASIBILITY.md`.

## Needed from you

1. Add the ten domains to the cloud allow-list (item 1), then tell me and I will run and record the probe.
2. Decide how to handle the host's retained old commits (item 2): support request, or recreate the repository.
