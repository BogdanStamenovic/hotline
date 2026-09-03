"""Forever shim: `fresh.py` (spawning a fresh Claude session, and the
Event/Reply event types) moved to hotline-claude. Kept here because
`hotline.cli` still says `from .fresh import Event`.

Aliases `sys.modules["hotline.fresh"]` to the real `hotline_claude.fresh`
module object rather than copying its names -- see tmuxen.py in this package
for why (module-identity-sensitive monkeypatching in the test suite). The plain imports above exist only so static tools (mypy, ruff) see the real
symbols and can check `__all__`; they're immediately superseded by the
`sys.modules` swap below, which is what actually runs."""

from __future__ import annotations

import sys

from hotline_claude.fresh import Event, FreshSession, Narrator, Reply

__all__ = ["Event", "FreshSession", "Narrator", "Reply"]

from hotline_claude import fresh as _fresh

sys.modules[__name__] = _fresh
