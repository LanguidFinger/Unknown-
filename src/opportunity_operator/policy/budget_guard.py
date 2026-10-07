"""Hard spending and usage caps. Exceeding any cap raises BudgetExceeded; nothing is ever 'soft'.

Two hard cost ceilings apply to every model call: a per-run cap and a per-UTC-month cap summed across
all runs from the append-only call log. Only an owner-recorded `budget_approval` row can raise either
ceiling, and only for that run or month. A pre-flight worst-case estimate (conservative token estimate
x input price + max output x output price) must fit under both ceilings before a call may start.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..config import Settings
from ..errors import BudgetExceeded


def month_key(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y-%m")


def month_bounds(month: str) -> tuple[str, str]:
    year, mon = (int(x) for x in month.split("-"))
    start = datetime(year, mon, 1, tzinfo=UTC)
    end = datetime(year + (mon == 12), 1 if mon == 12 else mon + 1, 1, tzinfo=UTC)
    return start.isoformat(), end.isoformat()


class SpendLedger:
    """Reads persisted spend and owner approvals from the database (agent/system connection)."""

    def __init__(self, conn: sqlite3.Connection, run_id: str | None, clock: Callable[[], datetime] | None = None) -> None:
        self._c, self._run_id, self._clock = conn, run_id, clock or (lambda: datetime.now(UTC))

    def month_spent(self) -> float:
        start, end = month_bounds(month_key(self._clock()))
        row = self._c.execute("SELECT COALESCE(SUM(cost_usd), 0) AS s FROM llm_call_log WHERE at >= ? AND at < ?", (start, end)).fetchone()
        return float(row["s"])

    def month_extra(self) -> float:
        row = self._c.execute("SELECT COALESCE(SUM(extra_usd), 0) AS s FROM budget_approval WHERE scope = 'month' AND period = ?",
                              (month_key(self._clock()),)).fetchone()
        return float(row["s"])

    def run_extra(self) -> float:
        if self._run_id is None:
            return 0.0
        row = self._c.execute("SELECT COALESCE(SUM(extra_usd), 0) AS s FROM budget_approval WHERE scope = 'run' AND period = ?",
                              (self._run_id,)).fetchone()
        return float(row["s"])


@dataclass
class BudgetGuard:
    max_llm_calls: int
    max_tokens_in: int
    max_tokens_out: int
    max_cost_usd: float            # per run
    monthly_cap_usd: float
    max_fetches: int
    max_searches: int
    ledger: SpendLedger | None = None
    llm_calls: int = field(default=0, init=False)
    tokens_in: int = field(default=0, init=False)
    tokens_out: int = field(default=0, init=False)
    cost_usd: float = field(default=0.0, init=False)
    fetches: int = field(default=0, init=False)
    searches: int = field(default=0, init=False)

    @classmethod
    def from_settings(cls, s: Settings, ledger: SpendLedger | None = None) -> BudgetGuard:
        return cls(s.max_llm_calls, s.max_tokens_in, s.max_tokens_out, s.max_cost_usd, s.monthly_cap_usd, s.max_fetches,
                   s.max_searches, ledger)

    @staticmethod
    def estimate_cost(tokens_in: int, tokens_out: int, price_in: float, price_out: float) -> float:
        return (tokens_in * price_in + tokens_out * price_out) / 1_000_000

    def run_ceiling(self) -> float:
        return self.max_cost_usd + (self.ledger.run_extra() if self.ledger else 0.0)

    def month_ceiling(self) -> float:
        return self.monthly_cap_usd + (self.ledger.month_extra() if self.ledger else 0.0)

    def precheck_llm(self, est_in: int, max_out: int, price_in: float, price_out: float) -> None:
        if self.llm_calls + 1 > self.max_llm_calls:
            raise BudgetExceeded("llm call cap reached")
        if self.tokens_in + est_in > self.max_tokens_in or self.tokens_out + max_out > self.max_tokens_out:
            raise BudgetExceeded("token cap would be exceeded")
        worst = self.estimate_cost(est_in, max_out, price_in, price_out)
        if self.cost_usd + worst > self.run_ceiling():
            raise BudgetExceeded(
                f"per-run cap ${self.run_ceiling():.2f} would be exceeded (spent ${self.cost_usd:.4f}, this call up to ${worst:.4f}); "
                "owner approval required to continue")
        if self.ledger is not None:
            spent = self.ledger.month_spent()
            if spent + worst > self.month_ceiling():
                raise BudgetExceeded(
                    f"monthly cap ${self.month_ceiling():.2f} would be exceeded (spent ${spent:.4f}, this call up to ${worst:.4f}); "
                    "owner approval required to continue")

    def charge_llm(self, tokens_in: int, tokens_out: int, price_in: float, price_out: float) -> float:
        cost = self.estimate_cost(tokens_in, tokens_out, price_in, price_out)
        self.llm_calls += 1
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.cost_usd += cost
        return cost

    def charge_fetch(self) -> None:
        if self.fetches + 1 > self.max_fetches:
            raise BudgetExceeded("fetch cap reached")
        self.fetches += 1

    def charge_search(self) -> None:
        if self.searches + 1 > self.max_searches:
            raise BudgetExceeded("search cap reached")
        self.searches += 1
