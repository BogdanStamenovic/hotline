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

**Matching is by command position, not by substring.** The first version matched
raw command strings anywhere, and blocked four legitimate calls during this build
whose only sin was *writing about* the commands it guards -- a test file, and a
status message explaining the guard itself. A denylist that cannot tell a command
from a mention of one is a denylist people route around, and routing around it is
exactly the habit it exists to prevent. So each clause is split on shell separators
and the dangerous name must be the thing being *run*: first token, after stripping
`sudo`, `env VAR=x` and similar prefixes. `sh -c "..."` is unwrapped and its
argument checked in turn, so the obvious escape still gets caught.

The other half of the problem (a mis-transcription that runs something merely
*wrong* rather than catastrophic) is not solvable here and is not pretended to be.
"""

from __future__ import annotations

import re
import shlex

# Prefixes that stand in front of the real command without being it.
_PREFIXES = {"sudo", "doas", "env", "time", "nohup", "nice", "ionice", "command", "exec", "setsid"}
_SHELLS = {"sh", "bash", "zsh", "dash", "ash", "ksh"}

# Binaries that are catastrophic no matter what arguments they are given.
_ALWAYS: dict[str, str] = {
    "mkfs": "creating a filesystem (destroys the target device)",
    "wipefs": "wiping filesystem signatures",
}

_DEVICE = re.compile(r"/dev/(sd[a-z]|nvme\d|vd[a-z]|mmcblk\d|disk\d)", re.IGNORECASE)
_RECURSIVE_FORCE = re.compile(r"^-[a-zA-Z]*(?:[rR][a-zA-Z]*f|f[a-zA-Z]*[rR])[a-zA-Z]*$")
_ROOT_PATH = re.compile(r"^/\*?$")
# A redirect onto a raw device is not a command, so it is the one thing still
# matched against the clause text rather than an argv.
_REDIRECT_TO_DEVICE = re.compile(r">\s*/dev/(sd[a-z]|nvme\d|vd[a-z]|mmcblk\d)", re.IGNORECASE)


def _split_clauses(command: str) -> list[str]:
    return [clause for clause in re.split(r"[|;\n]+|&&|\|\||&", command) if clause.strip()]


def _argv(clause: str) -> list[str]:
    """Best-effort argv for a clause. Unparseable quoting means we cannot judge it."""
    try:
        return shlex.split(clause, comments=True)
    except ValueError:
        return []


def _strip_prefixes(argv: list[str]) -> list[str]:
    """Drop `sudo`, `env FOO=bar`, and friends until the real command is in front."""
    index = 0
    while index < len(argv):
        token = argv[index]
        base = token.rsplit("/", 1)[-1]
        if base in _PREFIXES or ("=" in token and not token.startswith("-") and index > 0):
            index += 1
            continue
        if base == "env":
            index += 1
            continue
        break
    # `env FOO=bar cmd` leaves the assignments in front of cmd.
    while index < len(argv) and "=" in argv[index] and not argv[index].startswith("-"):
        index += 1
    return argv[index:]


def _inspect(argv: list[str], clause: str, depth: int = 0) -> str | None:
    argv = _strip_prefixes(argv)
    if not argv:
        return None
    base = argv[0].rsplit("/", 1)[-1]
    rest = argv[1:]

    if base in _SHELLS and depth < 2:
        # `bash -c "<command>"` -- judge what it would actually run.
        for index, token in enumerate(rest):
            if token == "-c" and index + 1 < len(rest):
                inner = rest[index + 1]
                for sub in _split_clauses(inner):
                    found = _inspect(_argv(sub), sub, depth + 1)
                    if found:
                        return found
        return None

    for name, reason in _ALWAYS.items():
        if base == name or base.startswith(name + "."):
            return reason

    if base == "rm":
        forced = any(_RECURSIVE_FORCE.match(token) for token in rest if token.startswith("-"))
        targets = [token for token in rest if not token.startswith("-")]
        if forced and any(_ROOT_PATH.match(token) for token in targets):
            return "a recursive forced delete of the filesystem root"
        return None

    if base == "dd":
        for token in rest:
            if token.startswith("of=") and _DEVICE.search(token):
                return "dd writing directly to a block device"
        return None

    if base == "shred":
        if any(_DEVICE.search(token) for token in rest):
            return "shredding a block device"
        return None

    if base in {"sgdisk", "parted", "fdisk", "sfdisk"}:
        lowered = [token.lower() for token in rest]
        if "--zap-all" in lowered or "-Z" in rest or "mklabel" in lowered:
            return "destroying a partition table"
        return None

    return None


def check(tool_name: str, tool_input: dict[str, object]) -> str | None:
    """Return a refusal reason, or None to let the call through."""
    if tool_name != "Bash":
        return None
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return None

    for clause in _split_clauses(command):
        if _REDIRECT_TO_DEVICE.search(clause):
            return "redirecting output onto a raw disk"
        found = _inspect(_argv(clause), clause)
        if found:
            return found
    return None


HOOK_SCRIPT = '''#!/usr/bin/env python3
"""hotline PreToolUse guard -- refuses the handful of commands that destroy a disk.

hotline runs Claude with bypassPermissions from a phone, sometimes via speech
recognition, so there is no human in the loop to catch a mis-heard command. This
blocks only what cannot be undone. Remove the "PreToolUse" entry from
~/.claude/settings.json to disable it. See hotline/guard.py for the reasoning."""
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


def hook_path() -> str:
    from .config import claude_home

    return str(claude_home() / "hooks" / "hotline-guard.py")


def install_guard() -> tuple[str, bool]:
    """Write the guard hook and register it for Bash. Idempotent and additive."""
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
    entries.append(
        {"matcher": "Bash", "hooks": [{"type": "command", "command": command, "timeout": 5}]}
    )
    settings_file.write_text(json.dumps(settings, indent=2) + "\n")
    return command, True
