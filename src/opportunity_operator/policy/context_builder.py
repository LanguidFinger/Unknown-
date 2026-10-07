"""The single choke point between stored profile data and any model.

Reads profile tables through the read-only `context` role (the only role the authorizer lets
read them) and releases a fact only if ALL hold:
  * owner has verified it,
  * it is not tagged sensitivity='high' (those never enter any prompt, local or cloud),
  * its effective level (the more restrictive of fact level and project ceiling) is permitted
    for the destination AND the purpose (see access.py),
  * RESTRICTED never qualifies (and cannot be stored).
Profile-blind purposes (EXTRACTION, SKEPTIC) receive no profile facts at all, so a hostile web
page cannot trick an extraction call into echoing owner data.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from ..config import Settings
from .access import AccessLevel, Destination, Purpose, more_restrictive, permitted_levels
from .disclosure_filter import DisclosureFilter
from .prompt import ContextFact, Prompt, _mint_prompt


class LocalValues:
    """Profile values for deterministic, local code only (e.g. eligibility gates).

    Cannot be placed in a Prompt (type-checked) and redacts itself when printed or logged.
    """

    def __init__(self, values: dict[str, str]) -> None:
        self._values = dict(values)

    def get(self, key: str) -> str | None:
        return self._values.get(key)

    def keys(self) -> list[str]:
        return sorted(self._values)

    def __repr__(self) -> str:
        return f"LocalValues(<{len(self._values)} values redacted>)"

    __str__ = __repr__

    def __reduce__(self) -> tuple[object, ...]:
        raise TypeError("LocalValues cannot be serialised")


class ContextBuilder:
    def __init__(self, context_conn: sqlite3.Connection, settings: Settings, disclosure_filter: DisclosureFilter) -> None:
        self._conn = context_conn
        self._settings = settings
        self._filter = disclosure_filter

    def facts(self, purpose: Purpose, destination: Destination) -> list[ContextFact]:
        allowed = permitted_levels(destination, purpose, self._settings)
        if not allowed:
            return []
        out: list[ContextFact] = []
        for source, table in (("owner", "owner_profile"), ("business", "business_profile")):
            for r in self._conn.execute(
                f"SELECT key, value, access_level FROM {table} "  # noqa: S608 - fixed table names
                "WHERE verified_by_owner = 1 AND sensitivity = 'normal' AND value IS NOT NULL ORDER BY key"
            ):
                if AccessLevel(r["access_level"]) in allowed:
                    out.append(ContextFact(f"{source}:{r['key']}", source, r["key"], r["value"], r["access_level"]))
        for r in self._conn.execute(
            "SELECT f.key, f.value, f.access_level, p.id AS pid, p.default_access_level AS ceil, p.external_alias AS alias "
            "FROM project_fact f JOIN project p ON p.id = f.project_id "
            "WHERE f.verified_by_owner = 1 AND f.sensitivity = 'normal' AND f.value IS NOT NULL ORDER BY p.id, f.key"
        ):
            effective = more_restrictive(AccessLevel(r["access_level"]), AccessLevel(r["ceil"]))
            if effective in allowed:
                label = r["alias"] or f"project-{r['pid'][-6:]}"
                out.append(ContextFact(f"project:{r['pid']}:{r['key']}", label, r["key"], r["value"], effective.value))
        return out

    def local_values(self) -> LocalValues:
        """For deterministic local code only. Includes owner-verified sensitive values."""
        vals: dict[str, str] = {}
        for prefix, table in (("owner", "owner_profile"), ("business", "business_profile")):
            for r in self._conn.execute(f"SELECT key, value FROM {table} WHERE verified_by_owner = 1 AND value IS NOT NULL"):  # noqa: S608
                vals[f"{prefix}.{r['key']}"] = r["value"]
        return LocalValues(vals)

    def make_prompt(
        self,
        purpose: Purpose,
        destination: Destination,
        task: str,
        *,
        untrusted: Sequence[str] = (),
        schema_name: str,
        prompt_version: str = "v0",
        stage: str | None = None,
    ) -> Prompt:
        prompt = _mint_prompt(
            purpose=purpose,
            destination=destination,
            task=task,
            facts=self.facts(purpose, destination),
            untrusted=list(untrusted),
            schema_name=schema_name,
            prompt_version=prompt_version,
            stage=stage or purpose.value.lower(),
        )
        # Trusted portion only: web-derived text containing a deny-term is not *our* disclosure.
        self._filter.check(prompt.trusted_text(), channel="prompt_mint")
        return prompt
