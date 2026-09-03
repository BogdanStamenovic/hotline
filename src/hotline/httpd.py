"""Forever shim: `httpd.py` (the small HTTP/1.1 server `hotlined` runs on)
moved to hotline-claude. Kept here because `hotline-ios` does `from
hotline.httpd import HttpError` (and `Server`, `Request`) directly.

Aliases `sys.modules["hotline.httpd"]` to the real `hotline_claude.httpd`
module object rather than copying its names -- see tmuxen.py in this package
for why (module-identity-sensitive monkeypatching in the test suite). The plain imports above exist only so static tools (mypy, ruff) see the real
symbols and can check `__all__`; they're immediately superseded by the
`sys.modules` swap below, which is what actually runs."""

from __future__ import annotations

import sys

from hotline_claude.httpd import (
    MAX_BODY,
    MAX_HEADERS,
    MAX_LINE,
    READ_TIMEOUT,
    STATUS_TEXT,
    Handler,
    HttpError,
    Request,
    Server,
)

__all__ = ["MAX_BODY", "MAX_HEADERS", "MAX_LINE", "READ_TIMEOUT", "STATUS_TEXT", "Handler", "HttpError", "Request", "Server"]

from hotline_claude import httpd as _httpd

sys.modules[__name__] = _httpd
