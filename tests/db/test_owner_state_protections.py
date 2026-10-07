"""Owner-state protections, demonstrated against the real database triggers.

The oracle for legal transitions is written out here by hand (from DESIGN.md section 4) and is
NOT read from the database, so a mistake in the seeded lifecycle table would be caught.
"""

from __future__ import annotations

import itertools
import random
import sqlite3

import pytest

from opportunity_operator.ids import new_id
from opportunity_operator.store.db import connect, tx
from opportunity_operator.store.repo import Repository, now_iso

STATES = ["DISCOVERED", "VERIFYING", "OWNER_REVIEW", "WATCHLIST", "DISQUALIFIED", "DECLINED", "APPROVED", "PREPARING",
          "READY_FOR_REVIEW", "SUBMITTED", "AWAITING_DECISION", "AWARDED", "NOT_AWARDED", "WITHDRAWN"]

# (from, to) -> (actors allowed, required owner decision or None)
ORACLE: dict[tuple[str, str], tuple[set[str], str | None]] = {
    ("DISCOVERED", "VERIFYING"): ({"agent", "system"}, None),
    ("VERIFYING", "OWNER_REVIEW"): ({"agent", "system"}, None),
    ("VERIFYING", "WATCHLIST"): ({"agent", "system"}, None),
    ("VERIFYING", "DISQUALIFIED"): ({"agent", "system"}, None),
    ("WATCHLIST", "VERIFYING"): ({"agent", "system"}, None),
    ("WATCHLIST", "DISQUALIFIED"): ({"agent", "system"}, None),
    ("OWNER_REVIEW", "APPROVED"): ({"owner"}, "approve"),
    ("OWNER_REVIEW", "DECLINED"): ({"owner"}, "decline"),
    ("OWNER_REVIEW", "WATCHLIST"): ({"owner"}, "watch"),
    ("OWNER_REVIEW", "DISQUALIFIED"): ({"agent", "system"}, None),
    ("APPROVED", "PREPARING"): ({"agent", "system"}, None),
    ("APPROVED", "DISQUALIFIED"): ({"agent", "system"}, None),
    ("APPROVED", "WITHDRAWN"): ({"owner"}, "withdraw"),
    ("PREPARING", "READY_FOR_REVIEW"): ({"agent", "system"}, None),
    ("PREPARING", "DISQUALIFIED"): ({"agent", "system"}, None),
    ("PREPARING", "WITHDRAWN"): ({"owner"}, "withdraw"),
    ("READY_FOR_REVIEW", "PREPARING"): ({"owner"}, "request_changes"),
    ("READY_FOR_REVIEW", "SUBMITTED"): ({"owner"}, "mark_submitted"),
    ("READY_FOR_REVIEW", "WITHDRAWN"): ({"owner"}, "withdraw"),
    ("READY_FOR_REVIEW", "DISQUALIFIED"): ({"agent", "system"}, None),
    ("SUBMITTED", "AWAITING_DECISION"): ({"agent", "system", "owner"}, None),
    ("AWAITING_DECISION", "AWARDED"): ({"owner"}, "mark_awarded"),
    ("AWAITING_DECISION", "NOT_AWARDED"): ({"owner"}, "mark_not_awarded"),
    ("AWAITING_DECISION", "WITHDRAWN"): ({"owner"}, "withdraw"),
    ("DECLINED", "VERIFYING"): ({"owner"}, "reopen"),
    ("DISQUALIFIED", "VERIFYING"): ({"owner"}, "reopen"),
}
OWNER_ONLY_TARGETS = {"APPROVED", "DECLINED", "SUBMITTED", "AWARDED", "NOT_AWARDED", "WITHDRAWN"}

