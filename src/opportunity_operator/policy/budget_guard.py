"""Hard per-run caps. Exceeding any cap raises BudgetExceeded; nothing is ever 'soft'."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Settings
from ..errors import BudgetExceeded


@dataclass
class BudgetGuard:
    max_llm_calls: int
    max_tokens_in: int
    max_tokens_out: int
    max_cost_usd: float
    max_fetches: int
    max_searches: int
    price_in_per_mtok: float = 0.0
    price_out_per_mtok: float = 0.0
    llm_calls: int = field(default=0, init=False)
    tokens_in: int = field(default=0, init=False)
    tokens_out: int = field(default=0, init=False)
    cost_usd: float = field(default=0.0, init=False)
    fetches: int = field(default=0, init=False)
    searches: int = field(default=0, init=False)

    @classmethod
    def from_settings(cls, s: Settings, *, price_in: float = 0.0, price_out: float = 0.0) -> BudgetGuard:
        return cls(s.max_llm_calls, s.max_tokens_in, s.max_tokens_out, s.max_cost_usd, s.max_fetches,
                   s.max_searches, price_in, price_out)

    def estimate_cost(self, tokens_in: int, tokens_out: int) -> float:
        return (tokens_in * self.price_in_per_mtok + tokens_out * self.price_out_per_mtok) / 1_000_000

    def precheck_llm(self, est_in: int, max_out: int) -> None:
        if self.llm_calls + 1 > self.max_llm_calls:
            raise BudgetExceeded("llm call cap reached")
        if self.tokens_in + est_in > self.max_tokens_in or self.tokens_out + max_out > self.max_tokens_out:
            raise BudgetExceeded("token cap would be exceeded")
        if self.cost_usd + self.estimate_cost(est_in, max_out) > self.max_cost_usd:
            raise BudgetExceeded("cost cap would be exceeded")

    def charge_llm(self, tokens_in: int, tokens_out: int) -> float:
        cost = self.estimate_cost(tokens_in, tokens_out)
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
