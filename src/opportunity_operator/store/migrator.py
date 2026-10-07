"""Apply SQL migrations in order, and verify the protections are intact.

Migration files are package resources, not user data. `verify_guards` rebuilds the reference
schema in memory from those same files and compares trigger *definitions* and the frozen
lifecycle table against the live database, so a dropped or edited guard is detected (it cannot
be prevented against a process that opens the file with its own code; see store/db.py).
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import UTC, datetime
from functools import lru_cache
from importlib import resources

from .db import connect

_PKG = "opportunity_operator.store.migrations"


def _migration_files() -> list[tuple[str, str]]:
    root = resources.files(_PKG)
    items = [(p.name, p.read_text(encoding="utf-8")) for p in root.iterdir() if p.name.endswith(".sql")]
    return sorted(items)


def _apply_all(conn: sqlite3.Connection) -> list[str]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migration "
        "(name TEXT PRIMARY KEY, sha256 TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    done = {r["name"]: r["sha256"] for r in conn.execute("SELECT name, sha256 FROM schema_migration")}
    applied: list[str] = []
    for name, sql in _migration_files():
        digest = hashlib.sha256(sql.encode()).hexdigest()
        if name in done:
            if done[name] != digest:
                raise RuntimeError(f"migration {name} was modified after being applied")
            continue
        now = datetime.now(UTC).isoformat()
        try:
            conn.executescript(f"BEGIN;\n{sql}\nINSERT INTO schema_migration VALUES ('{name}', '{digest}', '{now}');\nCOMMIT;")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        applied.append(name)
    return applied


def migrate(db_path: str) -> list[str]:
    """Apply pending migrations; returns the names applied."""
    conn = connect(db_path, "migrator")
    try:
        return _apply_all(conn)
    finally:
        conn.close()


@lru_cache(maxsize=1)
def _reference() -> tuple[dict[str, str], list[tuple[str, ...]]]:
    conn = connect(":memory:", "migrator")
    try:
        _apply_all(conn)
        return _snapshot(conn)
    finally:
        conn.close()


def _snapshot(conn: sqlite3.Connection) -> tuple[dict[str, str], list[tuple[str, ...]]]:
    triggers = {r["name"]: r["sql"] for r in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger'")}
    transitions = [tuple(r) for r in conn.execute(
        "SELECT from_state, to_state, actors, COALESCE(requires_decision, '') FROM state_transition ORDER BY 1, 2")]
    return triggers, transitions


def expected_guard_triggers() -> set[str]:
    return set(_reference()[0])


def verify_guards(db_path: str) -> list[str]:
    """Return problems found (empty = every guard present and unmodified)."""
    ref_triggers, ref_transitions = _reference()
    conn = connect(db_path, "agent")
    try:
        triggers, transitions = _snapshot(conn)
    finally:
        conn.close()
    problems = [f"missing trigger {n}" for n in sorted(set(ref_triggers) - set(triggers))]
    problems += [f"unexpected trigger {n}" for n in sorted(set(triggers) - set(ref_triggers))]
    problems += [f"trigger {n} was modified" for n in sorted(set(ref_triggers) & set(triggers))
                 if re.sub(r"\s+", " ", ref_triggers[n]) != re.sub(r"\s+", " ", triggers[n])]
    if transitions != ref_transitions:
        problems.append("state_transition rows differ from the reference lifecycle")
    return problems
