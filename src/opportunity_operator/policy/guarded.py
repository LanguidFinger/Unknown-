"""Guarded wrappers: the only way the orchestrator reaches models, search or the network.

Each wrapper (1) checks outbound text against the DisclosureFilter, (2) enforces BudgetGuard,
(3) records an audit row. Raw adapters are constructed only in the composition root (app.py).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel

from ..config import Settings
from ..errors import DisclosureBlocked, FeatureDisabled, ModelPolicyViolation
from ..ids import new_id
from ..ports.fetcher import Fetcher, FetchResult
from ..ports.llm import LLMClient, LLMResult, estimate_tokens
from ..ports.search import SearchHit, SearchProvider, SearchQuery
from .budget_guard import BudgetGuard
from .disclosure_filter import DisclosureFilter
from .prompt import Prompt


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Recorder(Protocol):
    def incident(self, channel: str, term_hash: str, context_sha: str) -> None: ...
    def llm_call(self, **kw: Any) -> None: ...
    def egress(self, **kw: Any) -> None: ...


class NullRecorder:
    def incident(self, channel: str, term_hash: str, context_sha: str) -> None: ...
    def llm_call(self, **kw: Any) -> None: ...
    def egress(self, **kw: Any) -> None: ...


class DbRecorder:
    """Writes audit rows through an agent/system connection."""

    def __init__(self, conn: sqlite3.Connection, run_id: str | None = None) -> None:
        self._c = conn
        self.run_id = run_id

    def incident(self, channel: str, term_hash: str, context_sha: str) -> None:
        self._c.execute("INSERT INTO audit_incident VALUES (?,?,?,?,?)", (new_id(), _now(), channel, term_hash, context_sha))

    def llm_call(self, **kw: Any) -> None:
        self._c.execute(
            "INSERT INTO llm_call_log (id, run_id, at, model_id, purpose, destination, prompt_sha, prompt_text, "
            "response_text, fact_ids, tokens_in, tokens_out, cost_usd, stage) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_id(), self.run_id, _now(), kw["model_id"], kw["purpose"], kw["destination"], kw["prompt_sha"],
             kw["prompt_text"], kw["response_text"], json.dumps(kw["fact_ids"]), kw["tokens_in"], kw["tokens_out"], kw["cost_usd"],
             kw["stage"]),
        )

    def egress(self, **kw: Any) -> None:
        self._c.execute(
            "INSERT INTO egress_log (id, run_id, at, kind, target, body_sha, status, bytes) VALUES (?,?,?,?,?,?,?,?)",
            (new_id(), self.run_id, _now(), kw["kind"], kw["target"], kw.get("body_sha"), kw.get("status"), kw.get("bytes")),
        )


class GuardedLLM:
    """The only route to a model. One configured client per pipeline stage; NO fallback of any kind.

    * A stage with no configured client is an error (there is no default model).
    * The client must be the model the settings assign to that stage, and the response must report the
      same model: a call silently served by another (possibly pricier) model is refused after being logged.
    * Provider errors propagate; this class never retries on, or escalates to, a different model.
    * Cost is logged per stage for every call that returned, including calls whose output was then blocked.
    """

    def __init__(self, clients: Mapping[str, LLMClient], settings: Settings, flt: DisclosureFilter, budget: BudgetGuard,
                 recorder: Recorder | None = None) -> None:
        self._clients, self._settings, self._filter, self._budget = dict(clients), settings, flt, budget
        self._rec: Recorder = recorder or NullRecorder()

    def _price(self, model_id: str) -> tuple[float, float]:
        try:
            return self._settings.model_prices[model_id]
        except KeyError:
            raise ModelPolicyViolation(f"no price configured for model {model_id!r}; refusing to call an unpriced model") from None

    def structured[M: BaseModel](self, prompt: Prompt, schema: type[M], *, max_output_tokens: int = 2000) -> LLMResult[M]:
        if not isinstance(prompt, Prompt):
            raise TypeError("LLM calls require a Prompt minted by ContextBuilder")
        stage = prompt.stage
        client = self._clients.get(stage)
        expected = self._settings.stage_models.get(stage)
        if client is None or expected is None:
            raise ModelPolicyViolation(f"no model is configured for stage {stage!r}")
        if client.model_id != expected:
            raise ModelPolicyViolation(f"stage {stage!r} is configured for {expected!r} but the client is {client.model_id!r}")
        price_in, price_out = self._price(expected)
        rendered = prompt.render()
        # Our own text must not contain deny-terms; web text is data and is not ours to police.
        self._filter.check(prompt.trusted_text(), channel="llm_prompt", recorder=self._rec)
        self._budget.precheck_llm(estimate_tokens(rendered), max_output_tokens, price_in, price_out)
        result = client.structured(prompt, schema, max_output_tokens=max_output_tokens)
        served_price = self._price(result.model_id) if result.model_id != expected else (price_in, price_out)
        cost = self._budget.charge_llm(result.usage.tokens_in, result.usage.tokens_out, *served_price)
        # A deny-term in the output that was not in the untrusted input is a possible leak/hallucination.
        blocked: DisclosureBlocked | None = None
        try:
            self._filter.check(result.raw_text, channel="llm_response", ignore=self._filter.hits(prompt.untrusted_text()),
                               recorder=self._rec)
        except DisclosureBlocked as exc:
            blocked = exc
        self._rec.llm_call(
            model_id=result.model_id, purpose=prompt.purpose.value, destination=prompt.destination.value, stage=stage,
            prompt_sha=hashlib.sha256(rendered.encode()).hexdigest(), prompt_text=rendered,
            response_text=None if blocked else result.raw_text, fact_ids=prompt.fact_ids,
            tokens_in=result.usage.tokens_in, tokens_out=result.usage.tokens_out, cost_usd=cost,
        )
        if result.model_id != expected:
            raise ModelPolicyViolation(f"stage {stage!r} was configured for {expected!r} but was served by {result.model_id!r}")
        if blocked is not None:
            raise blocked
        return result


class GuardedSearch:
    """General web search is disabled by default. The port stays so a vendor can be added later."""

    def __init__(self, inner: SearchProvider | None, settings: Settings, flt: DisclosureFilter, budget: BudgetGuard,
                 recorder: Recorder | None = None) -> None:
        self._inner, self._settings, self._filter, self._budget = inner, settings, flt, budget
        self._rec: Recorder = recorder or NullRecorder()

    def search(self, query: SearchQuery) -> list[SearchHit]:
        if not self._settings.search_enabled or self._inner is None:
            raise FeatureDisabled("general web search is disabled")
        self._filter.check(query.text, channel="search_query", recorder=self._rec)
        self._budget.charge_search()
        hits = self._inner.search(query)
        self._rec.egress(kind="search", target=query.text, body_sha=None, status=200, bytes=len(hits))
        return hits


class GuardedFetcher:
    def __init__(self, inner: Fetcher, flt: DisclosureFilter, budget: BudgetGuard, recorder: Recorder | None = None) -> None:
        self._inner, self._filter, self._budget = inner, flt, budget
        self._rec: Recorder = recorder or NullRecorder()

    def get(self, url: str) -> FetchResult:
        # A blocked URL is skipped (halt=False) rather than stopping the whole run.
        self._filter.check(url, channel="fetch_url", halt=False, recorder=self._rec)
        self._budget.charge_fetch()
        res = self._inner.get(url)
        self._rec.egress(kind="fetch", target=url, body_sha=res.sha256, status=res.status, bytes=len(res.body))
        return res

    def post_json(self, url: str, body: Mapping[str, Any]) -> FetchResult:
        text = json.dumps(body, sort_keys=True)
        self._filter.check(url + " " + text, channel="api_body", recorder=self._rec)
        self._budget.charge_fetch()
        res = self._inner.post_json(url, body)
        self._rec.egress(kind="api_post", target=url, body_sha=hashlib.sha256(text.encode()).hexdigest(),
                         status=res.status, bytes=len(res.body))
        return res
