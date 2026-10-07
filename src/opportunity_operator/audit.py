"""Independent integrity checks: evidence re-verification and the data-directory leakage sweep.

The sweep reads raw files and the raw database (read-only) so it does not depend on the
application's own protections.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field

from .safe_fs import DataDir
from .store.db import PROFILE_TABLES
from .text import canonical_text


@dataclass
class EvidenceAuditReport:
    snapshots_checked: int = 0
    evidence_checked: int = 0
    unverified_rows: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def verify_all_evidence(conn: sqlite3.Connection, datadir: DataDir) -> EvidenceAuditReport:
    rep = EvidenceAuditReport()
    texts: dict[str, str] = {}
    for s in conn.execute("SELECT id, raw_path, text_path, raw_sha256, text_sha256 FROM source_snapshot"):
        rep.snapshots_checked += 1
        try:
            raw, text = datadir.get_bytes(s["raw_path"]), datadir.get_text(s["text_path"])
        except OSError:
            rep.failures.append(f"snapshot {s['id']}: file missing")
            continue
        if hashlib.sha256(raw).hexdigest() != s["raw_sha256"]:
            rep.failures.append(f"snapshot {s['id']}: raw file hash mismatch")
        if hashlib.sha256(text.encode()).hexdigest() != s["text_sha256"]:
            rep.failures.append(f"snapshot {s['id']}: text hash mismatch")
        else:
            texts[s["id"]] = text
    for e in conn.execute("SELECT id, snapshot_id, quote, quote_verified, quote_start, quote_end FROM evidence"):
        rep.evidence_checked += 1
        if not e["quote_verified"]:
            rep.unverified_rows += 1
            continue
        text = texts.get(e["snapshot_id"])
        if text is None or text[e["quote_start"]:e["quote_end"]] != canonical_text(e["quote"]):
            rep.failures.append(f"evidence {e['id']}: marked verified but quote not found at recorded offsets")
    return rep


@dataclass(frozen=True)
class LeakFinding:
    location: str
    needle_index: int


def _variants(needle: str) -> list[bytes]:
    return [needle.encode("utf-8"), needle.encode("utf-16-le"), needle.lower().encode("utf-8")]


def leak_sweep(datadir: DataDir, *, anywhere: Iterable[str] = (), outside_profiles: Iterable[str] = ()) -> list[LeakFinding]:
    """Search the whole data directory for canary strings.

    `anywhere`: must appear nowhere at all, including raw database/WAL bytes (e.g. RESTRICTED content).
    `outside_profiles`: may exist in profile tables but nowhere else (logs, snapshots, dossiers, other tables).
    """
    anywhere_l, outside_l = list(anywhere), list(outside_profiles)
    findings: list[LeakFinding] = []

    def scan(blob: bytes, where: str, needles: list[str], offset: int) -> None:
        low = blob.lower()
        for i, n in enumerate(needles):
            if any(v in blob or v in low for v in _variants(n)):
                findings.append(LeakFinding(where, offset + i))

    for path in datadir.walk_files():
        rel = str(path.relative_to(datadir.root))
        blob = path.read_bytes()
        is_db_file = rel.endswith((".db", ".db-wal", ".db-shm"))
        scan(blob, rel, anywhere_l, 0)
        if not is_db_file:
            scan(blob, rel, outside_l, len(anywhere_l))
    for path in datadir.walk_files():
        if not str(path).endswith(".db"):
            continue
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for t in tables:
                if t in PROFILE_TABLES:
                    continue
                for row in conn.execute(f'SELECT * FROM "{t}"'):  # noqa: S608
                    blob = "\x1f".join("" if v is None else str(v) for v in row).encode("utf-8", "replace")
                    scan(blob, f"{path.name}:{t}", outside_l, len(anywhere_l))
        finally:
            conn.close()
    return findings
