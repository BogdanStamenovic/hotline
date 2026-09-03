"""Forever shim: `standin.py` (a stand-in reply while a session is busy)
moved to hotline-claude.

Aliases `sys.modules["hotline.standin"]` to the real `hotline_claude.standin`
module object rather than copying its names -- see tmuxen.py in this package
for why (module-identity-sensitive monkeypatching in the test suite). The plain imports above exist only so static tools (mypy, ruff) see the real
symbols and can check `__all__`; they're immediately superseded by the
`sys.modules` swap below, which is what actually runs."""

from __future__ import annotations

import sys

from hotline_claude.standin import (
    PANE_LINES,
    STANDIN_MODEL,
    STANDIN_TIMEOUT,
    TRANSCRIPT_BYTES,
    Standing,
    report,
)

__all__ = ["PANE_LINES", "STANDIN_MODEL", "STANDIN_TIMEOUT", "TRANSCRIPT_BYTES", "Standing", "report"]

from hotline_claude import standin as _standin

sys.modules[__name__] = _standin
