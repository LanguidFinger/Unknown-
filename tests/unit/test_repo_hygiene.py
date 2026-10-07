"""The repository must be safe to share: no secrets, identifiers, databases or real data."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SECRET_PATTERNS = {
    "anthropic key": r"sk-ant-[A-Za-z0-9_\-]{10,}", "openai-style key": r"sk-[A-Za-z0-9]{32,}", "aws access key": r"AKIA[0-9A-Z]{16}",
    "github token": r"gh[pousr]_[A-Za-z0-9]{30,}", "slack token": r"xox[abprs]-[A-Za-z0-9-]{10,}", "private key block": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "google api key": r"AIza[0-9A-Za-z_\-]{35}",
}
FORBIDDEN_SUFFIXES = (".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".pem", ".key", ".p12", ".pfx", ".env")


def tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=REPO, capture_output=True, text=True, check=True).stdout
    return [REPO / line for line in out.splitlines() if line and (REPO / line).is_file()]


def test_no_secrets_in_any_repository_file():
    hits = []
    for f in tracked_files():
        text = f.read_text(errors="ignore")
        for name, pat in SECRET_PATTERNS.items():
            if re.search(pat, text):
                hits.append((str(f.relative_to(REPO)), name))
    assert hits == []


def test_no_database_key_or_env_files_would_be_committed():
    assert [str(f.relative_to(REPO)) for f in tracked_files() if f.name.endswith(FORBIDDEN_SUFFIXES) or f.name.startswith(".env")] == []


def test_no_ein_like_identifiers_outside_obviously_synthetic_context():
    ein = re.compile(r"\b\d{2}-\d{7}\b")
    uei = re.compile(r"\b[A-Z0-9]{12}\b(?=.*UEI)|UEI[:=\s]+[A-Z0-9]{12}\b")
    offenders = []
    for f in tracked_files():
        if f.suffix in {".md", ".py", ".yaml", ".yml", ".sql", ".toml", ".json"}:
            t = f.read_text(errors="ignore")
            if (ein.search(t) or uei.search(t)) and "SYNTHETIC" not in t.upper():
                offenders.append(str(f.relative_to(REPO)))
    assert offenders == []


def test_gitignore_protects_data_and_secrets_by_default():
    text = (REPO / ".gitignore").read_text()
    for pattern in ("*.db", "*.db-wal", "snapshots/", "dossiers/", "profiles/", ".env", "*.key", "*.pem", ".opportunity-operator/", "data/"):
        assert pattern in text, pattern


def test_default_data_dir_is_outside_the_repo():
    from opportunity_operator.config import DEFAULT_DATA_DIR

    assert DEFAULT_DATA_DIR.startswith("~") and not (REPO / DEFAULT_DATA_DIR.lstrip("~/")).exists()


def test_bundled_seed_contains_only_unknown_or_not_yet_values():
    import yaml

    data = yaml.safe_load((REPO / "src/opportunity_operator/seeds/placeholder_project.yaml").read_text())
    facts = data["project"]["facts"]
    unknown_keys = {"development_stage", "technical_novelty", "research_claims", "commercialization_plan", "revenue", "customers",
                    "established_capabilities", "existing_ip", "patent_status", "estimated_budget", "funding_needs"}
    assert all(facts[k] == "UNKNOWN" for k in unknown_keys)
    assert all(v.startswith("not yet") for v in data["business"].values())
