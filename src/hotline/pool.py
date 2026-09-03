"""Forever shim: `pool.py` (the session pool `hotlined` and `hotline-ios`
both drive) moved to hotline-claude. Kept here because `hotline-ios` does
`from hotline.pool import SessionPool` directly, and `hotline.bot` still
says `from .pool import SessionPool`.

Aliases `sys.modules["hotline.pool"]` to the real `hotline_claude.pool`
module object rather than copying its names. This one matters twice over:
the test suite monkeypatches `pool_module.tmuxen.spawn` (reaching through
`pool.py`'s own `from . import tmuxen` binding), which only has any effect
on the real code path if `hotline.pool` and `hotline_claude.pool` are the
same object -- a name-copying shim would patch a dead copy. The plain imports above exist only so static tools (mypy, ruff) see the real
symbols and can check `__all__`; they're immediately superseded by the
`sys.modules` swap below, which is what actually runs."""

from __future__ import annotations

import sys

# `pool.py` itself does `from . import standin, tmuxen`, so those submodules are
# attributes of the real module at runtime and the test suite patches straight
# through them (`pool_module.standin.report`). Re-imported here so the static
# view of `hotline.pool` matches the runtime one -- without this, mypy rejects
# `pool_module.standin` as an attribute that does not exist, on a line that
# works fine.
from hotline_claude import standin, tmuxen
from hotline_claude.pool import (
    HELP_TEXT,
    ORPHAN_TIMEOUT,
    RELAY_TIMEOUT,
    Conversation,
    Deliver,
    SessionPool,
    describe_resources,
)

__all__ = ["HELP_TEXT", "ORPHAN_TIMEOUT", "RELAY_TIMEOUT", "Conversation", "Deliver", "SessionPool", "describe_resources", "standin", "tmuxen"]

from hotline_claude import pool as _pool

sys.modules[__name__] = _pool
