"""Shim: `hotline.errors` did not move -- see config.py in this package for
why this aliases `sys.modules` rather than copying names. The plain imports above exist only so static tools (mypy, ruff) see the real
symbols and can check `__all__`; they're immediately superseded by the
`sys.modules` swap below, which is what actually runs."""

from __future__ import annotations

import sys

from hotline.errors import (
    AmbiguousSession,
    ClaudeLaunchFailed,
    HotlineError,
    InjectFailed,
    ReplyTimeout,
    SessionNotFound,
)

__all__ = ["AmbiguousSession", "ClaudeLaunchFailed", "HotlineError", "InjectFailed", "ReplyTimeout", "SessionNotFound"]

from hotline import errors as _errors

sys.modules[__name__] = _errors
