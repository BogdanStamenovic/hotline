"""A `PreToolUse` denylist for the handful of commands with no undo.

Rationale, from the plan: hotline drives Claude with `bypassPermissions` from a
phone, and on the voice path the command arrives via a speech recogniser. Whisper
will occasionally hear something other than what was said, and with bypass there is
no confirmation step to catch it. This is the cheap half of that problem -- it
cannot be talked around by a bad prompt, because it is a hook, not an instruction.

It is deliberately *tiny*. Everything here destroys a disk or a filesystem and
cannot be undone by a timeshift snapshot living on the partition being destroyed.
Ordinary destructive work -- `rm -rf` on a project directory, `git reset --hard`,
dropping a table -- is not listed, because a guard that fires on normal work gets
switched off, and then it protects nothing.

The other half of the problem (a mis-transcription that runs something merely
*wrong* rather than catastrophic) is not solvable here and is not pretended to be.
"""

from __future__ import annotations

import re

# Each entry: (compiled pattern, what to tell the model). Patterns are matched
# against the raw command string with no shell parsing -- a hook that tried to
# understand shell grammar would be a bug farm, and the failure mode we care about
# is a plain-looking command, not an obfuscated one.
RULES: list[tuple[re.Pattern[str], str]] = [
    (
        # `rm -rf /` and friends. The trailing guard is what keeps this from firing
        # on every legitimate `rm -rf /home/bodas/some/project`: root only counts
        # when the slash is the whole path, or is globbed.
        re.compile(r"\brm\b[^|;&\n]*?\s-[a-zA-Z]*[rR][a-zA-Z]*f|\brm\b[^|;&\n]*?\s-[a-zA-Z]*f[a-zA-Z]*[rR]", re.IGNORECASE),
        "recursive forced delete of the filesystem root",
    ),
    (re.compile(r"\bmkfs(\.\w+)?\b", re.IGNORECASE), "creating a filesystem (destroys the target device)"),
    (re.compile(r"\bdd\b[^|;&\n]*\bof=\s*/dev/", re.IGNORECASE), "dd writing directly to a block device"),
    (re.compile(r">\s*/dev/(sd[a-z]|nvme\d|vd[a-z]|mmcblk\d)", re.IGNORECASE), "redirecting output onto a raw disk"),
    (re.compile(r"\bwipefs\b", re.IGNORECASE), "wiping filesystem signatures"),
    (re.compile(r"\bshred\b[^|;&\n]*\s/dev/", re.IGNORECASE), "shredding a block device"),
    (re.compile(r"\b(sgdisk|parted|fdisk)\b[^|;&\n]*(--zap-all|\bmklabel\b|\bo\b\s*$)", re.IGNORECASE),
     "destroying a partition table"),
]

# Only the root-delete rule needs the second stage; the others are unconditional.
_ROOT_TARGET = re.compile(r"(?:^|\s)(?:--\s+)?/(?:\s|$|\*)")


def _is_root_delete(command: str) -> bool:
    """True only when an `rm -rf` actually targets `/` itself.

    Split on shell separators first so that `cd /tmp/x && rm -rf .` is judged on
    the `rm` clause alone and a `/` appearing in an earlier clause cannot convict it.
    """
    for clause in re.split(r"[|;&\n]+", command):
        if not re.search(r"\brm\b", clause, re.IGNORECASE):
            continue
        if not re.search(r"-[a-zA-Z]*[rR][a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*[rR]", clause):
            continue
        args = re.sub(r"\brm\b|\s-[a-zA-Z-]+", " ", clause, flags=re.IGNORECASE)
        if _ROOT_TARGET.search(args):
            return True
    return False


def check(tool_name: str, tool_input: dict[str, object]) -> str | None:
    """Return a refusal reason, or None to let the call through."""
    if tool_name != "Bash":
        return None
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return None

    for pattern, reason in RULES:
        if not pattern.search(command):
            continue
        if reason.startswith("recursive forced delete") and not _is_root_delete(command):
            continue
        return reason
    return None


HOOK_SCRIPT = '''#!/usr/bin/env python3
"""hotline PreToolUse guard -- refuses the handful of commands that destroy a disk.

hotline runs Claude with bypassPermissions from a phone, sometimes via speech
recognition, so there is no human in the loop to catch a mis-heard command. This
blocks only what cannot be undone. Remove the "PreToolUse" entry from
~/.claude/settings.json to disable it. See hotline/guard.py for the reasoning.
"""
import json, sys

sys.path.insert(0, {package_root!r})
try:
    from hotline.guard import check
    payload = json.load(sys.stdin)
    reason = check(str(payload.get("tool_name") or ""), payload.get("tool_input") or {{}})
except Exception:
    reason = None

if reason:
    json.dump({{"hookSpecificOutput": {{
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            "hotline guard: refusing " + reason + ". This is blocked unconditionally "
            "because it cannot be undone. If you genuinely meant it, a human has to "
            "run it at the keyboard."),
    }}}}, sys.stdout)
sys.exit(0)
'''


def install_guard() -> tuple[str, bool]:
    """Write the guard hook and register it for Bash. Idempotent and additive."""
    import json
    from pathlib import Path

    from .config import claude_home, settings_path

    package_root = str(Path(__file__).resolve().parent.parent)
    path = claude_home() / "hooks" / "hotline-guard.py"
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
    entries.append(
        {"matcher": "Bash", "hooks": [{"type": "command", "command": command, "timeout": 5}]}
    )
    settings_file.write_text(json.dumps(settings, indent=2) + "\n")
    return command, True
