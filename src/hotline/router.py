"""Forever shim: `router.py` is one of hotline's three frozen files (see the
root README) and this split did not edit its content -- it moved verbatim to
hotline-claude because it drives the session registry, control sockets, hooks
and transcripts directly, and `pool.py` in that package holds a `Router` and
drives its full delivery API.

This aliases `sys.modules["hotline.router"]` to the real
`hotline_claude.router` module object rather than copying its names -- see
tmuxen.py in this package for why that matters (module-identity-sensitive
monkeypatching in the test suite). `hotline.__init__` still says `from
.router import Route, Router, parse_utterance`, `hotline.cli` still says
`from .router import Route, Router, describe, mid_turn, parse_utterance`,
and `hotline-ios` does `from hotline.router import ...` directly -- all keep
working unchanged. The plain imports above exist only so static tools (mypy, ruff) see the real
symbols and can check `__all__`; they're immediately superseded by the
`sys.modules` swap below, which is what actually runs.
"""

from __future__ import annotations

import sys

from hotline_claude.router import (
    MID_TURN_WINDOW,
    AmbiguousSession,
    ClaudeLaunchFailed,
    Event,
    HotlineError,
    InjectFailed,
    Reply,
    ReplyTimeout,
    Route,
    Router,
    SessionNotFound,
    Watch,
    _why_no_reply,  # tests import this directly
    describe,
    mid_turn,
    parse_utterance,
)

__all__ = ["MID_TURN_WINDOW", "AmbiguousSession", "ClaudeLaunchFailed", "Event", "HotlineError", "InjectFailed", "Reply", "ReplyTimeout", "Route", "Router", "SessionNotFound", "Watch", "_why_no_reply", "describe", "mid_turn", "parse_utterance"]

from hotline_claude import router as _router

sys.modules[__name__] = _router
