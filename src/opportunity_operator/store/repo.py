"""Repositories for opportunities, snapshots, evidence, assessments and lifecycle events.

Evidence integrity rule: `quote_verified` is computed here by re-reading the stored snapshot text
and locating the quote verbatim; callers cannot assert it. Offsets are stored so `audit` can
re-verify later. The database cannot read files, so triggers cannot check content; the audit
module closes that gap by re-verification.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from ..config import Settings
from ..errors import OperatorError, UnsupportedContent
from ..ids import new_id
from ..ports.fetcher import FetchResult
from ..safe_fs import DataDir
from ..text import canonical_text, find_quote, html_to_text

_TEXT_TYPES = {"text/html", "application/xhtml+xml"}
_PLAIN_TYPES = {"application/json", "text/plain", "application/xml", "text/xml"}


class IntegrityError(OperatorError):
    """Stored snapshot no longer matches its recorded hash."""


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def make_canonical_key(sponsor: str | None, program_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", f"{sponsor or ''} {program_name}".lower()).strip("-")


@dataclass(frozen=True)
class EvidenceRecord:
    id: str
    verified: bool


class Repository:
    def __init__(self, conn: sqlite3.Connection, datadir: DataDir, settings: Settings, role: str) -> None:
        self.conn, self.datadir, self.settings, self.role = conn, datadir, settings, role

    # ---- opportunities ------------------------------------------------------
    def create_opportunity(
        self, *, program_name: str, sponsor: str | None = None, url: str | None = None,
        instrument_type: str | None = None, canonical_key: str | None = None, tags: tuple[str, ...] = (),
    ) -> str:
        oid = new_id()
        self.conn.execute(
            "INSERT INTO opportunity (id, canonical_key, program_name, sponsor, primary_url, instrument_type, "
            "first_discovered, tags) VALUES (?,?,?,?,?,?,?,?)",
            (oid, canonical_key or make_canonical_key(sponsor, program_name), program_name, sponsor, url,
             instrument_type, now_iso(), json.dumps(list(tags))),
        )
        return oid

    def get_opportunity(self, oid: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM opportunity WHERE id = ?", (oid,)).fetchone()
        if row is None:
            raise KeyError(oid)
        return cast(sqlite3.Row, row)

    def transition(self, oid: str, to_state: str, reason: str, *, decision_id: str | None = None,
                   assessment_id: str | None = None) -> str:
        current = self.get_opportunity(oid)["state"]
        eid = new_id()
        self.conn.execute(
            "INSERT INTO status_event (id, opportunity_id, at, from_state, to_state, actor, reason, assessment_id, decision_id) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (eid, oid, now_iso(), current, to_state, self.role, reason, assessment_id, decision_id),
        )
        return eid

    # ---- snapshots ----------------------------------------------------------
    def save_snapshot(self, oid: str, fetch: FetchResult, source_tier: int) -> str:
        ctype = fetch.content_type
        if ctype in _TEXT_TYPES:
            text = html_to_text(fetch.body.decode("utf-8", errors="replace"))
        elif ctype in _PLAIN_TYPES:
            text = fetch.body.decode("utf-8", errors="replace")
        else:
            raise UnsupportedContent(f"{ctype or 'unknown'} snapshots are not supported yet (PDF extraction is Phase 1)")
        canon = canonical_text(text)
        raw_sha = hashlib.sha256(fetch.body).hexdigest()
        text_sha = hashlib.sha256(canon.encode()).hexdigest()
        sid = new_id()
        base = f"snapshots/{oid}/{sid}"
        self.datadir.put_bytes(f"{base}.raw", fetch.body)
        self.datadir.put_text(f"{base}.txt", canon)
        self.conn.execute(
            "INSERT INTO source_snapshot (id, opportunity_id, url, source_tier, content_type, http_status, raw_sha256, "
            "text_sha256, raw_path, text_path, retrieved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (sid, oid, fetch.final_url, source_tier, ctype, fetch.status, raw_sha, text_sha, f"{base}.raw", f"{base}.txt",
             fetch.retrieved_at),
        )
        return sid

    def snapshot_text(self, snapshot_id: str) -> str:
        row = self.conn.execute("SELECT text_path, text_sha256 FROM source_snapshot WHERE id = ?", (snapshot_id,)).fetchone()
        if row is None:
            raise KeyError(snapshot_id)
        text = self.datadir.get_text(row["text_path"])
        if hashlib.sha256(text.encode()).hexdigest() != row["text_sha256"]:
            raise IntegrityError(f"snapshot {snapshot_id} text does not match its recorded hash")
        return text

    # ---- evidence -----------------------------------------------------------
    def add_evidence(self, oid: str, snapshot_id: str, field: str, value: str | None, quote: str, *,
                     confidence: str | None = None, extracted_by: str) -> EvidenceRecord:
        match = find_quote(self.snapshot_text(snapshot_id), quote, min_chars=self.settings.min_quote_chars)
        eid = new_id()
        self.conn.execute(
            "INSERT INTO evidence (id, opportunity_id, snapshot_id, field, value, quote, quote_verified, quote_start, "
            "quote_end, confidence, extracted_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, oid, snapshot_id, field, value, quote, 1 if match else 0, match.start if match else None,
             match.end if match else None, confidence, extracted_by, now_iso()),
        )
        return EvidenceRecord(eid, match is not None)

    def supersede_evidence(self, old_id: str, new_id_: str) -> None:
        self.conn.execute("INSERT INTO evidence_supersession VALUES (?,?,?)", (old_id, new_id_, now_iso()))

    def verified_evidence(self, oid: str) -> list[sqlite3.Row]:
        """The ONLY evidence scoring may use: quote-verified and not superseded."""
        return self.conn.execute(
            "SELECT e.*, s.url AS source_url, s.source_tier, s.retrieved_at FROM evidence e "
            "JOIN source_snapshot s ON s.id = e.snapshot_id "
            "WHERE e.opportunity_id = ? AND e.quote_verified = 1 "
            "AND e.id NOT IN (SELECT evidence_id FROM evidence_supersession) ORDER BY e.field, e.created_at",
            (oid,),
        ).fetchall()

    # ---- assessments --------------------------------------------------------
    def add_assessment(self, oid: str, *, gate_results: dict[str, Any], evidence_ids: list[str],
                       model_id: str | None = None, prompt_version: str | None = None, rules_version: str | None = None,
                       **fields: Any) -> str:
        allowed = {"value", "fit", "effort", "probability_band", "probability_confidence", "probability_basis", "ip_risk",
                   "privacy_risk", "legal_review_required", "priority_score", "recommendation",
                   "why_it_deserves_attention", "downgrade_reason", "rationale"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown assessment fields: {sorted(unknown)}")
        verified = {r["id"] for r in self.verified_evidence(oid)}
        if not set(evidence_ids) <= verified:
            raise ValueError("assessments may cite only quote-verified, non-superseded evidence")
        aid = new_id()
        cols = ["id", "opportunity_id", "created_at", "gate_results", "model_id", "prompt_version", "rules_version", *fields]
        vals = [aid, oid, now_iso(), json.dumps(gate_results), model_id, prompt_version, rules_version, *fields.values()]
        self.conn.execute(
            f"INSERT INTO assessment ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals  # noqa: S608
        )
        for ev in evidence_ids:
            self.conn.execute("INSERT INTO assessment_evidence VALUES (?,?)", (aid, ev))
        return aid

    # ---- provenance ---------------------------------------------------------
    def explain(self, oid: str) -> dict[str, Any]:
        """Why did the system say this? Everything needed to reconstruct the recommendation."""
        def rows(query: str) -> list[dict[str, Any]]:
            return [dict(r) for r in self.conn.execute(query, (oid,))]

        return {
            "opportunity": dict(self.get_opportunity(oid)),
            "snapshots": rows("SELECT id, url, source_tier, content_type, raw_sha256, retrieved_at FROM source_snapshot WHERE opportunity_id = ?"),
            "evidence": rows(
                "SELECT e.id, e.field, e.value, e.quote, e.quote_verified, e.confidence, e.extracted_by, s.url, s.retrieved_at, "
                "(SELECT superseded_by FROM evidence_supersession x WHERE x.evidence_id = e.id) AS superseded_by "
                "FROM evidence e JOIN source_snapshot s ON s.id = e.snapshot_id WHERE e.opportunity_id = ? ORDER BY e.field"),
            "assessments": rows("SELECT * FROM assessment WHERE opportunity_id = ? ORDER BY created_at"),
            "assessment_evidence": rows(
                "SELECT ae.assessment_id, ae.evidence_id FROM assessment_evidence ae JOIN assessment a ON a.id = ae.assessment_id "
                "WHERE a.opportunity_id = ?"),
            "events": rows("SELECT at, from_state, to_state, actor, reason, decision_id FROM status_event WHERE opportunity_id = ? ORDER BY rowid"),
            "decisions": rows("SELECT at, decision, reason_code, notes FROM decision WHERE opportunity_id = ? ORDER BY rowid"),
        }
