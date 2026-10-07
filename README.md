# Opportunity Operator

A bounded agent that finds, verifies, ranks, prepares and tracks legitimate funding, credit and resource opportunities, with evidence provenance, human approval gates, and IP/privacy safeguards.

**Status: Phase 0 (foundations and controls). No live discovery has been run and no provider is enabled.**

- Design: `docs/opportunity-operator/DESIGN.md` (section 19 lists deviations from the original design)
- What Phase 0 built and demonstrated: `docs/opportunity-operator/PHASE0_REPORT.md`
- Source/API feasibility: `docs/opportunity-operator/SOURCE_FEASIBILITY.md`
- Cost model and provider recommendation: `docs/opportunity-operator/COST_MODEL.md`

## Safe to share

This repository holds code, schemas, documentation and synthetic fixtures only. Real profiles, snapshots, dossiers, the database and credentials live in a local data directory outside any git working tree (default `~/.opportunity-operator`, override with `--data-dir` or `OPOP_DATA_DIR`) which the code refuses to place inside a repository. API keys are read from the environment at runtime and are never stored.

## Develop

```bash
uv venv .venv && uv pip install -e ".[dev]"
.venv/bin/python -m pytest        # full suite
.venv/bin/ruff check . && .venv/bin/mypy
.venv/bin/opop init --data-dir /path/outside/any/repo/data
.venv/bin/opop doctor --data-dir /path/outside/any/repo/data
```

`opop` has no command that imports, ingests, indexes or scans a path, by design.
