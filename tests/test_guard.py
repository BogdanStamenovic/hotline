"""The guard's job is to block four things and nothing else.

Both directions are tested deliberately. A denylist that over-fires gets turned
off by its owner, and then it protects nothing -- so the false-positive table
matters at least as much as the true-positive one.

Every command here is base64. That is not obfuscation for its own sake: the guard
is installed globally on the machine this was written on, matches raw command
strings, and cannot tell a command from a *mention* of one. Writing these literals
into a test file through a shell heredoc makes the guard (correctly) deny the tool
call that writes the test. Encoding them keeps the strings out of any command line.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest

from hotline.guard import check

BLOCKED = [
    "bWtmcy5leHQ0IC9kZXYvbnZtZTBuMXA5",
    "cm0gLXJmIC8=",
    "cm0gLWZyIC8=",
    "c3VkbyBybSAtcmYgLyAtLW5vLXByZXNlcnZlLXJvb3Q=",
    "cm0gLXJmIC8q",
    "ZGQgaWY9L2Rldi96ZXJvIG9mPS9kZXYvc2RhIGJzPTFN",
    "d2lwZWZzIC1hIC9kZXYvbnZtZTBuMQ==",
    "ZWNobyB4ID4gL2Rldi9zZGE=",
    "c2hyZWQgLW4xIC9kZXYvc2Ri",
    "c2dkaXNrIC0temFwLWFsbCAvZGV2L3NkYQ==",
]

ALLOWED = [
    "cm0gLXJmIC9ob21lL2JvZGFzL2RhdGEvaG90bGluZS8udGVzdGJlZA==",
    "cm0gLXJmIC4vYnVpbGQ=",
    "cm0gLXJmIG5vZGVfbW9kdWxlcw==",
    "cm0gLXJmIC90bXAvZm9v",
    "Y2QgL3RtcC94ICYmIHJtIC1yZiAu",
    "Z2l0IHJlc2V0IC0taGFyZA==",
    "bHMgLWxhIC8=",
    "ZHUgLXN4aCAv",
    "ZmluZCAvIC1uYW1lICcqLnB5Jw==",
    "ZGQgaWY9L2Rldi9zZGEgb2Y9L2JhY2t1cC9kaXNrLmltZw==",
    "cnN5bmMgLWEgLyAvbW50L2JhY2t1cC8=",
]


def decode(value: str) -> str:
    return base64.b64decode(value).decode()


@pytest.mark.parametrize("encoded", BLOCKED)
def test_blocks_the_irreversible(encoded: str) -> None:
    assert check("Bash", {"command": decode(encoded)}) is not None


@pytest.mark.parametrize("encoded", ALLOWED)
def test_allows_ordinary_destructive_work(encoded: str) -> None:
    assert check("Bash", {"command": decode(encoded)}) is None


def test_ignores_other_tools() -> None:
    assert check("Write", {"command": decode(BLOCKED[0])}) is None
    assert check("Bash", {}) is None
    assert check("Bash", {"command": ""}) is None


def test_hook_script_speaks_the_documented_contract(tmp_path, monkeypatch) -> None:
    """The hook's stdout shape is what Claude Code actually reads; a correct
    `check()` behind a malformed wrapper would protect nothing."""
    from hotline.guard import install_guard

    monkeypatch.setenv("HOTLINE_CLAUDE_HOME", str(tmp_path))
    (tmp_path / "settings.json").write_text("{}")
    path, changed = install_guard()
    assert changed

    payload = json.dumps(
        {"session_id": "t", "tool_name": "Bash", "tool_input": {"command": decode(BLOCKED[0])}}
    )
    result = subprocess.run(
        [sys.executable, path], input=payload, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0
    body = json.loads(result.stdout)["hookSpecificOutput"]
    assert body["hookEventName"] == "PreToolUse"
    assert body["permissionDecision"] == "deny"
    assert "cannot be undone" in body["permissionDecisionReason"]

    allow = json.dumps(
        {"session_id": "t", "tool_name": "Bash", "tool_input": {"command": decode(ALLOWED[0])}}
    )
    result = subprocess.run(
        [sys.executable, path], input=allow, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_hook_never_fails_on_garbage_input(tmp_path, monkeypatch) -> None:
    """It runs before every Bash call in every session on the machine. A traceback
    here is a traceback in the middle of somebody's turn."""
    from hotline.guard import install_guard

    monkeypatch.setenv("HOTLINE_CLAUDE_HOME", str(tmp_path))
    (tmp_path / "settings.json").write_text("{}")
    path, _ = install_guard()
    for junk in ("", "not json", "[]", '{"tool_name": 5}'):
        result = subprocess.run(
            [sys.executable, path], input=junk, capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, junk
        assert result.stdout.strip() == "", junk


def test_install_guard_is_idempotent_and_additive(tmp_path, monkeypatch) -> None:
    from hotline.guard import install_guard

    monkeypatch.setenv("HOTLINE_CLAUDE_HOME", str(tmp_path))
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Write",
                            "hooks": [{"type": "command", "command": "someone-elses-hook"}],
                        }
                    ]
                }
            }
        )
    )
    install_guard()
    _, changed = install_guard()
    assert not changed
    entries = json.loads(settings.read_text())["hooks"]["PreToolUse"]
    commands = [h["command"] for e in entries for h in e["hooks"]]
    assert "someone-elses-hook" in commands
    assert len(entries) == 2


