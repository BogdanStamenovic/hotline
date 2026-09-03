"""Forever shim: `guard.py` (the PreToolUse denylist hook) moved to
hotline-claude. Kept here because `hotline.cli` still says `from .guard
import install_guard`.

Aliases `sys.modules["hotline.guard"]` to the real `hotline_claude.guard`
module object rather than copying its names -- see tmuxen.py in this package
for why (module-identity-sensitive monkeypatching in the test suite). The plain imports above exist only so static tools (mypy, ruff) see the real
symbols and can check `__all__`; they're immediately superseded by the
`sys.modules` swap below, which is what actually runs."""

from __future__ import annotations

import sys

from hotline_claude.guard import check, hook_path, install_guard

__all__ = ["check", "hook_path", "install_guard"]

from hotline_claude import guard as _guard

sys.modules[__name__] = _guard
