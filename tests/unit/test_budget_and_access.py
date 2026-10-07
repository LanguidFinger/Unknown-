import pytest

from opportunity_operator.config import Settings
from opportunity_operator.errors import BudgetExceeded
from opportunity_operator.policy.access import AccessLevel, Destination, Purpose, permitted_levels
from opportunity_operator.policy.budget_guard import BudgetGuard, month_bounds, month_key


def _b(**kw):
    base = dict(max_llm_calls=2, max_tokens_in=1000, max_tokens_out=500, max_cost_usd=1.0, monthly_cap_usd=50.0, max_fetches=1, max_searches=1)
    base.update(kw)
    return BudgetGuard(**base)


def test_llm_call_cap():
    b = _b()
    for _ in range(2):
        b.precheck_llm(10, 10, 1.0, 5.0)
        b.charge_llm(10, 10, 1.0, 5.0)
    with pytest.raises(BudgetExceeded):
        b.precheck_llm(10, 10, 1.0, 5.0)


def test_token_cap_precheck():
    with pytest.raises(BudgetExceeded):
        _b().precheck_llm(2000, 10, 1.0, 5.0)


def test_per_run_cost_cap_is_a_hard_pre_flight_ceiling_and_asks_for_owner_approval():
    b = _b(max_cost_usd=0.001, max_tokens_in=10**9, max_tokens_out=10**9)
    with pytest.raises(BudgetExceeded, match="owner approval required"):
        b.precheck_llm(100_000, 100, 10.0, 50.0)


def test_cost_accumulates_against_the_run_cap():
    b = _b(max_cost_usd=0.01, max_tokens_in=10**9, max_tokens_out=10**9, max_llm_calls=100)
    b.precheck_llm(1000, 100, 2.0, 10.0)
    b.charge_llm(4000, 400, 2.0, 10.0)  # 0.012 actual, over the estimate
    with pytest.raises(BudgetExceeded):
        b.precheck_llm(10, 10, 2.0, 10.0)


def test_fetch_and_search_caps():
    b = _b()
    b.charge_fetch(); b.charge_search()
    with pytest.raises(BudgetExceeded):
        b.charge_fetch()
    with pytest.raises(BudgetExceeded):
        b.charge_search()


def test_month_helpers_handle_year_end():
    assert month_bounds("2026-12")[1].startswith("2027-01-01")
    assert month_bounds("2026-02")[0].startswith("2026-02-01") and len(month_key()) == 7


L = AccessLevel
CASES = [
    (Purpose.EXTRACTION, Destination.CLOUD_LLM, False, set()),
    (Purpose.EXTRACTION, Destination.LOCAL_LLM, True, set()),
    (Purpose.SKEPTIC, Destination.CLOUD_LLM, True, set()),
    (Purpose.QUERY_EXPANSION, Destination.CLOUD_LLM, False, {L.PUBLIC}),
    (Purpose.QUERY_EXPANSION, Destination.LOCAL_LLM, False, {L.PUBLIC}),
    (Purpose.FIT_ASSESSMENT, Destination.CLOUD_LLM, False, {L.PUBLIC, L.APPLICATION_SAFE}),
    (Purpose.FIT_ASSESSMENT, Destination.CLOUD_LLM, True, {L.PUBLIC, L.APPLICATION_SAFE, L.CONFIDENTIAL}),
    (Purpose.FIT_ASSESSMENT, Destination.LOCAL_LLM, False, {L.PUBLIC, L.APPLICATION_SAFE, L.CONFIDENTIAL}),
    (Purpose.DRAFTING, Destination.CLOUD_LLM, True, {L.PUBLIC, L.APPLICATION_SAFE}),
    (Purpose.DRAFTING, Destination.LOCAL_LLM, True, {L.PUBLIC, L.APPLICATION_SAFE}),
    (Purpose.DRAFTING, Destination.APPLICATION_OUTPUT, True, {L.PUBLIC, L.APPLICATION_SAFE}),
    (Purpose.FIT_ASSESSMENT, Destination.APPLICATION_OUTPUT, True, {L.PUBLIC, L.APPLICATION_SAFE}),
]


@pytest.mark.parametrize(("purpose", "dest", "optin", "expected"), CASES)
def test_access_matrix(purpose, dest, optin, expected):
    assert permitted_levels(dest, purpose, Settings(confidential_to_cloud_llm=optin)) == frozenset(expected)


@pytest.mark.parametrize(("purpose", "dest", "optin", "_e"), CASES)
def test_restricted_is_never_permitted(purpose, dest, optin, _e):
    assert L.RESTRICTED not in permitted_levels(dest, purpose, Settings(confidential_to_cloud_llm=optin))
