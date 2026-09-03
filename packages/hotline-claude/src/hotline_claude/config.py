"""Shim: `hotline.config` did not move -- see this package's README for why.

Aliases `sys.modules["hotline_claude.config"]` to the real `hotline.config`
module object rather than copying its names. Several modules in this package
(`ask.py`, `guard.py`, `stops.py`) do `from .config import X` *inside a
function*, re-resolved on every call -- if this were a name-copying shim,
those calls would always see a stale snapshot from whenever this shim first
loaded, immune to any test monkeypatching `hotline.config` directly (as the
test suite does). Aliasing makes the two modules the same object, so a patch
made through either name is visible to both. The plain imports above exist only so static tools (mypy, ruff) see the real
symbols and can check `__all__`; they're immediately superseded by the
`sys.modules` swap below, which is what actually runs."""

from __future__ import annotations

import sys

from hotline.config import (
    CLAUDE_BIN,
    DEFAULT_REPLY_TIMEOUT,
    POLL_INTERVAL,
    QUIET_SECONDS,
    agents_file,
    bindings_file,
    claude_home,
    load_env,
    page_claim,
    projects_dir,
    runtime_dir,
    sessions_dir,
    settings_path,
    state_dir,
    stops_dir,
    stops_log,
)

__all__ = ["CLAUDE_BIN", "DEFAULT_REPLY_TIMEOUT", "POLL_INTERVAL", "QUIET_SECONDS", "agents_file", "bindings_file", "claude_home", "load_env", "page_claim", "projects_dir", "runtime_dir", "sessions_dir", "settings_path", "state_dir", "stops_dir", "stops_log"]

from hotline import config as _config

sys.modules[__name__] = _config
