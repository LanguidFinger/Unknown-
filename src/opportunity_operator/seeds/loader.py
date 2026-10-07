"""Loads the BUNDLED placeholder-project template (a package resource, never a user-supplied path)."""

from __future__ import annotations

from importlib import resources
from typing import Any

import yaml

from ..owner import OwnerSession


def load_placeholder_project(owner: OwnerSession) -> str:
    raw = resources.files("opportunity_operator.seeds").joinpath("placeholder_project.yaml").read_text(encoding="utf-8")
    data: dict[str, Any] = yaml.safe_load(raw)
    p = data["project"]
    pid = owner.create_project(p["name"], default_level=p["default_level"], external_alias=p["external_alias"])
    for key, value in p["facts"].items():
        owner.set_project_fact(pid, key, str(value).strip(), level="APPLICATION_SAFE", verified=False)
    for key, value in data["business"].items():
        owner.set_profile_fact("business", key, str(value), level="CONFIDENTIAL", verified=False, source="seed template")
    return pid
