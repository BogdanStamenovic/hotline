"""Forever shim: `ask.py` (the AskUserQuestion -> Discord bridge) moved to
hotline-claude. Kept here because `hotline.cli` still says
`from .ask import install_ask_hook`.

Aliases `sys.modules["hotline.ask"]` to the real `hotline_claude.ask` module
object rather than copying its names -- see tmuxen.py in this package for
why (module-identity-sensitive monkeypatching in the test suite). The plain imports above exist only so static tools (mypy, ruff) see the real
symbols and can check `__all__`; they're immediately superseded by the
`sys.modules` swap below, which is what actually runs."""

from __future__ import annotations

import sys

from hotline_claude.ask import (
    DEFAULT_WAIT,
    POLL_SECONDS,
    decide,
    format_questions,
    hook_path,
    install_ask_hook,
)

__all__ = ["DEFAULT_WAIT", "POLL_SECONDS", "decide", "format_questions", "hook_path", "install_ask_hook"]

from hotline_claude import ask as _ask

sys.modules[__name__] = _ask
