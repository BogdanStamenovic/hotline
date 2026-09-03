"""Forever shim: `tmuxen.py` (tmux session spawning) moved to hotline-claude.

This aliases `sys.modules["hotline.tmuxen"]` to the real `hotline_claude.tmuxen`
module object, rather than copying its names into a new module. That matters
here specifically: the test suite (and `pool.py`/`ccsocks.py`/`standin.py`/
`revive.py` internally, via `from . import tmuxen`) monkeypatches functions
directly on this module object (`tmuxen.spawn`, `tmuxen.exists`, ...). A
name-copying shim would patch a dead copy that nothing actually calls through;
aliasing `sys.modules` makes `hotline.tmuxen` and `hotline_claude.tmuxen`
literally the same object, so a patch made through either name is visible to
both. The plain imports above exist only so static tools (mypy, ruff) see the real
symbols and can check `__all__`; they're immediately superseded by the
`sys.modules` swap below, which is what actually runs.
"""

from __future__ import annotations

import sys

from hotline_claude.tmuxen import (
    COMMAND_SETTLE,
    PREFIX,
    capture,
    exists,
    interrupt,
    kill,
    send_command,
    sessions,
    spawn,
    tmux_name,
)

__all__ = ["COMMAND_SETTLE", "PREFIX", "capture", "exists", "interrupt", "kill", "send_command", "sessions", "spawn", "tmux_name"]

from hotline_claude import tmuxen as _tmuxen

sys.modules[__name__] = _tmuxen
