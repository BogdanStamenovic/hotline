"""Forever shim: `revive.py` (bringing a finished agent back) moved to
hotline-claude. Kept here because `hotline-ios` does `from hotline.revive
import brief_for / NoSuchAgent / NothingToResumeFrom / resume` directly, and
`hotline.cli` still says `from .revive import NoSuchAgent,
NothingToResumeFrom` and `from .revive import resume as resume_agent`.

Aliases `sys.modules["hotline.revive"]` to the real `hotline_claude.revive`
module object rather than copying its names -- see tmuxen.py in this package
for why (module-identity-sensitive monkeypatching in the test suite). The plain imports above exist only so static tools (mypy, ruff) see the real
symbols and can check `__all__`; they're immediately superseded by the
`sys.modules` swap below, which is what actually runs."""

from __future__ import annotations

import sys

from hotline_claude.revive import (
    Brief,
    NoSuchAgent,
    NothingToResumeFrom,
    Resumed,
    brief_for,
    rehome,
    resumable,
    resume,
)

__all__ = ["Brief", "NoSuchAgent", "NothingToResumeFrom", "Resumed", "brief_for", "rehome", "resumable", "resume"]

from hotline_claude import revive as _revive

sys.modules[__name__] = _revive
