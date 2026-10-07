"""SQLite connections with role-specific protections.

Roles
-----
agent / system : pipeline code. Cannot write owner-only tables, cannot read profile tables,
                 cannot change schema.
context        : read-only; the ONLY role that can read profile tables (used by ContextBuilder).
owner          : may write owner-only tables and record decisions (still bound by append-only
                 and state-machine triggers).
migrator       : DDL only, used by the migrator.

Two layers enforce this. (1) A SQLite *authorizer* denies schema changes, ATTACH, extension
loading, unsafe PRAGMAs, role-inappropriate reads/writes. (2) Triggers (migrations/0002) use
`current_actor()`, an application-defined function each connection registers for its own role;
a raw connection that lacks it cannot write any guarded table.

Honest limit: owner and agent share an OS user, so this guards against bugs and prompt
injection, not a malicious local process that opens the file with its own code.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Literal

Role = Literal["agent", "system", "owner", "context", "migrator"]

OWNER_ONLY_TABLES = frozenset(
    {"owner_profile", "business_profile", "project", "project_fact", "restricted_stub", "decision", "state_transition",
     "budget_approval"}
)
PROFILE_TABLES = frozenset({"owner_profile", "business_profile", "project", "project_fact", "restricted_stub"})

_DDL_DENIED = {
    sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_TEMP_INDEX,
    sqlite3.SQLITE_CREATE_TEMP_TABLE, sqlite3.SQLITE_CREATE_TEMP_TRIGGER, sqlite3.SQLITE_CREATE_TEMP_VIEW,
    sqlite3.SQLITE_CREATE_TRIGGER, sqlite3.SQLITE_CREATE_VIEW, sqlite3.SQLITE_DROP_INDEX,
    sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_TEMP_INDEX, sqlite3.SQLITE_DROP_TEMP_TABLE,
    sqlite3.SQLITE_DROP_TEMP_TRIGGER, sqlite3.SQLITE_DROP_TEMP_VIEW, sqlite3.SQLITE_DROP_TRIGGER,
    sqlite3.SQLITE_DROP_VIEW, sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH,
    sqlite3.SQLITE_CREATE_VTABLE, sqlite3.SQLITE_DROP_VTABLE, sqlite3.SQLITE_REINDEX, sqlite3.SQLITE_ANALYZE,
}
# Query-style pragmas take the object name as an argument (arg2) and only read.
_QUERY_PRAGMAS = frozenset({"table_info", "table_xinfo", "index_list", "index_info", "foreign_key_list",
                            "database_list", "compile_options"})
# Settable pragmas: reading is fine, but any argument/assignment (arg2 set) is a write and is denied.
_READ_ONLY_PRAGMAS = frozenset({"foreign_keys", "user_version", "journal_mode", "busy_timeout"})


def _make_authorizer(role: Role) -> Callable[..., int]:
    def authorizer(action: int, arg1: str | None, arg2: str | None, _db: str | None, _src: str | None) -> int:
        if action in _DDL_DENIED:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_PRAGMA:
            name = (arg1 or "").lower()
            ok = name in _QUERY_PRAGMAS or (arg2 is None and name in _READ_ONLY_PRAGMAS)
            return sqlite3.SQLITE_OK if ok else sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() == "load_extension":
            return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
            table = arg1 or ""
            if table.startswith("sqlite_") or table == "schema_migration":
                return sqlite3.SQLITE_DENY
            if role == "context":
                return sqlite3.SQLITE_DENY
            if role in ("agent", "system") and table in OWNER_ONLY_TABLES:
                return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_READ and role in ("agent", "system") and (arg1 or "") in PROFILE_TABLES:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    return authorizer


def connect(db_path: str, role: Role) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    if role == "migrator":
        conn.execute("PRAGMA journal_mode=WAL")
    actor = role
    conn.create_function("current_actor", 0, lambda: actor)
    if role != "migrator":
        conn.set_authorizer(_make_authorizer(role))
    return conn


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
