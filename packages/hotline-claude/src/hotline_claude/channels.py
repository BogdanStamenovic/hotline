"""Shim: `hotline.channels` did not move -- see config.py in this package
for why this aliases `sys.modules` rather than copying names. This is the
one that was actually caught by the test suite: `pool.py`'s
`from .channels import from_env as channels_from_env` is a function-local
import, re-resolved on every call, and the tests monkeypatch
`hotline.channels.from_env` directly -- a name-copying shim here would have
made every such patch invisible to `pool.py`. The plain imports above exist only so static tools (mypy, ruff) see the real
symbols and can check `__all__`; they're immediately superseded by the
`sys.modules` swap below, which is what actually runs."""

from __future__ import annotations

import sys

from hotline.channels import Channels, from_env, slug

__all__ = ["Channels", "from_env", "slug"]

from hotline import channels as _channels

sys.modules[__name__] = _channels
