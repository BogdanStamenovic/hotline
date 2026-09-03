"""Forever shim: `agents.py` (the session registry) moved to hotline-claude.
Kept here because `hotline-ios` does `from hotline.agents import Registry`
directly and `hotline.cli` still says `from .agents import ...`.

Aliases `sys.modules["hotline.agents"]` to the real `hotline_claude.agents`
module object rather than copying its names -- see tmuxen.py in this package
for why (module-identity-sensitive monkeypatching in the test suite). The plain imports above exist only so static tools (mypy, ruff) see the real
symbols and can check `__all__`; they're immediately superseded by the
`sys.modules` swap below, which is what actually runs."""

from __future__ import annotations

import sys

from hotline_claude.agents import DEFAULT_KEEP_DAYS, Agent, Registry

__all__ = ["DEFAULT_KEEP_DAYS", "Agent", "Registry"]

from hotline_claude import agents as _agents

sys.modules[__name__] = _agents
