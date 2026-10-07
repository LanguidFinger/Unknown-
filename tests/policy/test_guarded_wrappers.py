from __future__ import annotations

import pytest
from pydantic import BaseModel

from opportunity_operator.adapters.fake_fetcher import FakeFetcher
from opportunity_operator.adapters.mock_llm import MockLLM
from opportunity_operator.adapters.mock_search import MockSearch
from opportunity_operator.app import build_app
from opportunity_operator.errors import BudgetExceeded, DisclosureBlocked
from opportunity_operator.policy.access import Destination as D
from opportunity_operator.policy.access import Purpose as P
from opportunity_operator.ports.search import SearchQuery
from tests.conftest import PAGE_HTML, PAGE_URL


class Out(BaseModel):
    text: str


def _app(settings, owner, responder=None, **kw):
    owner.add_restricted_stub("restricted-area", ["Project Sentinel X"])
    llm = MockLLM(responder or (lambda p, s: {"text": "fine"}))
    search = MockSearch()
    fetcher = FakeFetcher({PAGE_URL: ("text/html", PAGE_HTML)})
    from dataclasses import replace  # noqa: F401
    app = build_app(settings.model_copy(update=kw), llm=llm, search=search, fetcher=fetcher, price_in_per_mtok=1.0, price_out_per_mtok=5.0)
    return app, llm, search, fetcher


def test_llm_rejects_raw_strings(settings, owner):
    app, *_ = _app(settings, owner)
    with pytest.raises(TypeError):
        app.llm.structured("just a string", Out)  # type: ignore[arg-type]
    app.close()


def test_deny_term_in_search_query_is_blocked_before_leaving_and_incident_has_only_a_hash(settings, owner):
    app, _, search, _ = _app(settings, owner)
    with pytest.raises(DisclosureBlocked) as e:
        app.search.search(SearchQuery("grants for Project Sentinel X style platforms"))
    assert e.value.halt is True and search.queries == []  # never reached the provider
    row = app.repo.conn.execute("SELECT channel, term_hash, context_sha FROM audit_incident").fetchone()
    assert row["channel"] == "search_query" and "sentinel" not in " ".join(map(str, tuple(row))).lower()
    app.close()


def test_deny_term_in_url_skips_fetch_without_halting(settings, owner):
    app, _, _, fetcher = _app(settings, owner)
    with pytest.raises(DisclosureBlocked) as e:
        app.fetcher.get("https://x.example.gov/project-sentinel-x")
    assert e.value.halt is False and fetcher.requested == []
    app.close()


def test_deny_term_in_trusted_prompt_text_is_blocked(settings, owner):
    app, llm, *_ = _app(settings, owner)
    with pytest.raises(DisclosureBlocked):
        app.builder.make_prompt(P.FIT_ASSESSMENT, D.CLOUD_LLM, "Compare with Project Sentinel X", schema_name="Out")
    assert llm.calls == []
    app.close()


def test_page_that_mentions_a_deny_term_is_data_not_a_leak(settings, owner):
    app, llm, *_ = _app(settings, owner)
    p = app.builder.make_prompt(P.EXTRACTION, D.CLOUD_LLM, "extract", untrusted=["a page about project sentinel x"], schema_name="Out")
    assert app.llm.structured(p, Out).parsed.text == "fine"  # input mention is not our disclosure
    app.close()


def test_model_emitting_a_deny_term_that_was_not_in_its_input_is_halted(settings, owner):
    app, llm, *_ = _app(settings, owner, responder=lambda p, s: {"text": "as seen in project sentinel x docs"})
    p = app.builder.make_prompt(P.EXTRACTION, D.CLOUD_LLM, "extract", untrusted=["innocent page"], schema_name="Out")
    with pytest.raises(DisclosureBlocked) as e:
        app.llm.structured(p, Out)
    assert e.value.halt
    assert app.repo.conn.execute("SELECT count(*) FROM llm_call_log").fetchone()[0] == 0  # blocked output is not logged
    app.close()


def test_post_body_is_scanned(settings, owner):
    app, *_ = _app(settings, owner)
    with pytest.raises(DisclosureBlocked):
        app.fetcher.post_json("https://api.grants.gov/v1/api/search2", {"keyword": "project sentinel x"})
    app.close()


def test_budget_caps_stop_the_run(settings, owner):
    app, llm, search, fetcher = _app(settings, owner, max_llm_calls=1, max_searches=1, max_fetches=1)
    p = app.builder.make_prompt(P.EXTRACTION, D.CLOUD_LLM, "extract", untrusted=["x"], schema_name="Out")
    app.llm.structured(p, Out)
    with pytest.raises(BudgetExceeded):
        app.llm.structured(p, Out)
    app.search.search(SearchQuery("small business AI grants"))
    with pytest.raises(BudgetExceeded):
        app.search.search(SearchQuery("another query"))
    app.fetcher.get(PAGE_URL)
    with pytest.raises(BudgetExceeded):
        app.fetcher.get(PAGE_URL)
    app.close()


def test_every_call_is_logged_for_audit(settings, owner):
    app, *_ = _app(settings, owner)
    p = app.builder.make_prompt(P.EXTRACTION, D.CLOUD_LLM, "extract", untrusted=["x"], schema_name="Out")
    app.llm.structured(p, Out)
    app.fetcher.get(PAGE_URL)
    app.search.search(SearchQuery("small business AI grants"))
    c = app.repo.conn
    assert c.execute("SELECT count(*) FROM llm_call_log").fetchone()[0] == 1
    assert {r["kind"] for r in c.execute("SELECT kind FROM egress_log")} == {"fetch", "search"}
    app.close()
