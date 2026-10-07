"""Composition root: the only place raw adapters meet guards.

The orchestrator receives an `App` whose llm/search/fetcher are Guarded* wrappers and whose
repository uses the 'agent' role. It never sees a raw adapter, an owner connection, or a
profile-reading connection.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .config import Settings
from .ids import new_id
from .policy.budget_guard import BudgetGuard, SpendLedger
from .policy.context_builder import ContextBuilder
from .policy.disclosure_filter import DisclosureFilter
from .policy.guarded import DbRecorder, GuardedFetcher, GuardedLLM, GuardedSearch
from .ports.fetcher import Fetcher
from .ports.llm import LLMClient
from .ports.search import SearchProvider
from .safe_fs import DataDir
from .store.db import connect
from .store.migrator import migrate, verify_guards
from .store.repo import Repository


def init_data_dir(settings: Settings) -> DataDir:
    dd = DataDir(settings.data_dir)
    dd.ensure_dir("snapshots")
    dd.ensure_dir("dossiers")
    migrate(dd.db_path())
    problems = verify_guards(dd.db_path())
    if problems:
        raise RuntimeError("database protections are not intact: " + "; ".join(problems))
    return dd


@dataclass
class App:
    settings: Settings
    datadir: DataDir
    repo: Repository
    llm: GuardedLLM
    search: GuardedSearch
    fetcher: GuardedFetcher
    builder: ContextBuilder
    budget: BudgetGuard
    disclosure_filter: DisclosureFilter
    run_id: str
    _conns: list[sqlite3.Connection]
    _finished: bool = field(default=False, init=False)

    def finish(self, status: str = "finished") -> None:
        if self._finished:
            return
        self._finished = True
        b = self.budget
        self.repo.conn.execute(
            "UPDATE run_log SET finished_at = ?, tokens_in = ?, tokens_out = ?, cost_usd = ?, fetches = ?, status = ? WHERE id = ?",
            (datetime.now(UTC).isoformat(), b.tokens_in, b.tokens_out, b.cost_usd, b.fetches, status, self.run_id),
        )

    def close(self) -> None:
        self.finish()
        for c in self._conns:
            c.close()


def build_app(
    settings: Settings, *, llms: Mapping[str, LLMClient], fetcher: Fetcher, search: SearchProvider | None = None,
) -> App:
    """`llms` maps pipeline stage -> client. There is no default and no fallback model."""
    dd = init_data_dir(settings)
    agent = connect(dd.db_path(), "agent")
    audit = connect(dd.db_path(), "system")        # separate autocommit connection: incidents/cost rows survive rollbacks
    context = connect(dd.db_path(), "context")
    run_id = new_id()
    agent.execute("INSERT INTO run_log (id, started_at, status) VALUES (?,?,'running')", (run_id, datetime.now(UTC).isoformat()))
    flt = DisclosureFilter.from_connection(context)
    budget = BudgetGuard.from_settings(settings, SpendLedger(audit, run_id))
    rec = DbRecorder(audit, run_id)
    return App(
        settings=settings, datadir=dd, repo=Repository(agent, dd, settings, "agent"),
        llm=GuardedLLM(llms, settings, flt, budget, rec), search=GuardedSearch(search, settings, flt, budget, rec),
        fetcher=GuardedFetcher(fetcher, flt, budget, rec), builder=ContextBuilder(context, settings, flt),
        budget=budget, disclosure_filter=flt, run_id=run_id, _conns=[agent, audit, context],
    )
