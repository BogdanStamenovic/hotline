"""Forever shim: `ccsocks.py` (control-socket discovery) moved to
hotline-claude. Kept here because `hotline-ios` does `from hotline.ccsocks
import discover / terminate / inject / status_of` directly, and
`hotline.cli` still says `from .ccsocks import discover`.

Aliases `sys.modules["hotline.ccsocks"]` to the real `hotline_claude.ccsocks`
module object rather than copying its names -- see tmuxen.py in this package
for why (module-identity-sensitive monkeypatching in the test suite). The plain imports above exist only so static tools (mypy, ruff) see the real
symbols and can check `__all__`; they're immediately superseded by the
`sys.modules` swap below, which is what actually runs."""

from __future__ import annotations

import sys

from hotline_claude.ccsocks import (
    PROGRAMMATIC_ENTRYPOINTS,
    LiveSession,
    discover,
    inject,
    refuse_if_self,
    status_of,
    terminate,
)

__all__ = ["PROGRAMMATIC_ENTRYPOINTS", "LiveSession", "discover", "inject", "refuse_if_self", "status_of", "terminate"]

from hotline_claude import ccsocks as _ccsocks

sys.modules[__name__] = _ccsocks
