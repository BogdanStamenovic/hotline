"""Forever shim: `daemon.py` (`hotlined`, the HTTP front end the iPhone
Shortcut talks to) moved to hotline-claude -- the `hotlined` console script
now points at `hotline_claude.daemon:main` directly (see hotline-claude's
pyproject.toml), but this path is kept in case anything still imports
`hotline.daemon` rather than running the installed script.

Aliases `sys.modules["hotline.daemon"]` to the real `hotline_claude.daemon`
module object rather than copying its names -- see tmuxen.py in this package
for why (module-identity-sensitive monkeypatching in the test suite). The plain imports above exist only so static tools (mypy, ruff) see the real
symbols and can check `__all__`; they're immediately superseded by the
`sys.modules` swap below, which is what actually runs."""

from __future__ import annotations

import sys

from hotline_claude.daemon import (
    CONVERSATIONAL_PREAMBLE,
    DEFAULT_HARD_TIMEOUT,
    DEFAULT_PORT,
    DEFAULT_SOFT_TIMEOUT,
    PENDING_REPLY,
    build_server,
    main,
    serve,
)

__all__ = ["CONVERSATIONAL_PREAMBLE", "DEFAULT_HARD_TIMEOUT", "DEFAULT_PORT", "DEFAULT_SOFT_TIMEOUT", "PENDING_REPLY", "build_server", "main", "serve"]

from hotline_claude import daemon as _daemon

sys.modules[__name__] = _daemon
