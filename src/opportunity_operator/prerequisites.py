"""Setup/prerequisite queue for the new (not yet formed) entity.

Discovery never waits on prerequisites. Opportunities record which prerequisites they need;
this service reports what blocks each one and which opportunities become actionable as each
prerequisite (plus its own unmet dependencies) is completed.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class QueueItem:
    key: str
    label: str
    status: str
    ready_to_start: bool
    blocks: int           # opportunities that need this (transitively) and are not yet actionable
    unlocks_alone: tuple[str, ...]  # opportunities that become actionable once this chain is done


class PrerequisiteService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def _graph(self) -> dict[str, tuple[str, str, list[str]]]:
        return {r["key"]: (r["label"], r["status"], json.loads(r["depends_on"]))
                for r in self._c.execute("SELECT key, label, status, depends_on FROM prerequisite")}

    def assert_acyclic(self) -> None:
        g = self._graph()
        state: dict[str, int] = {}

        def visit(k: str) -> None:
            if state.get(k) == 1:
                raise ValueError(f"prerequisite cycle at {k}")
            if state.get(k) == 2:
                return
            state[k] = 1
            for d in g[k][2]:
                visit(d)
            state[k] = 2

        for k in g:
            visit(k)

    def _unmet_closure(self, keys: set[str], g: dict[str, tuple[str, str, list[str]]]) -> set[str]:
        out: set[str] = set()
        stack = list(keys)
        while stack:
            k = stack.pop()
            if k in out or g[k][1] == "done":
                continue
            out.add(k)
            stack.extend(g[k][2])
        return out

    def _needs(self) -> dict[str, set[str]]:
        needs: dict[str, set[str]] = {}
        for r in self._c.execute("SELECT opportunity_id, prerequisite_key FROM opportunity_prerequisite"):
            needs.setdefault(r["opportunity_id"], set()).add(r["prerequisite_key"])
        return needs

    def blocked_by(self, opportunity_id: str) -> list[str]:
        g = self._graph()
        return sorted(self._unmet_closure(self._needs().get(opportunity_id, set()), g))

    def actionable(self, opportunity_id: str) -> bool:
        return not self.blocked_by(opportunity_id)

    def queue(self) -> list[QueueItem]:
        g = self._graph()
        needs = self._needs()
        unmet_by_opp = {o: self._unmet_closure(k, g) for o, k in needs.items()}
        items: list[QueueItem] = []
        for key, (label, status, deps) in g.items():
            if status == "done":
                continue
            chain = self._unmet_closure({key}, g)
            unlocks = tuple(sorted(o for o, u in unmet_by_opp.items() if u and u <= chain))
            blocks = sum(1 for u in unmet_by_opp.values() if key in u)
            ready = all(g[d][1] == "done" for d in deps)
            items.append(QueueItem(key, label, status, ready, blocks, unlocks))
        # Ready-to-start items first, then by how many opportunities they hold up.
        return sorted(items, key=lambda i: (not i.ready_to_start, -i.blocks, i.key))