PATH: dict[str, list[tuple[str, str]]] = {  # (to_state, how)
    "DISCOVERED": [],
    "VERIFYING": [("VERIFYING", "agent")],
    "OWNER_REVIEW": [("VERIFYING", "agent"), ("OWNER_REVIEW", "agent")],
    "WATCHLIST": [("VERIFYING", "agent"), ("WATCHLIST", "agent")],
    "DISQUALIFIED": [("VERIFYING", "agent"), ("DISQUALIFIED", "agent")],
}
PATH["DECLINED"] = PATH["OWNER_REVIEW"] + [("DECLINED", "decline")]
PATH["APPROVED"] = PATH["OWNER_REVIEW"] + [("APPROVED", "approve")]
PATH["PREPARING"] = PATH["APPROVED"] + [("PREPARING", "agent")]
PATH["READY_FOR_REVIEW"] = PATH["PREPARING"] + [("READY_FOR_REVIEW", "agent")]
PATH["SUBMITTED"] = PATH["READY_FOR_REVIEW"] + [("SUBMITTED", "mark_submitted")]
PATH["AWAITING_DECISION"] = PATH["SUBMITTED"] + [("AWAITING_DECISION", "agent")]
PATH["AWARDED"] = PATH["AWAITING_DECISION"] + [("AWARDED", "mark_awarded")]
PATH["NOT_AWARDED"] = PATH["AWAITING_DECISION"] + [("NOT_AWARDED", "mark_not_awarded")]
PATH["WITHDRAWN"] = PATH["READY_FOR_REVIEW"] + [("WITHDRAWN", "withdraw")]


class Env:
    def __init__(self, datadir, settings):
        self.agent = connect(datadir.db_path(), "agent")
        self.owner = connect(datadir.db_path(), "owner")
        self.arepo = Repository(self.agent, datadir, settings, "agent")
        self.orepo = Repository(self.owner, datadir, settings, "owner")

    def decision(self, oid: str, kind: str) -> str:
        did = new_id()
        self.owner.execute("INSERT INTO decision (id, opportunity_id, at, actor, decision) VALUES (?,?,?,?,?)",
                           (did, oid, now_iso(), "owner", kind))
        return did

    def opp_in(self, state: str) -> str:
        oid = self.arepo.create_opportunity(program_name=f"p-{new_id()}", sponsor="Synthetic")
        for to_state, how in PATH[state]:
            if how == "agent":
                self.arepo.transition(oid, to_state, "setup")
            else:
                self.orepo.transition(oid, to_state, "setup", decision_id=self.decision(oid, how))
        assert self.arepo.get_opportunity(oid)["state"] == state
        return oid


@pytest.fixture
def env(datadir, settings):
    return Env(datadir, settings)


def _state(env, oid):
    return env.arepo.get_opportunity(oid)["state"]


@pytest.mark.parametrize(("frm", "to"), list(itertools.product(STATES, STATES)))
def test_agent_transition_matrix(env, frm, to):
    oid = env.opp_in(frm)
    allowed = "agent" in ORACLE.get((frm, to), (set(), None))[0]
    if allowed:
        env.arepo.transition(oid, to, "test")
        assert _state(env, oid) == to
    else:
        with pytest.raises(sqlite3.DatabaseError):
            env.arepo.transition(oid, to, "test")
        assert _state(env, oid) == frm


@pytest.mark.parametrize(("frm", "to"), list(itertools.product(STATES, STATES)))
def test_owner_transition_matrix(env, frm, to):
    oid = env.opp_in(frm)
    actors, needs = ORACLE.get((frm, to), (set(), None))
    if "owner" not in actors:
        with pytest.raises(sqlite3.DatabaseError):
            env.orepo.transition(oid, to, "test", decision_id=env.decision(oid, "note"))
        assert _state(env, oid) == frm
        return
    if needs:
        with pytest.raises(sqlite3.DatabaseError, match="decision required"):  # no decision at all
            env.orepo.transition(oid, to, "test")
        with pytest.raises(sqlite3.DatabaseError, match="decision required"):  # wrong decision type
            env.orepo.transition(oid, to, "test", decision_id=env.decision(oid, "note"))
        env.orepo.transition(oid, to, "test", decision_id=env.decision(oid, needs))
    else:
        env.orepo.transition(oid, to, "test")
    assert _state(env, oid) == to


def test_agent_can_never_reach_owner_only_states_even_with_forged_actor(env):
    oid = env.opp_in("OWNER_REVIEW")
    for target in OWNER_ONLY_TARGETS:
        with pytest.raises(sqlite3.DatabaseError, match="actor does not match"):
            env.agent.execute(
                "INSERT INTO status_event (id, opportunity_id, at, from_state, to_state, actor) VALUES (?,?,?,?,?, 'owner')",
                (new_id(), oid, now_iso(), "OWNER_REVIEW", target))
    assert _state(env, oid) == "OWNER_REVIEW"


