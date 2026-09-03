"""Shim: `hotline.pager` did not move -- see config.py in this package for
why this aliases `sys.modules` rather than copying names. The plain imports above exist only so static tools (mypy, ruff) see the real
symbols and can check `__all__`; they're immediately superseded by the
`sys.modules` swap below, which is what actually runs."""

from __future__ import annotations

import sys

from hotline.pager import Pager, PagerError, PageResult, build_ladder, from_env

__all__ = ["PageResult", "Pager", "PagerError", "build_ladder", "from_env"]

from hotline import pager as _pager

sys.modules[__name__] = _pager
