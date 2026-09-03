"""Forever shim: `transcript.py` (reading a Claude session's transcript)
moved to hotline-claude.

Aliases `sys.modules["hotline.transcript"]` to the real
`hotline_claude.transcript` module object rather than copying its names --
see tmuxen.py in this package for why (module-identity-sensitive
monkeypatching in the test suite). The plain imports above exist only so static tools (mypy, ruff) see the real
symbols and can check `__all__`; they're immediately superseded by the
`sys.modules` swap below, which is what actually runs."""

from __future__ import annotations

import sys

from hotline_claude.transcript import (
    MAX_SLICE_BYTES,
    SYNTHETIC_USER_PREFIXES,
    Slice,
    TranscriptEvent,
    Turn,
    events_since,
    read_since,
    sidechain_paths,
    size_of,
    transcript_path,
    turn_in_flight,
)

__all__ = ["MAX_SLICE_BYTES", "SYNTHETIC_USER_PREFIXES", "Slice", "TranscriptEvent", "Turn", "events_since", "read_since", "sidechain_paths", "size_of", "transcript_path", "turn_in_flight"]

from hotline_claude import transcript as _transcript

sys.modules[__name__] = _transcript