def test_agent_cannot_write_decisions(env):
    oid = env.opp_in("OWNER_REVIEW")
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        env.agent.execute("INSERT INTO decision (id, opportunity_id, at, actor, decision) VALUES (?,?,?,?,?)",
                          (new_id(), oid, now_iso(), "owner", "approve"))


def test_agent_cannot_borrow_an_owner_decision_for_a_different_opportunity_or_type(env):
    a, b = env.opp_in("OWNER_REVIEW"), env.opp_in("OWNER_REVIEW")
    did = env.decision(a, "approve")
    with pytest.raises(sqlite3.DatabaseError):  # agent actor can never target APPROVED
        env.arepo.transition(b, "APPROVED", "borrowed", decision_id=did)
    with pytest.raises(sqlite3.DatabaseError, match="decision required"):  # owner cannot reuse a decision across opportunities
        env.orepo.transition(b, "APPROVED", "borrowed", decision_id=did)
    assert _state(env, b) == "OWNER_REVIEW"


def test_decision_is_single_use(env):
    oid = env.opp_in("READY_FOR_REVIEW")
    did = env.decision(oid, "request_changes")
    env.orepo.transition(oid, "PREPARING", "changes", decision_id=did)
    env.arepo.transition(oid, "READY_FOR_REVIEW", "again")
    with pytest.raises(sqlite3.DatabaseError):  # UNIQUE(decision_id)
        env.orepo.transition(oid, "PREPARING", "replay", decision_id=did)


def test_state_cannot_be_updated_directly(env):
    oid = env.opp_in("OWNER_REVIEW")
    for conn in (env.agent, env.owner):
        with pytest.raises(sqlite3.DatabaseError, match="only change through status_event"):
            conn.execute("UPDATE opportunity SET state = 'APPROVED' WHERE id = ?", (oid,))


def test_stale_from_state_is_rejected(env):
    oid = env.opp_in("OWNER_REVIEW")
    with pytest.raises(sqlite3.DatabaseError, match="stale from_state"):
        env.agent.execute("INSERT INTO status_event (id, opportunity_id, at, from_state, to_state, actor) VALUES (?,?,?,?,?,?)",
                          (new_id(), oid, now_iso(), "VERIFYING", "DISQUALIFIED", "agent"))


def test_opportunities_cannot_be_deleted_or_born_in_other_states(env):
    oid = env.opp_in("DISCOVERED")
    with pytest.raises(sqlite3.DatabaseError, match="never deleted"):
        env.owner.execute("DELETE FROM opportunity WHERE id = ?", (oid,))
    with pytest.raises(sqlite3.DatabaseError, match="start DISCOVERED"):
        env.agent.execute("INSERT INTO opportunity (id, canonical_key, program_name, state, first_discovered) VALUES ('x','k','n','APPROVED',?)", (now_iso(),))


# Tables needing richer fixtures (evidence, assessment, snapshot, call logs) are covered in
# tests/provenance/test_evidence_model.py::test_provenance_tables_are_append_only.
APPEND_ONLY = ["status_event", "decision", "audit_incident", "discovery_hit", "state_transition"]


@pytest.mark.parametrize("table", APPEND_ONLY)
@pytest.mark.parametrize("verb", ["UPDATE", "DELETE"])
def test_append_only_even_for_owner(env, table, verb):
    stmt = {"UPDATE": f"UPDATE {table} SET rowid = rowid", "DELETE": f"DELETE FROM {table}"}[verb]
    # BEFORE triggers fire per row, so seed one row first.
    if table == "status_event":
        env.opp_in("VERIFYING")
    elif table == "decision":
        env.decision(env.opp_in("OWNER_REVIEW"), "note")
    elif table == "audit_incident":
        env.agent.execute("INSERT INTO audit_incident VALUES (?,?,?,?,?)", (new_id(), now_iso(), "c", "h", "s"))
    elif table == "discovery_hit":
        env.agent.execute("INSERT INTO discovery_hit VALUES (?,?,?,?,?,?)", (new_id(), env.opp_in("DISCOVERED"), now_iso(), "s", None, None))
    with pytest.raises(sqlite3.DatabaseError):
        env.owner.execute(stmt)
    with pytest.raises(sqlite3.DatabaseError):
        env.agent.execute(stmt)


