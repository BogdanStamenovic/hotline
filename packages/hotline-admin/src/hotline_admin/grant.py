"""`hotline --grant NAME ROLE <message>` -- the one verb this plugin owns.

Moved out of `hotline.cli` verbatim (behavior unchanged): record a role and
the Discord message where a human granted it, so a reader can check the
delegation against Discord instead of trusting this machine's say-so. Granting
authority is an admin action; `hotline` core discovers this module through the
`hotline.plugins` entry point declared in this package's pyproject.toml rather
than importing it directly, so a `hotline` install with no `hotline-admin`
simply doesn't have `--grant`.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from hotline_claude.agents import Registry

from hotline.config import load_env
from hotline.provenance import verify


def grant_role(
    name: str, role: str, where: str, registry: Registry, log: Callable[[str], None]
) -> int:
    """`--grant NAME ROLE <message>` -- record a role and where it was granted.

    The message is required, not optional. A role recorded without one is an
    assertion this machine makes about itself, and the entire value of the role
    is that a reader can check the delegation against Discord instead.
    """
    parts = [p for p in where.replace("https://discord.com/channels/", "").split("/") if p]
    if len(parts) < 2 or not all(p.isdigit() for p in parts[-2:]):
        print(
            "hotline: error: pass the Discord message where he granted it -- a "
            "message link, or 'channel_id/message_id'. A role with no receipt is "
            "just this machine vouching for itself.",
            file=sys.stderr,
        )
        return 2
    channel_id, message_id = parts[-2], parts[-1]

    env = load_env()
    verdict = verify(
        {"kind": "sys-admin", "label": name, "granted_by": message_id, "granted_in": channel_id},
        token=env.get("HOTLINE_BOT_TOKEN"),
        gated_user_id=env.get("DISCORD_USER_ID"),
    )
    if not verdict.ok:
        # Refuse rather than record-and-warn. A role that half-verified would be
        # read as a role.
        print(f"hotline: error: not granting it -- {verdict.summary}", file=sys.stderr)
        return 1

    agent = registry.grant(name, role, message_id, channel_id)
    if agent is None:
        print(f"hotline: error: no agent called {name!r}. Try --agents.", file=sys.stderr)
        return 1
    log(f"granted: {agent.describe()}")
    print(verdict)
    return 0


def register() -> Callable[[str, str, str, Registry, Callable[[str], None]], int]:
    """Entry point hook: hand `hotline.cli` the callable it dispatches `--grant` to."""
    return grant_role
