"""Composition root: the only place raw adapters meet guards.

The orchestrator receives an `App` whose llm/search/fetcher are Guarded* wrappers and whose
repository uses the 'agent' role. It never sees a raw adapter, an owner connection, or a
profile-reading connection.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .config import Settings
from .policy.budget_guard import BudgetGuard
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
    _conns: list[sqlite3.Connection]

    def close(self) -> None:
        for c in self._conns:
            c.close()


def build_app(
    settings: Settings, *, llm: LLMClient, search: SearchProvider, fetcher: Fetcher,
    price_in_per_mtok: float = 0.0, price_out_per_mtok: float = 0.0,
) -> App:
    dd = init_data_dir(settings)
    agent = connect(dd.db_path(), "agent")
    audit = connect(dd.db_path(), "system")        # separate autocommit connection: incidents survive rollbacks
    context = connect(dd.db_path(), "context")
    flt = DisclosureFilter.from_connection(context)
    budget = BudgetGuard.from_settings(settings, price_in=price_in_per_mtok, price_out=price_out_per_mtok)
    rec = DbRecorder(audit)
    return App(
        settings=settings, datadir=dd, repo=Repository(agent, dd, settings, "agent"),
        llm=GuardedLLM(llm, flt, budget, rec), search=GuardedSearch(search, flt, budget, rec),
        fetcher=GuardedFetcher(fetcher, flt, budget, rec), builder=ContextBuilder(context, settings, flt),
        budget=budget, disclosure_filter=flt, _conns=[agent, audit, context],
    )
