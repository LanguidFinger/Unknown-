"""Exception types. Every policy refusal is a distinct, catchable type."""


class OperatorError(Exception):
    """Base class."""


class PathEscapeError(OperatorError):
    """A filesystem path resolved outside the data directory (or via a symlink)."""


class UnsafeDataDir(OperatorError):
    """The configured data directory is in an unsafe location."""


class DisclosureBlocked(OperatorError):
    """Outbound text matched a deny-term. `halt` says whether the run must stop."""

    def __init__(self, channel: str, term_hash: str, *, halt: bool = True) -> None:
        super().__init__(f"disclosure blocked on channel={channel!r} (term hash {term_hash[:12]})")
        self.channel = channel
        self.term_hash = term_hash
        self.halt = halt


class BudgetExceeded(OperatorError):
    """A per-run cap (tokens, dollars, fetches, searches) was reached."""


class FetchRefused(OperatorError):
    """The fetch policy refused a URL, redirect, response size or content type."""


class AccessDenied(OperatorError):
    """A caller asked for data its access level / destination does not permit."""


class NotAuthorized(OperatorError):
    """An owner-only action was attempted without owner confirmation."""


class UnsupportedContent(OperatorError):
    """Snapshot content type that Phase 0 cannot turn into verifiable text (e.g. PDF)."""


class FeatureDisabled(OperatorError):
    """A capability that is deliberately switched off (e.g. general web search)."""


class ModelPolicyViolation(OperatorError):
    """A call was routed to, or served by, a model other than the one configured for its stage."""
