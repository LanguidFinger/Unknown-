import pytest

from opportunity_operator.errors import NotAuthorized
from opportunity_operator.prerequisites import PrerequisiteService
from opportunity_operator.store.db import connect


def _opp(app_repo, name):
    return app_repo.create_opportunity(program_name=name, sponsor="Synthetic Agency")


@pytest.fixture
def svc(agent_conn):
    return PrerequisiteService(agent_conn)


def test_seed_graph_is_acyclic_and_everything_starts_not_started(svc, agent_conn):
    svc.assert_acyclic()
    statuses = {r["status"] for r in agent_conn.execute("SELECT status FROM prerequisite")}
    assert statuses == {"not_started"}  # new entity is treated as not yet established
    assert agent_conn.execute("SELECT count(*) FROM prerequisite WHERE details_verified = 1").fetchone()[0] == 0


def test_discovery_proceeds_and_blockers_are_transitive(svc, agent_conn, datadir, settings):
    from opportunity_operator.store.repo import Repository

    repo = Repository(agent_conn, datadir, settings, "agent")
    sam_opp, plain_opp = _opp(repo, "SAM-required program"), _opp(repo, "No-prereq program")
    agent_conn.execute("INSERT INTO opportunity_prerequisite VALUES (?,?,NULL)", (sam_opp, "sam_registration_active"))
    assert svc.blocked_by(sam_opp) == ["ein_obtained", "entity_formed", "sam_registration_active"]
    assert svc.actionable(plain_opp)  # opportunities without prerequisites are not held up
    assert not svc.actionable(sam_opp)


def test_queue_orders_ready_first_and_reports_unlocks(svc, agent_conn, datadir, settings, owner):
    from opportunity_operator.store.repo import Repository

    repo = Repository(agent_conn, datadir, settings, "agent")
    a, b = _opp(repo, "A"), _opp(repo, "B")
    agent_conn.execute("INSERT INTO opportunity_prerequisite VALUES (?,?,NULL)", (a, "sam_registration_active"))
    agent_conn.execute("INSERT INTO opportunity_prerequisite VALUES (?,?,NULL)", (b, "entity_formed"))
    q = svc.queue()
    assert q[0].key == "entity_formed" and q[0].ready_to_start
    by_key = {i.key: i for i in q}
    assert by_key["entity_formed"].unlocks_alone == (b,)            # completing entity formation alone unlocks B
    assert a not in by_key["entity_formed"].unlocks_alone           # A still needs EIN + SAM
    assert set(by_key["sam_registration_active"].unlocks_alone) == {a, b}  # whole SAM chain unlocks both
    owner.set_prerequisite("entity_formed", "done")
    assert svc.actionable(b) and not svc.actionable(a)


def test_only_owner_can_mark_done(agent_conn, datadir, settings):
    with pytest.raises(Exception, match="only the owner"):
        agent_conn.execute("UPDATE prerequisite SET status = 'done' WHERE key = 'entity_formed'")
    agent_conn.execute("UPDATE prerequisite SET status = 'in_progress' WHERE key = 'entity_formed'")  # allowed


def test_owner_confirmation_is_required_for_done(settings, datadir):
    from opportunity_operator.owner import OwnerSession

    o = OwnerSession(datadir, settings, confirm=lambda _p: False)
    try:
        with pytest.raises(NotAuthorized):
            o.set_prerequisite("entity_formed", "done")
    finally:
        o.close()
    c = connect(datadir.db_path(), "agent")
    assert c.execute("SELECT status FROM prerequisite WHERE key='entity_formed'").fetchone()[0] == "not_started"
