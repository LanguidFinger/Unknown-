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
            "response_text, fact_ids, tokens_in, tokens_out, cost_usd) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_id(), self.run_id, _now(), kw["model_id"], kw["purpose"], kw["destination"], kw["prompt_sha"],
             kw["prompt_text"], kw["response_text"], json.dumps(kw["fact_ids"]), kw["tokens_in"], kw["tokens_out"], kw["cost_usd"]),
        )

    def egress(self, **kw: Any) -> None:
        self._c.execute(
            "INSERT INTO egress_log (id, run_id, at, kind, target, body_sha, status, bytes) VALUES (?,?,?,?,?,?,?,?)",
            (new_id(), self.run_id, _now(), kw["kind"], kw["target"], kw.get("body_sha"), kw.get("status"), kw.get("bytes")),
        )


class GuardedLLM:
    def __init__(self, inner: LLMClient, flt: DisclosureFilter, budget: BudgetGuard, recorder: Recorder | None = None) -> None:
        self._inner, self._filter, self._budget = inner, flt, budget
        self._rec: Recorder = recorder or NullRecorder()

    def structured[M: BaseModel](self, prompt: Prompt, schema: type[M], *, max_output_tokens: int = 2000) -> LLMResult[M]:
        if not isinstance(prompt, Prompt):
            raise TypeError("LLM calls require a Prompt minted by ContextBuilder")
        rendered = prompt.render()
        # Our own text must not contain deny-terms; web text is data and is not ours to police.
        self._filter.check(prompt.trusted_text(), channel="llm_prompt", recorder=self._rec)
        self._budget.precheck_llm(estimate_tokens(rendered), max_output_tokens)
        result = self._inner.structured(prompt, schema, max_output_tokens=max_output_tokens)
        # A deny-term in the output that was not in the untrusted input is a possible leak/hallucination.
        ignore = self._filter.hits(prompt.untrusted_text())
        self._filter.check(result.raw_text, channel="llm_response", ignore=ignore, recorder=self._rec)
        cost = self._budget.charge_llm(result.usage.tokens_in, result.usage.tokens_out)
        self._rec.llm_call(
            model_id=result.model_id, purpose=prompt.purpose.value, destination=prompt.destination.value,
            prompt_sha=hashlib.sha256(rendered.encode()).hexdigest(), prompt_text=rendered,
            response_text=result.raw_text, fact_ids=prompt.fact_ids,
            tokens_in=result.usage.tokens_in, tokens_out=result.usage.tokens_out, cost_usd=cost,
        )
        return result


class GuardedSearch:
    def __init__(self, inner: SearchProvider, flt: DisclosureFilter, budget: BudgetGuard, recorder: Recorder | None = None) -> None:
        self._inner, self._filter, self._budget = inner, flt, budget
        self._rec: Recorder = recorder or NullRecorder()

    def search(self, query: SearchQuery) -> list[SearchHit]:
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
