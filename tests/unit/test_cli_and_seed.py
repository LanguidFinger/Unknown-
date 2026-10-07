"""CLI behaviour, the owner-confirmation requirement, and the bundled placeholder project."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from opportunity_operator.cli import app
from opportunity_operator.config import Settings
from opportunity_operator.errors import NotAuthorized
from opportunity_operator.owner import OwnerSession
from opportunity_operator.policy.access import Destination as D
from opportunity_operator.policy.access import Purpose as P
from opportunity_operator.policy.context_builder import ContextBuilder
from opportunity_operator.policy.disclosure_filter import DisclosureFilter
from opportunity_operator.seeds.loader import load_placeholder_project
from opportunity_operator.store.db import connect
from opportunity_operator.store.repo import Repository

runner = CliRunner()


def test_init_and_doctor_succeed_on_a_fresh_data_dir(tmp_path):
    d = str(tmp_path / "data")
    assert runner.invoke(app, ["init", "--data-dir", d]).exit_code == 0
    res = runner.invoke(app, ["doctor", "--data-dir", d])
    assert res.exit_code == 0 and "all guard triggers present" in res.output


def test_owner_decisions_from_the_cli_require_an_interactive_terminal(tmp_path, datadir, settings):
    repo = Repository(connect(datadir.db_path(), "agent"), datadir, settings, "agent")
    oid = repo.create_opportunity(program_name="P", sponsor="S")
    repo.transition(oid, "VERIFYING", "t")
    repo.transition(oid, "OWNER_REVIEW", "t")
    res = runner.invoke(app, ["decide", oid, "approve", "--data-dir", str(datadir.root)], input="APPROVE " + oid + "\n")
    assert res.exit_code != 0  # CliRunner has no TTY: refused even though the right text was typed
    assert repo.get_opportunity(oid)["state"] == "OWNER_REVIEW"


def test_owner_session_requires_the_confirmation_callback_to_approve(datadir, settings):
    repo = Repository(connect(datadir.db_path(), "agent"), datadir, settings, "agent")
    oid = repo.create_opportunity(program_name="P", sponsor="S")
    repo.transition(oid, "VERIFYING", "t")
    repo.transition(oid, "OWNER_REVIEW", "t")
    o = OwnerSession(datadir, settings, confirm=lambda _p: False)
    try:
        with pytest.raises(NotAuthorized):
            o.decide(oid, "approve")
    finally:
        o.close()
    assert repo.get_opportunity(oid)["state"] == "OWNER_REVIEW"
    asked: list[str] = []
    o2 = OwnerSession(datadir, settings, confirm=lambda p: (asked.append(p), True)[1])
    try:
        o2.decide(oid, "approve", reason_code="fits", notes="synthetic")
    finally:
        o2.close()
    assert repo.get_opportunity(oid)["state"] == "APPROVED" and asked == [f"APPROVE {oid}"]
    ex = repo.explain(oid)
    assert ex["decisions"][0]["decision"] == "approve" and ex["events"][-1]["actor"] == "owner"


def test_explain_command_outputs_provenance_json(tmp_path, datadir, settings):
    repo = Repository(connect(datadir.db_path(), "agent"), datadir, settings, "agent")
    oid = repo.create_opportunity(program_name="P", sponsor="S")
    res = runner.invoke(app, ["explain", oid, "--data-dir", str(datadir.root)])
    assert res.exit_code == 0 and json.loads(res.output)["opportunity"]["id"] == oid


def test_placeholder_project_is_loaded_unverified_and_reaches_no_model_until_verified(datadir, settings, owner):
    load_placeholder_project(owner)
    ctx = connect(datadir.db_path(), "context")
    builder = ContextBuilder(ctx, Settings(data_dir=datadir.root), DisclosureFilter())
    for dest in D:
        assert builder.facts(P.FIT_ASSESSMENT, dest) == [] and builder.facts(P.DRAFTING, dest) == []  # nothing verified yet
    # No invented claims: every non-description fact is the explicit string UNKNOWN.
    rows = {r["key"]: r["value"] for r in ctx.execute("SELECT key, value FROM project_fact")}
    assert rows["technical_novelty"] == rows["revenue"] == rows["customers"] == rows["patent_status"] == "UNKNOWN"
    biz = {r["key"]: r["value"] for r in ctx.execute("SELECT key, value FROM business_profile")}
    assert biz["entity_status"] == "not yet formed" and biz["ein_status"] == "not yet obtained"
    assert biz["sam_registration_status"] == "not yet completed" and "ein" not in biz and "uei" not in biz
    pid = ctx.execute("SELECT id FROM project").fetchone()[0]
    for key in rows:
        owner.verify_fact("project_fact", key, project_id=pid)
    facts = builder.facts(P.FIT_ASSESSMENT, D.CLOUD_LLM)
    assert {f.key for f in facts} == set(rows) and {f.label for f in facts} == {"Opportunity Operator Platform"}
    ctx.close()


def test_placeholder_project_has_no_owner_identity_or_old_entity_identifiers(datadir, owner):
    load_placeholder_project(owner)
    c = connect(datadir.db_path(), "context")
    assert c.execute("SELECT count(*) FROM owner_profile").fetchone()[0] == 0  # owner facts are entered locally, never bundled
    c.close()


def test_verify_fact_requires_project_id_and_scopes_to_that_project(datadir, owner):
    p1, p2 = owner.create_project("A"), owner.create_project("B")
    owner.set_project_fact(p1, "k", "v1")
    owner.set_project_fact(p2, "k", "v2")
    with pytest.raises(ValueError):
        owner.verify_fact("project_fact", "k")
    owner.verify_fact("project_fact", "k", project_id=p1)
    c = connect(datadir.db_path(), "context")
    got = {r["project_id"]: r["verified_by_owner"] for r in c.execute("SELECT project_id, verified_by_owner FROM project_fact")}
    assert got == {p1: 1, p2: 0}
    c.close()
