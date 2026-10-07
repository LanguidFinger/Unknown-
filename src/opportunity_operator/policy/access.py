"""Access levels, destinations, purposes and the matrix that joins them.

A fact may be used only if its level is permitted for BOTH the destination it is going to and
the purpose it is used for. RESTRICTED is never permitted anywhere (and has no storage).
"""

from __future__ import annotations

from enum import StrEnum

from ..config import Settings


class AccessLevel(StrEnum):
    PUBLIC = "PUBLIC"
    APPLICATION_SAFE = "APPLICATION_SAFE"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


class Destination(StrEnum):
    CLOUD_LLM = "CLOUD_LLM"
    LOCAL_LLM = "LOCAL_LLM"
    APPLICATION_OUTPUT = "APPLICATION_OUTPUT"


class Purpose(StrEnum):
    QUERY_EXPANSION = "QUERY_EXPANSION"   # may see PUBLIC descriptors only
    EXTRACTION = "EXTRACTION"             # profile-blind: reads untrusted pages, sees NO profile data
    FIT_ASSESSMENT = "FIT_ASSESSMENT"
    SKEPTIC = "SKEPTIC"                   # profile-blind: judges evidence only
    DRAFTING = "DRAFTING"                 # output may end up in an application


_RANK = {AccessLevel.PUBLIC: 0, AccessLevel.APPLICATION_SAFE: 1, AccessLevel.CONFIDENTIAL: 2, AccessLevel.RESTRICTED: 3}

_PURPOSE_CAP: dict[Purpose, AccessLevel | None] = {
    Purpose.QUERY_EXPANSION: AccessLevel.PUBLIC,
    Purpose.EXTRACTION: None,
    Purpose.FIT_ASSESSMENT: AccessLevel.CONFIDENTIAL,
    Purpose.SKEPTIC: None,
    Purpose.DRAFTING: AccessLevel.APPLICATION_SAFE,
}


def more_restrictive(a: AccessLevel, b: AccessLevel) -> AccessLevel:
    return a if _RANK[a] >= _RANK[b] else b


def permitted_levels(destination: Destination, purpose: Purpose, settings: Settings) -> frozenset[AccessLevel]:
    """Levels whose facts may flow to `destination` for `purpose`."""
    cap = _PURPOSE_CAP[purpose]
    if cap is None:
        return frozenset()
    if destination is Destination.CLOUD_LLM:
        dest_max = AccessLevel.CONFIDENTIAL if settings.confidential_to_cloud_llm else AccessLevel.APPLICATION_SAFE
    elif destination is Destination.LOCAL_LLM:
        dest_max = AccessLevel.CONFIDENTIAL
    else:  # APPLICATION_OUTPUT
        dest_max = AccessLevel.APPLICATION_SAFE
    limit = min(_RANK[cap], _RANK[dest_max])
    return frozenset(lvl for lvl, rank in _RANK.items() if rank <= limit and lvl is not AccessLevel.RESTRICTED)
