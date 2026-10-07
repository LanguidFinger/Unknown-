"""CLI. The command set is pinned by a test: there is deliberately no import/ingest/index command."""

from __future__ import annotations

import json
import sys
from typing import Annotated

import typer

from .app import init_data_dir
from .audit import leak_sweep, verify_all_evidence
from .config import load_settings
from .owner import OwnerSession
from .prerequisites import PrerequisiteService
from .seeds.loader import load_placeholder_project
from .store.db import connect
from .store.migrator import verify_guards
from .store.repo import Repository

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Opportunity Operator (Phase 0)")
DataDirOpt = Annotated[str | None, typer.Option("--data-dir", help="Local data directory (outside any repo).")]


def _confirm(prompt: str) -> bool:
    """Interactive typed confirmation; refuses to run without a TTY."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        typer.echo("Owner actions require an interactive terminal.", err=True)
        return False
    typed = typer.prompt(f"Type the action to confirm [{prompt}]")
    return typed.strip() == prompt


@app.command()
def init(data_dir: DataDirOpt = None) -> None:
    """Create the data directory and database."""
    s = load_settings(data_dir)
    dd = init_data_dir(s)
    typer.echo(f"Initialised {dd.root}")


@app.command()
def doctor(data_dir: DataDirOpt = None) -> None:
    """Verify that the database protections are actually in place."""
    s = load_settings(data_dir)
    dd = init_data_dir(s)
    problems = verify_guards(dd.db_path())
    for p in problems:
        typer.echo(f"PROBLEM: {p}")
    typer.echo("OK: all guard triggers present" if not problems else "FAILED")
    raise typer.Exit(1 if problems else 0)


@app.command()
def explain(opportunity_id: str, data_dir: DataDirOpt = None) -> None:
    """Show sources, quotes, scores, events and decisions behind an opportunity."""
    s = load_settings(data_dir)
    dd = init_data_dir(s)
    conn = connect(dd.db_path(), "agent")
    typer.echo(json.dumps(Repository(conn, dd, s, "agent").explain(opportunity_id), indent=2, default=str))


@app.command("decide")
def decide(opportunity_id: str, decision: str, reason_code: str | None = None, notes: str | None = None, data_dir: DataDirOpt = None) -> None:
    """Record an owner decision (interactive confirmation required)."""
    s = load_settings(data_dir)
    dd = init_data_dir(s)
    owner = OwnerSession(dd, s, _confirm)
    try:
        owner.decide(opportunity_id, decision, reason_code=reason_code, notes=notes)
    finally:
        owner.close()
    typer.echo("recorded")


@app.command("prereq-list")
def prereq_list(data_dir: DataDirOpt = None) -> None:
    """Setup queue for the new entity, with what each step unlocks."""
    s = load_settings(data_dir)
    dd = init_data_dir(s)
    conn = connect(dd.db_path(), "agent")
    for it in PrerequisiteService(conn).queue():
        flag = "READY" if it.ready_to_start else "waiting on earlier step"
        typer.echo(f"[{it.status}] {it.key}: {it.label} ({flag}; blocks {it.blocks}; unlocks alone {len(it.unlocks_alone)})")


@app.command("prereq-set")
def prereq_set(key: str, status: str, notes: str | None = None, data_dir: DataDirOpt = None) -> None:
    """Owner: set a prerequisite status ('done' requires confirmation)."""
    s = load_settings(data_dir)
    dd = init_data_dir(s)
    owner = OwnerSession(dd, s, _confirm)
    try:
        owner.set_prerequisite(key, status, notes)
    finally:
        owner.close()


@app.command("restricted-add")
def restricted_add(label: str, term: Annotated[list[str], typer.Option("--term")], data_dir: DataDirOpt = None) -> None:
    """Owner: register deny-terms for a restricted area. Stores NO content, only the terms."""
    s = load_settings(data_dir)
    dd = init_data_dir(s)
    owner = OwnerSession(dd, s, _confirm)
    try:
        owner.add_restricted_stub(label, term)
    finally:
        owner.close()
    typer.echo(f"registered {len(term)} deny-term(s) under label {label!r}")


@app.command("seed-placeholder-project")
def seed_placeholder_project(data_dir: DataDirOpt = None) -> None:
    """Owner: load the bundled 'Opportunity Operator Platform' placeholder (unverified)."""
    s = load_settings(data_dir)
    dd = init_data_dir(s)
    owner = OwnerSession(dd, s, _confirm)
    try:
        load_placeholder_project(owner)
    finally:
        owner.close()
    typer.echo("loaded (all facts unverified; nothing reaches a model until you verify them)")


@app.command("profile-set")
def profile_set(
    scope: str, key: str, value: str, level: str = "CONFIDENTIAL", sensitivity: str = "normal", data_dir: DataDirOpt = None,
) -> None:
    """Owner: set an owner/business profile fact (stored unverified; defaults to CONFIDENTIAL)."""
    if scope not in {"owner", "business"}:
        raise typer.BadParameter("scope must be 'owner' or 'business'")
    s = load_settings(data_dir)
    dd = init_data_dir(s)
    owner = OwnerSession(dd, s, _confirm)
    try:
        owner.set_profile_fact(scope, key, value, level=level, sensitivity=sensitivity)  # type: ignore[arg-type]
    finally:
        owner.close()


@app.command("verify-fact")
def verify_fact(table: str, key: str, project_id: str | None = None, data_dir: DataDirOpt = None) -> None:
    """Owner: attest that a stored fact is accurate (interactive confirmation required)."""
    if table not in {"owner_profile", "business_profile", "project_fact"}:
        raise typer.BadParameter("table must be owner_profile, business_profile or project_fact")
    s = load_settings(data_dir)
    dd = init_data_dir(s)
    owner = OwnerSession(dd, s, _confirm)
    try:
        owner.verify_fact(table, key, project_id=project_id)  # type: ignore[arg-type]
    finally:
        owner.close()


@app.command("audit-evidence")
def audit_evidence(data_dir: DataDirOpt = None) -> None:
    """Re-verify every stored quote against its snapshot and the snapshot hashes."""
    s = load_settings(data_dir)
    dd = init_data_dir(s)
    rep = verify_all_evidence(connect(dd.db_path(), "agent"), dd)
    typer.echo(f"snapshots={rep.snapshots_checked} evidence={rep.evidence_checked} unverified_rows={rep.unverified_rows}")
    for f in rep.failures:
        typer.echo(f"FAIL: {f}")
    raise typer.Exit(0 if rep.ok else 1)


@app.command("audit-sweep")
def audit_sweep(needle: Annotated[list[str], typer.Option("--needle")], data_dir: DataDirOpt = None) -> None:
    """Search the data directory for strings that must never appear outside profile tables."""
    s = load_settings(data_dir)
    dd = init_data_dir(s)
    found = leak_sweep(dd, outside_profiles=needle)
    for f in found:
        typer.echo(f"FOUND needle #{f.needle_index} in {f.location}")
    typer.echo("clean" if not found else "LEAK FOUND")
    raise typer.Exit(1 if found else 0)


ALLOWED_COMMANDS = frozenset({
    "init", "doctor", "explain", "decide", "prereq-list", "prereq-set", "restricted-add",
    "seed-placeholder-project", "profile-set", "verify-fact", "audit-evidence", "audit-sweep",
})
