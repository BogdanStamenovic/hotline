"""Shim: `hotline.phoneauth` did not move -- see config.py in this package
for why this aliases `sys.modules` rather than copying names. The plain imports above exist only so static tools (mypy, ruff) see the real
symbols and can check `__all__`; they're immediately superseded by the
`sys.modules` swap below, which is what actually runs."""

from __future__ import annotations

import sys

from hotline.phoneauth import (
    PhoneAuthError,
    Receipt,
    canonical_bytes,
    enroll,
    load_keys,
    load_receipt,
    persist_receipt,
    reverify,
    verify_message,
)

__all__ = ["PhoneAuthError", "Receipt", "canonical_bytes", "enroll", "load_keys", "load_receipt", "persist_receipt", "reverify", "verify_message"]

from hotline import phoneauth as _phoneauth

sys.modules[__name__] = _phoneauth
