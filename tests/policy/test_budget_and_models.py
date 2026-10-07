"""Owner-approved spending limits, per-stage cost logging, no silent model fallback, search disabled."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

from opportunity_operator.adapters.fake_fetcher import FakeFetcher
from opportunity_operator.adapters.mock_llm import MockLLM, mock_clients
from opportunity_operator.adapters.mock_search import MockSearch
from opportunity_operator.app import build_app
from opportunity_operator.cli import app as cli_app
from opportunity_operator.config import DEFAULT_MODEL_PRICES, DEFAULT_STAGE_MODELS, Settings
from opportunity_operator.errors import BudgetExceeded, FeatureDisabled, ModelPolicyViolation
from opportunity_operator.ids import new_id
from opportunity_operator.owner import OwnerSession
from opportunity_operator.policy.access import Destination as D
from opportunity_operator.policy.access import Purpose as P
from opportunity_operator.ports.llm import LLMResult, Usage
from opportunity_operator.ports.search import SearchQuery
from opportunity_operator.usage import usage_by_stage
from tests.conftest import PAGE_URL
from tests.support import pipeline


class Out(BaseModel):
    text: str


def ok(prompt, schema):
    return {"text": "fine"}


def prompt_for(app, stage="extract", text="x"):
    return app.builder.make_prompt(P.EXTRACTION, D.CLOUD_LLM, "extract", untrusted=[text], schema_name="Out", stage=stage)


def insert_cost(conn, cost, at=None, model="claude-haiku-4-5", stage="extract"):
    conn.execute(
        "INSERT INTO llm_call_log (id, at, model_id, purpose, destination, prompt_sha, prompt_text, tokens_in, tokens_out, cost_usd, stage) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (new_id(), (at or datetime.now(UTC)).isoformat(), model, "EXTRACTION", "CLOUD_LLM", "s", "p", 1, 1, cost, stage))


# ---- approved defaults ---------------------------------------------------------------------------------------------
def test_approved_defaults_are_the_narrow_split_with_hard_caps():
    s = Settings()
    assert s.max_cost_usd == 5.0 and s.monthly_cap_usd == 50.0 and s.search_enabled is False
    assert s.stage_models["triage"] == s.stage_models["extract"] == "claude-haiku-4-5"
    assert s.stage_models["terms"] == "claude-sonnet-5-5" and s.stage_models["judge"] == "claude-opus-5-5"
    assert set(s.stage_models.values()) <= set(s.model_prices)
    assert "claude-fable-5-1" not in set(s.stage_models.values())  # the 2.5x-Opus model is not in the split
    assert DEFAULT_STAGE_MODELS == s.stage_models and DEFAULT_MODEL_PRICES == s.model_prices


# ---- per-run cap -----------------------------------------------------------------------------------------------------
def test_per_run_cap_stops_and_needs_owner_approval(make_app, settings, owner):
    app, llm, _ = make_app(ok, max_cost_usd=0.0001)
    with pytest.raises(BudgetExceeded, match="per-run cap.*owner approval required"):
        app.llm.structured(prompt_for(app), Out)
    assert llm.calls == []  # stopped before the model was called
    owner.approve_budget("run", app.run_id, 1.0, "synthetic test approval")
    assert app.llm.structured(prompt_for(app), Out).parsed.text == "fine"  # allowed once the owner raised THIS run's cap


def test_a_run_approval_does_not_apply_to_other_runs(make_app, owner):
    app1, _, _ = make_app(ok, max_cost_usd=0.0001)
    owner.approve_budget("run", app1.run_id, 1.0, "only for run 1")
    app2, _, _ = make_app(ok, max_cost_usd=0.0001)
    with pytest.raises(BudgetExceeded):
        app2.llm.structured(prompt_for(app2), Out)


# ---- monthly cap -------------------------------------------------------------------------------------------------------
def test_monthly_cap_counts_all_runs_this_month_and_ignores_other_months(make_app, owner):
    app, llm, _ = make_app(ok)
    last_month = datetime.now(UTC).replace(day=1) - timedelta(days=2)
    insert_cost(app.repo.conn, 400.0, at=last_month)  # a previous month never counts
    app.llm.structured(prompt_for(app), Out)
    insert_cost(app.repo.conn, 49.9999)  # other runs this month have used the budget
    with pytest.raises(BudgetExceeded, match="monthly cap \\$50.00.*owner approval required"):
        app.llm.structured(prompt_for(app, text="y" * 20000), Out)
    owner.approve_budget("month", datetime.now(UTC).strftime("%Y-%m"), 5.0, "synthetic monthly approval")
    app.llm.structured(prompt_for(app), Out)  # approved extra headroom applies to the month
    assert len(llm.calls) == 2


def test_monthly_cap_applies_across_separate_runs(make_app):
    app1, _, _ = make_app(ok)
    insert_cost(app1.repo.conn, 50.0)
    app2, llm2, _ = make_app(ok)
    with pytest.raises(BudgetExceeded, match="monthly cap"):
        app2.llm.structured(prompt_for(app2), Out)
    assert llm2.calls == []


def test_only_the_owner_can_record_budget_approvals(make_app, datadir):
    app, _, _ = make_app(ok)
    for role_conn in (app.repo.conn,):
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            role_conn.execute("INSERT INTO budget_approval VALUES (?,?,?,?,?,?)", (new_id(), "t", "month", "2026-10", 1000.0, "forged"))
    raw = sqlite3.connect(datadir.db_path())
    with pytest.raises(sqlite3.DatabaseError, match="no such function"):
        raw.execute("INSERT INTO budget_approval VALUES (?,?,?,?,?,?)", (new_id(), "t", "month", "2026-10", 1000.0, "forged"))
    raw.close()


def test_budget_approvals_are_append_only_even_for_the_owner(settings, datadir, owner):
    owner.approve_budget("month", "2026-10", 5.0, "synthetic")
    from opportunity_operator.store.db import connect

    c = connect(datadir.db_path(), "owner")
    for stmt in ("UPDATE budget_approval SET extra_usd = 9999", "DELETE FROM budget_approval"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            c.execute(stmt)
    c.close()


def test_owner_confirmation_is_required_to_approve_budget(settings, datadir):
    o = OwnerSession(datadir, settings, confirm=lambda _p: False)
    try:
        from opportunity_operator.errors import NotAuthorized

        with pytest.raises(NotAuthorized):
            o.approve_budget("month", "2026-10", 5.0, "x")
        with pytest.raises(ValueError):
            o.approve_budget("month", "2026-10", 0.0, "x")
    finally:
        o.close()


def test_cli_budget_approval_requires_a_terminal(datadir):
    r = CliRunner().invoke(cli_app, ["budget-approve", "month", "2026-10", "5", "why", "--data-dir", str(datadir.root)], input="x\n")
    assert r.exit_code != 0


# ---- cost by stage -----------------------------------------------------------------------------------------------------
def test_cost_is_logged_per_stage_and_model_and_rolls_up_to_the_run(make_app):
    app, _, _ = make_app()
    oid = app.repo.create_opportunity(program_name="P", sponsor="S")
    pipeline.run(app, oid, PAGE_URL)
    rows = {(u.stage, u.model_id): u for u in usage_by_stage(app.repo.conn)}
    assert set(rows) == {("extract", "claude-haiku-4-5"), ("judge", "claude-opus-5-5")}
    assert all(u.calls == 1 and u.cost_usd > 0 and u.tokens_in > 0 for u in rows.values())
    logged = app.repo.conn.execute("SELECT sum(cost_usd) FROM llm_call_log").fetchone()[0]
    assert logged == pytest.approx(sum(u.cost_usd for u in rows.values()))
    app.finish()
    rl = app.repo.conn.execute("SELECT status, cost_usd, tokens_in, finished_at FROM run_log WHERE id = ?", (app.run_id,)).fetchone()
    assert rl["status"] == "finished" and rl["cost_usd"] == pytest.approx(logged) and rl["finished_at"]


def test_usage_command_reports_stage_costs(make_app, datadir):
    app, _, _ = make_app()
    oid = app.repo.create_opportunity(program_name="P", sponsor="S")
    pipeline.run(app, oid, PAGE_URL)
    r = CliRunner().invoke(cli_app, ["usage", "--data-dir", str(datadir.root)])
    assert r.exit_code == 0 and "extract" in r.output and "judge" in r.output and "monthly cap" in r.output


# ---- no silent fallback --------------------------------------------------------------------------------------------
def test_a_failing_cheap_model_is_not_retried_on_a_pricier_one(settings):
    def boom(prompt, schema):
        raise RuntimeError("provider outage")

    clients, calls = mock_clients(boom, settings.stage_models)
    app = build_app(settings, llms=clients, fetcher=FakeFetcher({}))
    with pytest.raises(RuntimeError, match="provider outage"):
        app.llm.structured(prompt_for(app, "extract"), Out)
    assert [c.schema for c in calls] == ["Out"] and len(calls) == 1  # exactly one attempt, on the configured model only
    assert app.repo.conn.execute("SELECT count(*) FROM llm_call_log").fetchone()[0] == 0  # nothing billed, nothing escalated
    app.close()


def test_stage_without_a_configured_client_is_an_error_not_a_default(settings):
    clients, _ = mock_clients(ok, settings.stage_models)
    del clients["extract"]
    app = build_app(settings, llms=clients, fetcher=FakeFetcher({}))
    with pytest.raises(ModelPolicyViolation, match="no model is configured"):
        app.llm.structured(prompt_for(app, "extract"), Out)
    with pytest.raises(ModelPolicyViolation):
        app.llm.structured(prompt_for(app, "stage_that_does_not_exist"), Out)
    app.close()


def test_client_must_be_the_model_configured_for_its_stage(settings):
    clients, _ = mock_clients(ok, settings.stage_models)
    clients["extract"] = MockLLM(ok, model_id="claude-opus-5-5")  # a pricier model wired into a cheap stage
    app = build_app(settings, llms=clients, fetcher=FakeFetcher({}))
    with pytest.raises(ModelPolicyViolation, match="configured for 'claude-haiku-4-5'"):
        app.llm.structured(prompt_for(app, "extract"), Out)
    app.close()


def test_unpriced_models_are_refused_before_any_call(settings):
    s = settings.model_copy(update={"stage_models": {**settings.stage_models, "extract": "claude-mystery-1"}})
    clients, calls = mock_clients(ok, s.stage_models)
    app = build_app(s, llms=clients, fetcher=FakeFetcher({}))
    with pytest.raises(ModelPolicyViolation, match="no price configured"):
        app.llm.structured(prompt_for(app, "extract"), Out)
    assert calls == []
    app.close()


def test_a_call_served_by_a_different_model_is_logged_at_its_real_cost_then_refused(settings):
    class Sneaky(MockLLM):
        def structured(self, prompt, schema, *, max_output_tokens):
            return LLMResult(schema.model_validate({"text": "x"}), Usage(1000, 1000), "claude-opus-5-5", '{"text":"x"}')

    clients, _ = mock_clients(ok, settings.stage_models)
    clients["extract"] = Sneaky(ok, model_id="claude-haiku-4-5")  # reports haiku, but the provider served opus (e.g. server-side fallback)
    app = build_app(settings, llms=clients, fetcher=FakeFetcher({}))
    with pytest.raises(ModelPolicyViolation, match="served by 'claude-opus-5-5'"):
        app.llm.structured(prompt_for(app, "extract"), Out)
    row = app.repo.conn.execute("SELECT model_id, cost_usd, stage FROM llm_call_log").fetchone()
    assert row["model_id"] == "claude-opus-5-5" and row["stage"] == "extract"
    assert row["cost_usd"] == pytest.approx((1000 * 4.0 + 1000 * 20.0) / 1e6)  # charged at the pricier model's rate
    app.close()


# ---- web search is disabled -------------------------------------------------------------------------------------------
def test_search_is_disabled_by_default_even_if_a_provider_is_wired(settings):
    clients, _ = mock_clients(ok, settings.stage_models)
    provider = MockSearch()
    app = build_app(settings, llms=clients, fetcher=FakeFetcher({}), search=provider)
    with pytest.raises(FeatureDisabled):
        app.search.search(SearchQuery("small business AI grants"))
    assert provider.queries == [] and app.budget.searches == 0
    app.close()


def test_search_needs_both_the_flag_and_a_provider(settings):
    clients, _ = mock_clients(ok, settings.stage_models)
    enabled = settings.model_copy(update={"search_enabled": True})
    no_provider = build_app(enabled, llms=clients, fetcher=FakeFetcher({}))
    with pytest.raises(FeatureDisabled):
        no_provider.search.search(SearchQuery("q"))
    no_provider.close()
    provider = MockSearch()
    with_provider = build_app(enabled, llms=clients, fetcher=FakeFetcher({}), search=provider)
    with_provider.search.search(SearchQuery("q"))
    assert provider.queries == ["q"]  # the interface works once deliberately enabled
    with_provider.close()


def test_no_search_vendor_adapter_or_host_exists_in_the_codebase():
    from pathlib import Path

    import opportunity_operator

    src = Path(opportunity_operator.__file__).parent
    vendors = ("tavily", "serpapi", "serper", "brave", "bing.com", "api.exa", "customsearch", "googleapis.com/customsearch", "duckduckgo")
    hits = [(p.name, v) for p in src.rglob("*") if p.is_file() and p.suffix in {".py", ".yaml", ".sql"}
            for v in vendors if v in p.read_text(errors="ignore").lower()]
    assert hits == []
    assert set(Settings().api_allow_hosts) == {"api.grants.gov", "api.www.sbir.gov"}