@pytest.mark.parametrize("stmt", [
    "DROP TRIGGER status_event_guard", "DROP TABLE decision", "ALTER TABLE opportunity ADD COLUMN x TEXT",
    "CREATE TABLE evil (x)", "CREATE TRIGGER t AFTER INSERT ON opportunity BEGIN SELECT 1; END",
    "ATTACH DATABASE ':memory:' AS other", "PRAGMA writable_schema = 1", "PRAGMA foreign_keys = OFF",
    "PRAGMA trusted_schema = ON", "PRAGMA recursive_triggers = ON", "CREATE VIEW v AS SELECT 1",
    "DELETE FROM sqlite_master", "SELECT load_extension('x')", "REINDEX", "ANALYZE",
])
def test_schema_tampering_denied_for_agent_and_owner(env, stmt):
    for conn in (env.agent, env.owner):
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(stmt)


def test_raw_connection_without_actor_function_fails_closed(datadir):
    raw = sqlite3.connect(datadir.db_path())
    with pytest.raises(sqlite3.DatabaseError, match="no such function"):
        raw.execute("INSERT INTO opportunity (id, canonical_key, program_name, first_discovered) VALUES ('r','rk','n','t')")
    with pytest.raises(sqlite3.DatabaseError, match="no such function"):
        raw.execute("INSERT INTO project VALUES ('p','n','CONFIDENTIAL',NULL,'t')")
    raw.close()


def test_tampering_by_a_raw_connection_is_detected(datadir):
    """Honest limit: a process with its own sqlite handle can drop triggers. Startup verification catches it."""
    from opportunity_operator.store.migrator import verify_guards

    assert verify_guards(datadir.db_path()) == []
    raw = sqlite3.connect(datadir.db_path())
    raw.execute("DROP TRIGGER status_event_guard")
    raw.commit(); raw.close()
    assert any("missing trigger status_event_guard" in p for p in verify_guards(datadir.db_path()))


def test_tampering_with_frozen_lifecycle_table_is_detected(datadir):
    from opportunity_operator.store.migrator import verify_guards

    raw = sqlite3.connect(datadir.db_path())
    raw.execute("DROP TRIGGER state_transition_frozen_update")
    raw.execute("UPDATE state_transition SET actors = ',agent,' WHERE to_state = 'APPROVED'")
    raw.commit(); raw.close()
    problems = verify_guards(datadir.db_path())
    assert any("state_transition rows differ" in p for p in problems)


def test_app_refuses_to_start_when_protections_are_missing(datadir, settings):
    from opportunity_operator.app import init_data_dir

    raw = sqlite3.connect(datadir.db_path()); raw.execute("DROP TRIGGER opportunity_state_follows_event"); raw.commit(); raw.close()
    with pytest.raises(RuntimeError, match="protections are not intact"):
        init_data_dir(settings)


def test_randomised_agent_behaviour_never_reaches_owner_only_states(env):
    rng = random.Random(1234)
    reached: set[str] = set()
    for _ in range(60):
        oid = env.opp_in(rng.choice(["DISCOVERED", "VERIFYING", "OWNER_REVIEW", "WATCHLIST", "DISQUALIFIED"]))
        for _step in range(25):
            for conn_role in ("agent", "system"):
                conn = env.agent if conn_role == "agent" else connect(env.agent.execute("PRAGMA database_list").fetchone()["file"], "system")
                target = rng.choice(STATES)
                try:
                    conn.execute(
                        "INSERT INTO status_event (id, opportunity_id, at, from_state, to_state, actor, decision_id) VALUES (?,?,?,?,?,?,?)",
                        (new_id(), oid, now_iso(), _state(env, oid), target, conn_role, None))
                except sqlite3.DatabaseError:
                    pass
                reached.add(_state(env, oid))
                if conn_role == "system":
                    conn.close()
    assert not (reached & OWNER_ONLY_TARGETS), reached
    assert reached  # the walk did move around


