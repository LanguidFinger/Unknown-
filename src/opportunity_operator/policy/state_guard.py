"""Python-side view of the lifecycle table. Enforcement itself lives in DB triggers (0002)."""

from __future__ import annotations

import sqlite3

OWNER_ONLY_TARGETS = frozenset(
    {"APPROVED", "DECLINED", "SUBMITTED", "AWARDED", "NOT_AWARDED", "WITHDRAWN"}
)


def legal_next_states(conn: sqlite3.Connection, from_state: str | None, actor: str) -> list[str]:
    rows = conn.execute(
        "SELECT to_state FROM state_transition WHERE from_state = ? AND instr(actors, ',' || ? || ',') > 0 ORDER BY to_state",
        (from_state or "<none>", actor),
    )
    return [r["to_state"] for r in rows]
