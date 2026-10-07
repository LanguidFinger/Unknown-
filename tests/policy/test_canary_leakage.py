"""Canary/leakage tests: unique strings at every access level must flow only where policy allows.

Scenario A (cloud models): no CONFIDENTIAL/sensitive/unverified/ceiling-capped/restricted canary may
appear in any prompt, log, snapshot, dossier or non-profile table. Scenario B (local model): CONFIDENTIAL
facts may be used, but sensitive, unverified and RESTRICTED canaries still may not appear anywhere.
"""

from __future__ import annotations

import sqlite3

import pytest

from opportunity_operator.audit import leak_sweep
from opportunity_operator.policy.access import Destination
from tests.conftest import PAGE_URL, default_responder
from tests.support import pipeline

C = {
    "pub": "CANARY-PUBLIC-91c2", "app": "CANARY-APPSAFE-5be0", "conf": "CANARY-CONFIDENTIAL-33d7",
    "sens": "CANARY-SENSITIVE-ID-0a8f", "unver": "CANARY-UNVERIFIED-77e1", "ceil": "CANARY-CEILCAPPED-c4d9",
    "pname": "CANARY-PROJECT-INTERNAL-NAME-18ab", "restricted": "CANARY-RESTRICTED-CONTENT-6f02",
}
DENY = "Quasar Vault"


@pytest.fixture
def seeded(owner):
    owner.set_profile_fact("owner", "bio", C["pub"], level="PUBLIC", verified=True)
    owner.set_profile_fact("owner", "sensitive_identifier", C["sens"], level="PUBLIC", sensitivity="high", verified=True)
    owner.set_profile_fact("owner", "unverified_note", C["unver"], level="PUBLIC", verified=False)
    owner.set_profile_fact("business", "internal_note", C["conf"], level="CONFIDENTIAL", verified=True)
    p1 = owner.create_project(C["pname"], default_level="APPLICATION_SAFE", external_alias="Platform Project")
    owner.set_project_fact(p1, "description", C["app"], level="APPLICATION_SAFE", verified=True)
    p2 = owner.create_project("Other " + C["pname"], default_level="CONFIDENTIAL", external_alias="Other Project")
    owner.set_project_fact(p2, "ceiling_capped", C["ceil"], level="APPLICATION_SAFE", verified=True)
    owner.add_restricted_stub("restricted-area", [DENY])  # deny-terms only; the content has nowhere to go
    # The restricted *content* cannot even be stored:
    with pytest.raises(sqlite3.IntegrityError):
        owner._conn.execute("INSERT INTO project_fact (id, project_id, key, value, access_level) VALUES ('r', ?, 'k', ?, 'RESTRICTED')",
                            (p1, C["restricted"]))  # noqa: SLF001
    return C


def _run(make_app, dest):
    app, llm, _ = make_app(default_responder)
    oid = app.repo.create_opportunity(program_name="Synthetic AI Innovation Grant", sponsor="Synthetic Agency")
    pipeline.run(app, oid, PAGE_URL, fit_destination=dest)
    return app, llm, oid


def _all_prompts(app, llm):
    logged = [r["prompt_text"] for r in app.repo.conn.execute("SELECT prompt_text FROM llm_call_log")]
    return [c.rendered for c in llm.calls], logged


def test_cloud_scenario_prompts_logs_and_outputs_are_clean(make_app, seeded, datadir):
    app, llm, oid = _run(make_app, Destination.CLOUD_LLM)
    recorded, logged = _all_prompts(app, llm)
    assert recorded == logged and len(recorded) == 2
    extraction, fit = recorded
    for v in C.values():
        assert v not in extraction  # extraction is profile-blind
    assert C["pub"] in fit and C["app"] in fit
    for key in ("conf", "sens", "unver", "ceil", "pname", "restricted"):
        assert C[key] not in fit, key
    dossier = datadir.get_text(f"dossiers/{oid}/dossier.md")
    assert C["app"] in dossier and C["pub"] in dossier
    for key in ("conf", "sens", "unver", "ceil", "pname", "restricted"):
        assert C[key] not in dossier, key
    findings = leak_sweep(datadir, anywhere=[C["restricted"], DENY],
                          outside_profiles=[C[k] for k in ("conf", "sens", "unver", "ceil", "pname")])
    # DENY legitimately exists once (the stub row in restricted_stub, a profile table, raw db bytes) -- but only there:
    assert [f for f in findings if f.needle_index != 1] == [], findings
    assert {f.location.split(":")[0] for f in findings} <= {"operator.db", "operator.db-wal"}


def test_local_scenario_allows_confidential_but_never_sensitive_unverified_or_restricted(make_app, seeded, datadir):
    app, llm, oid = _run(make_app, Destination.LOCAL_LLM)
    _, fit = _all_prompts(app, llm)[0]
    assert C["conf"] in fit and C["ceil"] in fit
    for key in ("sens", "unver", "pname", "restricted"):
        assert C[key] not in fit, key
    dossier = datadir.get_text(f"dossiers/{oid}/dossier.md")
    assert C["conf"] not in dossier and C["ceil"] not in dossier  # confidential never reaches application-facing output
    findings = leak_sweep(datadir, anywhere=[C["restricted"]], outside_profiles=[C["sens"], C["unver"], C["pname"]])
    assert findings == []


def test_sweep_has_teeth_positive_and_negative_controls(make_app, seeded, datadir):
    app, llm, oid = _run(make_app, Destination.CLOUD_LLM)
    assert leak_sweep(datadir, outside_profiles=[C["conf"]]) == []  # profile tables may hold confidential data
    datadir.put_text(f"dossiers/{oid}/leaky.md", f"oops {C['conf'].lower()}")
    hits = leak_sweep(datadir, outside_profiles=[C["conf"]])
    assert hits and hits[0].location.endswith("leaky.md")
    app.repo.conn.execute("INSERT INTO run_log (id, started_at, errors) VALUES ('r1','t',?)", (C["sens"],))
    assert any(f.location == "operator.db:run_log" for f in leak_sweep(datadir, outside_profiles=[C["sens"]]))


def test_hostile_model_that_echoes_everything_it_can_see_learns_nothing_sensitive(make_app, seeded, datadir):
    """A model (or injected page) that repeats its whole prompt back can only repeat what policy gave it."""
    def echo(prompt, schema):
        if schema.__name__ == "ExtractionOut":
            return {"items": []}
        return {"fit": 5, "rationale": prompt.render()}

    app, llm, _ = make_app(echo)
    oid = app.repo.create_opportunity(program_name="P", sponsor="S")
    pipeline.run(app, oid, PAGE_URL, fit_destination=Destination.CLOUD_LLM)
    stored = " ".join(r[0] or "" for r in app.repo.conn.execute("SELECT rationale FROM assessment"))
    for key in ("conf", "sens", "unver", "ceil", "pname", "restricted"):
        assert C[key] not in stored, key
