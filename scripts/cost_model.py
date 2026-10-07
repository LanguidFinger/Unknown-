"""Reproducible cost model for the Opportunity Operator pipeline.

EVERYTHING HERE IS AN ASSUMPTION, NOT A MEASUREMENT. Prices come from Anthropic's published pricing
(retrieved 2026-10-07; see docs/opportunity-operator/COST_MODEL.md for sources); token counts, funnel
rates and thinking overheads are estimates to be replaced by measured values from `run_log` and
`llm_call_log` during Phase 1. Edit the constants and re-run:

    python scripts/cost_model.py
"""

from __future__ import annotations

from dataclasses import dataclass

# model -> (input $/MTok, output $/MTok, tokens-per-text multiplier vs Haiku tokenizer, extra thinking output tokens/call)
# Source: platform.claude.com pricing page (official, retrieved 2026-10-07). Multiplier: pricing page states the
# newer tokenizer yields ~30% more tokens for the same text. Thinking overhead is our assumption at LOW effort
# (Sonnet 5.5) / MEDIUM effort (Opus 5.5, its default); Haiku 4.5 has no always-on thinking.
MODELS: dict[str, tuple[float, float, float, int]] = {
    "haiku-4.5": (1.0, 5.0, 1.0, 0),
    "sonnet-5.5": (2.0, 10.0, 1.3, 500),
    "opus-5.5": (4.0, 20.0, 1.3, 2000),
    "fable-5.1": (10.0, 50.0, 1.3, 3000),
}
BATCH_FACTOR = 0.5  # Message Batches: 50% off input and output (official)

# General web search is DISABLED (owner decision): discovery uses free government APIs/pages and curated official pages.
# If a vendor is added later, set the price and query count here (any figure for a vendor is unverified).
SEARCH_USD_PER_1K = 0.0
SEARCH_QUERIES_PER_100_CANDIDATES = 0

# Funnel assumptions per 100 discovered candidates.
PREFILTER_KEEP = 0.60   # deterministic rules (status, domain, dedupe, hard knockouts) drop 40% before any model sees them
TRIAGE_KEEP = 0.30      # cheap-model relevance triage keeps 30% of the rest
APPROVED_PER_100 = 2    # owner approves ~2 of the (max 5) queue items per 100 discovered


@dataclass(frozen=True)
class Call:
    in_tokens: int   # in Haiku-tokenizer tokens
    out_tokens: int


TRIAGE = Call(800, 120)            # title + synopsis snippet per candidate
EXTRACT = Call(36_000, 2_000)      # ~3 documents x ~12k tokens -> structured evidence JSON
TERMS_WINDOWED = Call(6_000, 1_500)   # IP/data/fees/exclusivity clause pass over keyword-hit windows only
TERMS_FULL = Call(15_000, 1_500)      # same pass over the whole terms document (no deterministic windowing)
JUDGE = Call(4_000, 1_000)         # fit assessment + skeptic over verified evidence summary
DOSSIER_CALL = Call(15_000, 3_000)  # one drafted section group; 4 per dossier
QUERY_EXPANSION = Call(2_000, 1_000)


def cost(model: str, call: Call, *, batch: bool = False, n: int = 1) -> float:
    pin, pout, mult, think = MODELS[model]
    usd = (call.in_tokens * mult * pin + (call.out_tokens + think) * pout) / 1_000_000 * n
    return usd * (BATCH_FACTOR if batch else 1.0)


@dataclass(frozen=True)
class Scenario:
    name: str
    triage: tuple[str, bool]
    extract: tuple[str, bool]
    terms: tuple[str, bool]
    judge: tuple[str, bool]
    dossier: tuple[tuple[str, int], ...]  # (model, number of the 4 calls)
    windowed_terms: bool = True


SCENARIOS = [
    Scenario("LEAN (recommended start)", ("haiku-4.5", True), ("haiku-4.5", True), ("sonnet-5.5", True), ("opus-5.5", False),
             (("sonnet-5.5", 3), ("opus-5.5", 1))),
    Scenario("BALANCED", ("haiku-4.5", True), ("sonnet-5.5", True), ("sonnet-5.5", True), ("opus-5.5", False),
             (("opus-5.5", 4),)),
    Scenario("PREMIUM", ("haiku-4.5", True), ("opus-5.5", True), ("opus-5.5", True), ("opus-5.5", False),
             (("opus-5.5", 4),)),
    Scenario("LEAN but no deterministic windowing of terms", ("haiku-4.5", True), ("haiku-4.5", True), ("sonnet-5.5", True),
             ("opus-5.5", False), (("sonnet-5.5", 3), ("opus-5.5", 1)), windowed_terms=False),
    Scenario("ALL FABLE 5.1 (for comparison only)", ("haiku-4.5", True), ("fable-5.1", True), ("fable-5.1", True),
             ("fable-5.1", False), (("fable-5.1", 4),)),
]


def evaluate(s: Scenario) -> dict[str, float]:
    discovery = SEARCH_QUERIES_PER_100_CANDIDATES * SEARCH_USD_PER_1K / 1000
    if SEARCH_QUERIES_PER_100_CANDIDATES:
        discovery += cost("haiku-4.5", QUERY_EXPANSION, batch=True)
    triaged = 100 * PREFILTER_KEEP
    triage = cost(*s.triage[:1], TRIAGE, batch=s.triage[1], n=int(triaged))
    verified = triaged * TRIAGE_KEEP
    terms_call = TERMS_WINDOWED if s.windowed_terms else TERMS_FULL
    verify_unit = (cost(s.extract[0], EXTRACT, batch=s.extract[1]) + cost(s.terms[0], terms_call, batch=s.terms[1])
                   + cost(s.judge[0], JUDGE, batch=s.judge[1]))
    dossier_unit = sum(cost(m, DOSSIER_CALL, n=k) for m, k in s.dossier)
    per_100 = discovery + triage + verified * verify_unit + APPROVED_PER_100 * dossier_unit
    return {
        "discovery_per_100": discovery, "triage_per_100": triage, "verify_unit": verify_unit,
        "verified_per_100": verified, "dossier_unit": dossier_unit, "all_in_per_100": per_100,
    }


def main() -> None:
    print(f"Funnel per 100 discovered: prefilter keeps {PREFILTER_KEEP:.0%} -> triage keeps {TRIAGE_KEEP:.0%} "
          f"-> {100 * PREFILTER_KEEP * TRIAGE_KEEP:.0f} fully verified -> <=5 to owner -> ~{APPROVED_PER_100} dossiers\n")
    print("| Scenario | Discover+triage per 100 discovered | Per opportunity fully verified | Per completed dossier | All-in per 100 discovered |")
    print("|---|---|---|---|---|")
    for s in SCENARIOS:
        r = evaluate(s)
        print(f"| {s.name} | ${r['discovery_per_100'] + r['triage_per_100']:.2f} | ${r['verify_unit']:.3f} | "
              f"${r['dossier_unit']:.2f} | ${r['all_in_per_100']:.2f} |")


if __name__ == "__main__":
    main()
