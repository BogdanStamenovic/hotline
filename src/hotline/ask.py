"""A PreToolUse bridge for `AskUserQuestion`: relay the question to the agent's
Discord channel and feed Bogdan's typed answer back as the tool result.

`AskUserQuestion` renders an interactive picker that only a human sitting at the
terminal can answer. A hotline-spawned agent has no such human: the picker hangs
the session forever, and a message injected in the meantime lands *inside* the
menu as a garbage selection rather than as a turn. That is exactly how `data-af`
wedged on 2026-09-01 -- it asked "public or private?", the picker opened, and
Bogdan's reply became an unusable menu entry.

This hook fires *before* the picker renders. It posts the question and its
options to `#agent-<name>`, waits for his reply, and returns a PreToolUse
`deny` whose reason carries his answer verbatim. The model reads that as the
tool's result and proceeds -- no menu ever appears, and nothing has to parse his
free text into a structured selection, because the model does that itself (it
maps "private" or "the second one" to the right option, which a keystroke
injector never could).

Scope, deliberately narrow: it acts only when `HOTLINE_SPAWNED` is set AND the
session resolves to a registered agent with a channel. Bogdan's own keyboard
sessions carry no `HOTLINE_SPAWNED`, so their picker is left exactly as it was --
this must never intercept a question he is sitting in front of.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

# How long to wait for his reply before handing the agent a sane fallback. A
# human answers in minutes; the ceiling only exists so a forgotten question does
# not pin a session forever. The settings.json hook `timeout` must be at least
# this plus a little slack, or Claude Code kills the hook mid-wait.
DEFAULT_WAIT = float(os.environ.get("HOTLINE_ASK_TIMEOUT", "1200"))
POLL_SECONDS = 3.0


def format_questions(questions: list[dict[str, Any]], name: str) -> str:
    """The Discord message he answers. Options are listed so he can reply with a
    label, a letter, or a sentence -- whatever is natural -- and the model maps
    it back. Nothing here depends on him using an exact string."""
    lines = [
        (
            f"**{name} is asking you a question** (its terminal picker can't reach "
            f"you, so answer here):"
        ),
        "",
    ]
    multi = len(questions) > 1
    for qi, q in enumerate(questions, 1):
        stem = str(q.get("question") or "").strip()
        header = str(q.get("header") or "").strip()
        prefix = f"**Q{qi}. " if multi else "**"
        head = f" *({header})*" if header else ""
        lines.append(f"{prefix}{stem}**{head}")
        for oi, opt in enumerate(q.get("options") or []):
            label = str(opt.get("label") or "").strip()
            desc = str(opt.get("description") or "").strip()
            bullet = f"  - **{label}**"
            if desc:
                bullet += f" — {desc}"
            lines.append(bullet)
        if q.get("multiSelect"):
            lines.append("  *(you can pick more than one)*")
        lines.append("")
    lines.append("_Reply with your choice(s) in your own words; I'll pass it straight back._")
    return "\n".join(lines).strip()


def _deny(*, answer: str | None, name: str, questions: list[dict[str, Any]]) -> dict[str, Any]:
    if answer is not None:
        reason = (
            f"Bogdan answered this AskUserQuestion via Discord (#agent-{name}), because "
            f"you are a headless hotline agent and the interactive picker cannot reach "
            f"him. His answer, verbatim:\n\n{answer}\n\nTreat this as his response to the "
            f"question(s) and proceed on it. Map it to the option(s) yourself; do not call "
            f"AskUserQuestion again for the same thing."
        )
    else:
        wait_min = int(DEFAULT_WAIT // 60)
        reason = (
            f"This AskUserQuestion was relayed to Bogdan in #agent-{name} (the interactive "
            f"picker cannot reach a headless agent), but he did not reply within "
            f"{wait_min} minutes. Do not sit on the picker. Pick the safest, most "
            f"reversible option, state which you picked and why in your channel, and "
            f"continue; he can correct it. Only stop and wait if no option is safe to "
            f"take without him."
        )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _wait_for_reply(
    pager: Any, channel_id: str, anchor: str, deadline: float, sleep: Callable[[float], None]
) -> str | None:
    """His first reply in the channel after `anchor`, or None if the clock runs out."""
    while time.monotonic() < deadline:
        replies = pager.replies_since(channel_id, anchor)
        for text in replies:
            if text.strip():
                return text.strip()
        sleep(POLL_SECONDS)
    return None


def decide(
    payload: dict[str, Any],
    *,
    registry: Any = None,
    pager: Any = None,
    wait: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> dict[str, Any] | None:
    """The hook's whole decision. Returns a PreToolUse output dict to emit, or
    None to stay silent (which lets the normal picker render).

    Silent -- never intercepts -- unless every condition holds: the session is a
    spawned agent, the tool really is AskUserQuestion with questions, and it maps
    to a registered agent that has a channel to ask in.
    """
    if not os.environ.get("HOTLINE_SPAWNED"):
        return None
    if payload.get("tool_name") != "AskUserQuestion":
        return None
    questions = (payload.get("tool_input") or {}).get("questions") or []
    if not questions:
        return None
    session_id = str(payload.get("session_id") or "")
    if not session_id:
        return None

    if registry is None:
        from .agents import Registry

        registry = Registry()
    agent = registry.get(session_id)
    if agent is None or agent.channel_id is None:
        # Spawned, but nothing to relay to. Better to let the picker render than
        # to swallow the question silently -- at least the pane shows it is stuck.
        return None

    if pager is None:
        from .pager import from_env

        pager = from_env()

    channel_id = str(agent.channel_id)
    message = format_questions(questions, agent.name)
    anchor = pager.send(channel_id, message)
    deadline = now() + (wait if wait is not None else DEFAULT_WAIT)
    answer = _wait_for_reply(pager, channel_id, anchor, deadline, sleep)
    return _deny(answer=answer, name=agent.name, questions=questions)


HOOK_SCRIPT = '''#!/usr/bin/env python3
"""hotline AskUserQuestion bridge -- relays the picker to Discord and feeds his
answer back, so a headless agent never hangs on a menu nobody is sitting at.
Installed by `hotline --install-hook`. See hotline/ask.py for the reasoning.
Must always exit 0: a raise here would fail a real tool call."""
import json, sys

sys.path.insert(0, {package_root!r})
decision = None
try:
    from hotline.ask import decide
    from hotline.config import load_env
    load_env()
    decision = decide(json.load(sys.stdin))
except Exception:
    decision = None

if decision:
    json.dump(decision, sys.stdout)
sys.exit(0)
'''


def hook_path() -> str:
    from .config import claude_home

    return str(claude_home() / "hooks" / "hotline-ask.py")


def install_ask_hook() -> tuple[str, bool]:
    """Write the bridge hook and register it for AskUserQuestion. Idempotent and
    additive: it must never clobber his own hooks, and re-running must be a no-op.

    The hook self-gates on HOTLINE_SPAWNED, so registering it globally is safe --
    his keyboard sessions run it, it sees no spawn marker, and it returns silently
    before touching Discord or the picker."""
    import json
    from pathlib import Path

    from .config import settings_path

    package_root = str(Path(__file__).resolve().parent.parent)
    path = Path(hook_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HOOK_SCRIPT.format(package_root=package_root))
    path.chmod(0o755)

    settings_file = settings_path()
    try:
        settings = json.loads(settings_file.read_text())
    except (OSError, ValueError):
        settings = {}
    entries = settings.setdefault("hooks", {}).setdefault("PreToolUse", [])
    command = str(path)
    for entry in entries:
        for hook in entry.get("hooks", []):
            if hook.get("command") == command:
                return command, False
    # timeout must outlast DEFAULT_WAIT, or Claude Code kills the hook mid-wait
    # and the answer never lands. Slack on top of the wait ceiling.
    entries.append(
        {
            "matcher": "AskUserQuestion",
            "hooks": [{"type": "command", "command": command, "timeout": int(DEFAULT_WAIT) + 120}],
        }
    )
    settings_file.write_text(json.dumps(settings, indent=2) + "\n")
    return command, True
