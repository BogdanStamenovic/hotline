"""Shim: `hotline.bot` (the Discord bot relay) did not move -- see config.py
in this package for why this aliases `sys.modules` rather than copying
names. The plain imports above exist only so static tools (mypy, ruff) see
the real symbols and can check `__all__`; they're immediately
superseded by the `sys.modules` swap below, which is what actually
runs."""

from __future__ import annotations

import sys

from hotline.bot import HotlineBot, Narration, build_bot, run_bot

__all__ = ["HotlineBot", "Narration", "build_bot", "run_bot"]

from hotline import bot as _bot

sys.modules[__name__] = _bot
