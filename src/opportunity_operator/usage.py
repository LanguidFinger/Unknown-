"""Model usage and cost by pipeline stage, from the append-only call log."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .policy.budget_guard import month_bounds, month_key


@dataclass(frozen=True)
class StageUsage:
    stage: str
    model_id: str
    calls: int
    tokens_in: int
    tokens_out: int
    cost_usd: float

    @property
    def avg_cost_per_call(self) -> float:
        return self.cost_usd / self.calls if self.calls else 0.0


def usage_by_stage(conn: sqlite3.Connection, month: str | None = None) -> list[StageUsage]:
    start, end = month_bounds(month or month_key())
    rows = conn.execute(
        "SELECT COALESCE(stage, '(unlabelled)') AS stage, model_id, count(*) AS calls, COALESCE(sum(tokens_in), 0) AS ti, "
        "COALESCE(sum(tokens_out), 0) AS tout, COALESCE(sum(cost_usd), 0) AS cost FROM llm_call_log "
        "WHERE at >= ? AND at < ? GROUP BY stage, model_id ORDER BY cost DESC",
        (start, end),
    )
    return [StageUsage(r["stage"], r["model_id"], r["calls"], r["ti"], r["tout"], r["cost"]) for r in rows]
