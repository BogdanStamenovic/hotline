"""Shim: `hotline.provenance` is frozen and did not move -- see config.py in
this package for why this aliases `sys.modules` rather than copying names.
The plain imports above exist only so static tools (mypy, ruff) see the real
symbols and can check `__all__`; they're immediately superseded by the
`sys.modules` swap below, which is what actually runs."""

from __future__ import annotations

import sys

from hotline.provenance import MARKER, Origin, Verdict, body_of, digest, parse, verify

__all__ = ["MARKER", "Origin", "Verdict", "body_of", "digest", "parse", "verify"]

from hotline import provenance as _provenance

sys.modules[__name__] = _provenance
