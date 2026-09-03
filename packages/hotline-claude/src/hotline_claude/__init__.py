"""hotline-claude: the session-orchestration infrastructure underneath hotline.

Spawning, pooling and reviving Claude Code sessions; the control-socket and
transcript plumbing that talks to a live session; the session registry; and the
hooks (Stop, PreToolUse guard, AskUserQuestion bridge) that make a session
addressable by `hotline` at all. `router.py` lives here too -- it is one of
hotline's frozen files and is not edited by this split, but it drives the
session registry, control sockets, hooks and transcripts directly, so it moves
with them rather than staying behind in the comms-only `hotline` package.
"""

from __future__ import annotations

__version__ = "0.1.0"
