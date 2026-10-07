"""Settings. Deliberately has NO field that can point at project sources, repos or documents.

Only the data directory (where real profiles/dossiers/snapshots live, outside the repo) and
numeric/host policy knobs exist. `extra="forbid"` plus a test that pins the exact field set
keeps it that way. Secrets (API keys) are never settings: adapters read them from the
environment at construction time and they are never logged or stored.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .safe_fs import DataDir

DEFAULT_DATA_DIR = "~/.opportunity-operator"

# USD per million tokens (input, output). Anthropic published prices, retrieved 2026-10-07. Prices change:
# override in the data directory's settings.yaml rather than editing code. Used only for budget enforcement.
DEFAULT_MODEL_PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-fable-5-1": (10.0, 50.0),
}
# Narrow model split approved by the owner. Each stage has exactly one model; there is NO fallback chain.
DEFAULT_STAGE_MODELS: dict[str, str] = {
    "query_expansion": "claude-haiku-4-5",
    "triage": "claude-haiku-4-5",
    "extract": "claude-haiku-4-5",
    "terms": "claude-sonnet-5-5",
    "judge": "claude-opus-5-5",
    "dossier_draft": "claude-sonnet-5-5",
    "dossier_summary": "claude-opus-5-5",
}


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data_dir: Path = Path(DEFAULT_DATA_DIR)
    # 'direct': we resolve DNS and refuse non-public addresses. 'proxy': the environment's
    # egress proxy is trusted for address filtering (e.g. managed sandboxes).
    egress_mode: Literal["direct", "proxy"] = "direct"
    api_allow_hosts: tuple[str, ...] = ("api.grants.gov", "api.www.sbir.gov")
    extra_allow_hosts: tuple[str, ...] = ()
    max_fetch_bytes: int = Field(5_000_000, gt=0)
    min_host_interval_s: float = Field(1.0, ge=0)
    min_quote_chars: int = Field(15, ge=8)
    # Per-run budget caps (hard stops).
    max_llm_calls: int = Field(200, ge=0)
    max_tokens_in: int = Field(2_000_000, ge=0)
    max_tokens_out: int = Field(300_000, ge=0)
    max_cost_usd: float = Field(5.0, ge=0)          # hard cap per run
    monthly_cap_usd: float = Field(50.0, ge=0)      # hard cap per UTC month, across all runs
    max_fetches: int = Field(300, ge=0)
    max_searches: int = Field(60, ge=0)
    stage_models: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_STAGE_MODELS))
    model_prices: dict[str, tuple[float, float]] = Field(default_factory=lambda: dict(DEFAULT_MODEL_PRICES))
    # General web search is OFF. No vendor adapter exists; the port is kept so one can be added later.
    search_enabled: bool = False
    # Sending CONFIDENTIAL facts to a cloud LLM is off unless explicitly opted in.
    confidential_to_cloud_llm: bool = False


def load_settings(data_dir: str | os.PathLike[str] | None = None, **overrides: object) -> Settings:
    chosen = Path(data_dir or os.environ.get("OPOP_DATA_DIR") or DEFAULT_DATA_DIR)
    values: dict[str, object] = {"data_dir": chosen}
    dd = DataDir(chosen)
    if dd.exists("settings.yaml"):
        loaded = yaml.safe_load(dd.get_text("settings.yaml")) or {}
        if not isinstance(loaded, dict):
            raise ValueError("settings.yaml must be a mapping")
        values.update(loaded)
    values.update(overrides)
    values["data_dir"] = chosen
    return Settings(**values)  # type: ignore[arg-type]
