"""Shim: `hotline.mirror` did not move -- see config.py in this package for
why this aliases `sys.modules` rather than copying names. The plain imports above exist only so static tools (mypy, ruff) see the real
symbols and can check `__all__`; they're immediately superseded by the
`sys.modules` swap below, which is what actually runs."""

from __future__ import annotations

import sys

from hotline.mirror import mirror_sent, read_state

__all__ = ["mirror_sent", "read_state"]

from hotline import mirror as _mirror

sys.modules[__name__] = _mirror
