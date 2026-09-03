"""Forever shim: `stops.py` (the Stop-hook reply spool) moved to
hotline-claude. Kept here because `hotline.cli` still says `from .stops
import install_hook, stops_dir`.

Aliases `sys.modules["hotline.stops"]` to the real `hotline_claude.stops`
module object rather than copying its names -- see tmuxen.py in this package
for why (module-identity-sensitive monkeypatching in the test suite). The plain imports above exist only so static tools (mypy, ruff) see the real
symbols and can check `__all__`; they're immediately superseded by the
`sys.modules` swap below, which is what actually runs."""

from __future__ import annotations

import sys

from hotline_claude.stops import (
    hook_path,
    install_hook,
    record_stop,
    stop_stamp,
    stops_dir,
    wait_for_stop,
)

__all__ = ["hook_path", "install_hook", "record_stop", "stop_stamp", "stops_dir", "wait_for_stop"]

from hotline_claude import stops as _stops

sys.modules[__name__] = _stops