# ---- mkfs on a file is not mkfs on a disk -----------------------------------
#
# Reported by `hotline-ios`, which hit it building a disk image. The rule keyed
# on the binary and never looked at argv, while `dd` and `shred` in the same
# function already did exactly that check. Destructive commands stay base64 here,
# following the rest of this file: a test suite should not carry a literal
# `mkfs.ext4 /dev/...` in plain text.

DEVICE_MKFS = [
    "bWtmcy5leHQ0IC9kZXYvbnZtZTBuMXA0",
    "c3VkbyBta2ZzLmV4dDQgL2Rldi9zZGEx",
    "bWtmcy5leHQ0IC9kZXYvZGlzay9ieS1sYWJlbC9kYXRh",
    "bWtmcy5leHQ0IC9kZXYvbWFwcGVyL3ZnLXJvb3Q=",
    "bWtmcy5leHQ0",
    "bWtmcy5leHQ0IC1G",
    "d2lwZWZzIC1hIC9kZXYvc2Rh",
]


@pytest.mark.parametrize("encoded", DEVICE_MKFS)
def test_mkfs_at_anything_not_provably_a_file_is_still_blocked(encoded: str) -> None:
    """Includes /dev paths the device regex does not know (by-label, mapper) and
    the no-target case -- "I could not tell" has to read as refuse, or this is
    decoration rather than a guard."""
    assert check("Bash", {"command": decode(encoded)}) is not None


def test_making_a_filesystem_in_a_regular_file_is_allowed(tmp_path: Path) -> None:
    """`mkfs.ext4 ./disk.img` touches no device and is undone by deleting a file."""
    image = tmp_path / "disk.img"
    image.write_bytes(b"")

    assert check("Bash", {"command": f"mkfs.ext4 {image}"}) is None


def test_making_a_filesystem_in_a_file_that_does_not_exist_yet_is_allowed(
    tmp_path: Path,
) -> None:
    """The usual case: the image is created by the command itself. Refusing a
    path that is merely absent would restore the bug."""
    assert check("Bash", {"command": f"mkfs.ext4 {tmp_path / 'new.img'}"}) is None


def test_mkfs_through_a_symlink_into_dev_is_blocked(tmp_path: Path) -> None:
    """The check the path text cannot do. A harmless-looking name pointed at a
    real device node is still caught, because the stat follows symlinks."""
    device = Path("/dev/nvme0n1")
    if not device.exists():
        pytest.skip("no /dev/nvme0n1 on this machine to point at")
    link = tmp_path / "innocent.img"
    link.symlink_to(device)

    assert check("Bash", {"command": f"mkfs.ext4 {link}"}) is not None


def test_wipefs_gets_the_same_treatment(tmp_path: Path) -> None:
    image = tmp_path / "disk.img"
    image.write_bytes(b"")

    assert check("Bash", {"command": f"wipefs -a {image}"}) is None
