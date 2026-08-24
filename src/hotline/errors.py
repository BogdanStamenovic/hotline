"""Exception types for hotline.

Kept in their own module rather than in the core module: hotline has several
peer-level core modules (`ccsocks`, `transcript`, `fresh`, `stops`) that all need
to raise, and hanging the hierarchy off any one of them would create an import
cycle. `router` re-exports these so the public surface still reads as the
convention expects.
"""

from __future__ import annotations


class HotlineError(Exception):
    """Raised when hotline cannot complete an operation."""


class SessionNotFound(HotlineError):
    """No live Claude session matched the requested target."""


class AmbiguousSession(HotlineError):
    """More than one live session matched, and no tiebreak was possible."""


class InjectFailed(HotlineError):
    """A message could not be handed to a live session's inbox."""


class ReplyTimeout(HotlineError):
    """A session accepted the message but did not finish a turn in time."""


class ClaudeLaunchFailed(HotlineError):
    """A fresh `claude` subprocess could not be started or died early."""
