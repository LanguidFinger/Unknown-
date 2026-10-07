"""Opportunity provenance/evidence model: every fact has a source, a date and a checked quote."""

from __future__ import annotations

import sqlite3

import pytest

from opportunity_operator.audit import verify_all_evidence
from opportunity_operator.errors import UnsupportedContent
from opportunity_operator.ports.fetcher import FetchResult
from opportunity_operator.store.repo import IntegrityError
from tests.conftest import PAGE_URL
from tests.support import pipeline

DEADLINE_QUOTE = "Applications are due by March 31, 2027 at 5:00 PM Eastern."


@pytest.fixture
def ran(make_app):
    app, llm, fetcher = make_app()
    oid = app.repo.create_opportunity(program_name="Synthetic AI Innovation Grant", sponsor="Synthetic Agency", url=PAGE_URL)
    result = pipeline.run(app, oid, PAGE_URL)
    return app, oid, result


def test_pipeline_records_verified_evidence_with_source_and_date(ran):
    app, oid, result = ran
    assert len(result["evidence_ids"]) == 3 and result["unverified"] == 0
    for e in app.repo.verified_evidence(oid):
        assert e["source_url"] == PAGE_URL and e["retrieved_at"] and e["quote_verified"] == 1 and e["extracted_by"]


def test_fabricated_quote_is_stored_unverified_and_excluded_from_scoring(make_app):
    def liar(prompt, schema):
        if schema.__name__ == "ExtractionOut":
            return {"items": [
                {"field": "deadline", "value": "2027-04-30", "quote": "Applications are due by April 30, 2027 at 5:00 PM Eastern."},
                {"field": "award_max", "value": "150000", "quote": "Awards of up to $150,000 are available to small businesses."},
            ]}
        return {"fit": 9, "rationale": "r"}

    app, _, _ = make_app(liar)
    oid = app.repo.create_opportunity(program_name="P", sponsor="S")
    out = pipeline.run(app, oid, PAGE_URL)
    assert out["unverified"] == 1
    fields = [e["field"] for e in app.repo.verified_evidence(oid)]
    assert fields == ["award_max"]  # the invented deadline never reaches scoring


def test_assessment_may_cite_only_verified_evidence(make_app):
    app, _, _ = make_app()
    oid = app.repo.create_opportunity(program_name="P", sponsor="S")
    sid = app.repo.save_snapshot(oid, app.fetcher.get(PAGE_URL), 1)
    bad = app.repo.add_evidence(oid, sid, "deadline", "x", "this sentence is not in the page at all", extracted_by="t")
    assert not bad.verified
    with pytest.raises(ValueError, match="quote-verified"):
        app.repo.add_assessment(oid, gate_results={}, evidence_ids=[bad.id], fit=5)


def test_superseded_evidence_drops_out_of_scoring(make_app):
    app, _, _ = make_app()
    oid = app.repo.create_opportunity(program_name="P", sponsor="S")
    sid = app.repo.save_snapshot(oid, app.fetcher.get(PAGE_URL), 1)
    old = app.repo.add_evidence(oid, sid, "deadline", "2027-03-31", DEADLINE_QUOTE, extracted_by="t")
    new = app.repo.add_evidence(oid, sid, "deadline", "2027-03-31", DEADLINE_QUOTE, extracted_by="t2")
    app.repo.supersede_evidence(old.id, new.id)
    assert [e["id"] for e in app.repo.verified_evidence(oid)] == [new.id]


def test_evidence_cannot_cite_another_opportunities_snapshot(make_app):
    app, _, _ = make_app()
    a = app.repo.create_opportunity(program_name="A", sponsor="S")
    b = app.repo.create_opportunity(program_name="B", sponsor="S")
    sid = app.repo.save_snapshot(a, app.fetcher.get(PAGE_URL), 1)
    with pytest.raises(sqlite3.DatabaseError, match="different opportunities"):
        app.repo.add_evidence(b, sid, "deadline", "x", DEADLINE_QUOTE, extracted_by="t")


def test_audit_reverifies_quotes_from_the_stored_snapshot(ran, datadir):
    app, oid, _ = ran
    rep = verify_all_evidence(app.repo.conn, datadir)
    assert rep.ok and rep.evidence_checked == 3 and rep.snapshots_checked == 1


def test_audit_detects_tampered_snapshot_file(ran, datadir):
    app, oid, _ = ran
    path = app.repo.conn.execute("SELECT text_path FROM source_snapshot").fetchone()[0]
    datadir.put_text(path, datadir.get_text(path).replace("March 31", "April 30"))
    assert not verify_all_evidence(app.repo.conn, datadir).ok
    with pytest.raises(IntegrityError):
        app.repo.add_evidence(oid, app.repo.conn.execute("SELECT id FROM source_snapshot").fetchone()[0],
                              "deadline", "x", DEADLINE_QUOTE, extracted_by="t")


def test_audit_detects_a_row_that_claims_verification_it_does_not_have(ran, datadir):
    """The DB cannot read files, so a lying writer is possible; audit is the independent check."""
    app, oid, _ = ran
    sid = app.repo.conn.execute("SELECT id FROM source_snapshot").fetchone()[0]
    app.repo.conn.execute(
        "INSERT INTO evidence (id, opportunity_id, snapshot_id, field, value, quote, quote_verified, quote_start, quote_end, "
        "extracted_by, created_at) VALUES ('forged', ?, ?, 'award_max', '9999999', 'Awards of up to $9,999,999 are available.', 1, 5, 40, 'x', 't')",
        (oid, sid))
    rep = verify_all_evidence(app.repo.conn, datadir)
    assert not rep.ok and any("forged" in f for f in rep.failures)


def test_pdf_snapshots_are_refused_rather_than_silently_unverifiable(make_app):
    app, _, _ = make_app()
    oid = app.repo.create_opportunity(program_name="P", sponsor="S")
    pdf = FetchResult("https://x.example.gov/a.pdf", "https://x.example.gov/a.pdf", 200, "application/pdf", b"%PDF-1.4", "t", "h")
    with pytest.raises(UnsupportedContent):
        app.repo.save_snapshot(oid, pdf, 3)


def test_provenance_tables_are_append_only(ran, datadir):
    app, oid, _ = ran
    from opportunity_operator.store.db import connect

    owner = connect(datadir.db_path(), "owner")
    for table in ("source_snapshot", "evidence", "assessment", "assessment_evidence", "llm_call_log", "egress_log"):
        assert owner.execute(f"SELECT count(*) FROM {table}").fetchone()[0] > 0, table
        for stmt in (f"UPDATE {table} SET rowid = rowid", f"DELETE FROM {table}"):
            for conn in (owner, app.repo.conn):
                with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                    conn.execute(stmt)
    owner.close()


def test_explain_reconstructs_why_the_system_said_what_it_said(ran):
    app, oid, _ = ran
    ex = app.repo.explain(oid)
    assert [e["to_state"] for e in ex["events"]] == ["DISCOVERED", "VERIFYING", "OWNER_REVIEW"]
    assert all(e["actor"] == "agent" for e in ex["events"])
    q = {e["field"]: e for e in ex["evidence"]}
    assert q["deadline"]["quote"] == DEADLINE_QUOTE and q["deadline"]["url"] == PAGE_URL and q["deadline"]["retrieved_at"]
    assert ex["assessments"][0]["model_id"] == "mock" and ex["assessments"][0]["rationale"]
    assert len(ex["assessment_evidence"]) == 3
    assert ex["snapshots"][0]["raw_sha256"]
