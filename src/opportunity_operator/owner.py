"""Owner authority. The ONLY module that opens an owner-role connection.

Every state-changing decision needs a confirmation callback to return True. The CLI supplies an
interactive typed confirmation that requires a TTY; library callers must pass their own callback,
so nothing in the agent pipeline can obtain owner authority by accident. The pipeline modules
must never import this module (enforced by tests/isolation/test_architecture.py).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Literal

from .config import Settings
from .errors import NotAuthorized
from .ids import new_id
from .safe_fs import DataDir
from .store.db import connect, tx
from .store.repo import Repository, now_iso

Confirm = Callable[[str], bool]

DECISION_TARGET = {
    "approve": "APPROVED", "decline": "DECLINED", "watch": "WATCHLIST", "request_changes": "PREPARING",
    "mark_submitted": "SUBMITTED", "withdraw": "WITHDRAWN", "mark_awarded": "AWARDED",
    "mark_not_awarded": "NOT_AWARDED", "reopen": "VERIFYING",
}
Level = Literal["PUBLIC", "APPLICATION_SAFE", "CONFIDENTIAL"]


class OwnerSession:
    def __init__(self, datadir: DataDir, settings: Settings, confirm: Confirm) -> None:
        self._conn = connect(datadir.db_path(), "owner")
        self._repo = Repository(self._conn, datadir, settings, "owner")
        self._confirm = confirm

    def close(self) -> None:
        self._conn.close()

    def _require(self, prompt: str) -> None:
        if not self._confirm(prompt):
            raise NotAuthorized("owner confirmation declined")

    # ---- lifecycle decisions ------------------------------------------------
    def decide(self, opportunity_id: str, decision: str, *, reason_code: str | None = None, notes: str | None = None) -> str:
        if decision not in DECISION_TARGET:
            raise ValueError(f"unknown decision {decision!r}")
        self._require(f"{decision.upper()} {opportunity_id}")
        did = new_id()
        with tx(self._conn):
            self._conn.execute(
                "INSERT INTO decision (id, opportunity_id, at, actor, decision, reason_code, notes) VALUES (?,?,?,?,?,?,?)",
                (did, opportunity_id, now_iso(), "owner", decision, reason_code, notes),
            )
            self._repo.transition(opportunity_id, DECISION_TARGET[decision], f"owner decision: {decision}", decision_id=did)
        return did

    # ---- prerequisites ------------------------------------------------------
    def set_prerequisite(self, key: str, status: str, notes: str | None = None) -> None:
        if status == "done":
            self._require(f"MARK PREREQUISITE DONE {key}")
        self._conn.execute("UPDATE prerequisite SET status = ?, notes = COALESCE(?, notes), updated_at = ? WHERE key = ?",
                           (status, notes, now_iso(), key))

    # ---- restricted stubs (deny-terms only; never content) -------------------
    def add_restricted_stub(self, label: str, deny_terms: list[str]) -> str:
        sid = new_id()
        self._conn.execute("INSERT INTO restricted_stub VALUES (?,?,?,?)", (sid, label, json.dumps(deny_terms), now_iso()))
        return sid

    # ---- profiles -----------------------------------------------------------
    def set_profile_fact(self, scope: Literal["owner", "business"], key: str, value: str | None, *,
                         level: Level = "CONFIDENTIAL", sensitivity: Literal["normal", "high"] = "normal",
                         verified: bool = False, source: str | None = None) -> None:
        table = "owner_profile" if scope == "owner" else "business_profile"
        self._conn.execute(
            f"INSERT INTO {table} (key, value, sensitivity, access_level, source, verified_by_owner, updated_at) "  # noqa: S608
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, sensitivity=excluded.sensitivity, "
            "access_level=excluded.access_level, source=excluded.source, verified_by_owner=excluded.verified_by_owner, "
            "updated_at=excluded.updated_at",
            (key, value, sensitivity, level, source, int(verified), now_iso()),
        )

    def create_project(self, name: str, *, default_level: Level = "CONFIDENTIAL", external_alias: str | None = None) -> str:
        pid = new_id()
        self._conn.execute("INSERT INTO project VALUES (?,?,?,?,?)", (pid, name, default_level, external_alias, now_iso()))
        return pid

    def set_project_fact(self, project_id: str, key: str, value: str | None, *, level: Level = "CONFIDENTIAL",
                         sensitivity: Literal["normal", "high"] = "normal", verified: bool = False) -> None:
        self._conn.execute(
            "INSERT INTO project_fact (id, project_id, key, value, sensitivity, access_level, verified_by_owner, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(project_id, key) DO UPDATE SET value=excluded.value, "
            "sensitivity=excluded.sensitivity, access_level=excluded.access_level, "
            "verified_by_owner=excluded.verified_by_owner, updated_at=excluded.updated_at",
            (new_id(), project_id, key, value, sensitivity, level, int(verified), now_iso()),
        )

    def verify_fact(self, table: Literal["owner_profile", "business_profile", "project_fact"], key: str, *,
                    project_id: str | None = None) -> None:
        """Owner attests a stored fact is accurate; only verified facts can ever reach a model."""
        if table == "project_fact" and project_id is None:
            raise ValueError("project_id is required to verify a project fact")
        self._require(f"VERIFY {table}.{key}")
        if table == "project_fact":
            self._conn.execute("UPDATE project_fact SET verified_by_owner = 1, updated_at = ? WHERE key = ? AND project_id = ?",
                               (now_iso(), key, project_id))
        else:
            self._conn.execute(f"UPDATE {table} SET verified_by_owner = 1, updated_at = ? WHERE key = ?", (now_iso(), key))  # noqa: S608
