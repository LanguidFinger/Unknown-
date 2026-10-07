import pytest

from opportunity_operator.config import Settings
from opportunity_operator.errors import BudgetExceeded
from opportunity_operator.policy.access import AccessLevel, Destination, Purpose, permitted_levels
from opportunity_operator.policy.budget_guard import BudgetGuard


def _b(**kw):
    base = dict(max_llm_calls=2, max_tokens_in=1000, max_tokens_out=500, max_cost_usd=1.0, max_fetches=1, max_searches=1)
    base.update(kw)
    return BudgetGuard(**base)


def test_llm_call_cap():
    b = _b()
    for _ in range(2):
        b.precheck_llm(10, 10)
        b.charge_llm(10, 10)
    with pytest.raises(BudgetExceeded):
        b.precheck_llm(10, 10)


def test_token_cap_precheck():
    with pytest.raises(BudgetExceeded):
        _b().precheck_llm(2000, 10)


def test_cost_cap_precheck():
    b = _b(max_cost_usd=0.001, price_in_per_mtok=10.0, price_out_per_mtok=50.0)
    with pytest.raises(BudgetExceeded):
        b.precheck_llm(100_000, 100)


def test_fetch_and_search_caps():
    b = _b()
    b.charge_fetch(); b.charge_search()
    with pytest.raises(BudgetExceeded):
        b.charge_fetch()
    with pytest.raises(BudgetExceeded):
        b.charge_search()


L = AccessLevel
CASES = [
    # purpose, destination, confidential_opt_in, expected levels
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
