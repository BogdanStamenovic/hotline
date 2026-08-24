"""hotline: voice/text bridge to Claude Code sessions."""

from __future__ import annotations

__version__ = "0.1.0"

from .cli import main
from .errors import HotlineError
from .router import Route, Router, parse_utterance

__all__ = [
    "HotlineError",
    "Route",
    "Router",
    "__version__",
    "main",
    "parse_utterance",
]