def test_profile_tables_are_owner_only_and_restricted_has_no_home(env):
    for stmt in ("INSERT INTO owner_profile (key, value) VALUES ('a','b')", "INSERT INTO business_profile (key, value) VALUES ('a','b')",
                 "INSERT INTO project VALUES ('p','n','CONFIDENTIAL',NULL,'t')",
                 "INSERT INTO restricted_stub VALUES ('s','l','[\"term\"]','t')"):
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            env.agent.execute(stmt)
    for stmt in ("INSERT INTO project VALUES ('p2','n','RESTRICTED',NULL,'t')",
                 "INSERT INTO owner_profile (key, value, access_level) VALUES ('k','v','RESTRICTED')",
                 "INSERT INTO business_profile (key, value, access_level) VALUES ('k','v','RESTRICTED')"):
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            env.owner.execute(stmt)
    env.owner.execute("INSERT INTO project VALUES ('p3','n','CONFIDENTIAL',NULL,'t')")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        env.owner.execute("INSERT INTO project_fact (id, project_id, key, value, access_level) VALUES ('f','p3','k','v','RESTRICTED')")
    cols = {r["name"] for r in env.owner.execute("PRAGMA table_info(restricted_stub)")}
    assert cols == {"id", "label", "deny_terms", "created_at"}  # no place to put restricted content


def test_new_facts_default_to_confidential_and_unverified(env):
    env.owner.execute("INSERT INTO owner_profile (key, value) VALUES ('k','v')")
    row = env.owner.execute("SELECT access_level, verified_by_owner, sensitivity FROM owner_profile").fetchone()
    assert tuple(row) == ("CONFIDENTIAL", 0, "normal")


def test_agent_can_only_suggest_preferences(env):
    env.agent.execute("INSERT INTO preference (id, key, value, origin, active, created_at) VALUES ('p1','no_equity','1','suggested',0,'t')")
    with pytest.raises(sqlite3.DatabaseError, match="only the owner"):
        env.agent.execute("INSERT INTO preference (id, key, value, origin, active, created_at) VALUES ('p2','k','v','suggested',1,'t')")
    with pytest.raises(sqlite3.DatabaseError, match="only the owner"):
        env.agent.execute("INSERT INTO preference (id, key, value, origin, active, created_at) VALUES ('p3','k','v','confirmed',0,'t')")
    with pytest.raises(sqlite3.DatabaseError, match="changed by the owner only"):
        env.agent.execute("UPDATE preference SET active = 1 WHERE id = 'p1'")
    env.owner.execute("UPDATE preference SET active = 1, origin = 'confirmed' WHERE id = 'p1'")


def test_authorizer_read_isolation(env, datadir):
    env.owner.execute("INSERT INTO owner_profile (key, value) VALUES ('k','v')")
    for table in ("owner_profile", "business_profile", "project", "project_fact", "restricted_stub"):
        for role in ("agent", "system"):
            c = connect(datadir.db_path(), role)
            with pytest.raises(sqlite3.DatabaseError, match="prohibited|not authorized"):
                c.execute(f"SELECT * FROM {table}").fetchall()
            c.close()
    ctx = connect(datadir.db_path(), "context")
    assert ctx.execute("SELECT count(*) FROM owner_profile").fetchone()[0] == 1
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        ctx.execute("INSERT INTO opportunity (id, canonical_key, program_name, first_discovered) VALUES ('a','b','c','d')")
    ctx.close()


def test_transaction_rolls_back_atomically_when_event_is_refused(env):
    oid = env.opp_in("OWNER_REVIEW")
    with pytest.raises(sqlite3.DatabaseError):
        with tx(env.owner):
            did = env.decision(oid, "approve")
            env.orepo.transition(oid, "WITHDRAWN", "illegal from OWNER_REVIEW", decision_id=did)
    assert env.owner.execute("SELECT count(*) FROM decision WHERE decision = 'approve' AND opportunity_id = ?", (oid,)).fetchone()[0] == 0


def test_control_the_guard_trigger_is_what_stops_forged_owner_transitions(datadir, settings):
    """Positive control: without the trigger the same forged write succeeds, so the trigger is the protection."""
    import sqlite3 as _s

    e = Env(datadir, settings)
    oid = e.opp_in("OWNER_REVIEW")
    raw = _s.connect(datadir.db_path())
    raw.execute("DROP TRIGGER status_event_guard")
    raw.commit(); raw.close()
    e.agent.execute("INSERT INTO status_event (id, opportunity_id, at, from_state, to_state, actor) VALUES (?,?,?,?,?,?)",
                    (new_id(), oid, now_iso(), "OWNER_REVIEW", "APPROVED", "agent"))
    assert _state(e, oid) == "APPROVED"  # the unguarded database lets the agent self-approve...
    from opportunity_operator.store.migrator import verify_guards

    assert any("status_event_guard" in p for p in verify_guards(datadir.db_path()))  # ...and startup verification flags it
