#!/usr/bin/env python3
"""Refuse to commit anything containing a value from .env.

Written after a real near-miss: Bogdan's guild and channel ids ended up as
constants in a test file, staged for a push to a public repo. `.env` being
gitignored does not help when the *values* get copied somewhere else, and no
amount of care replaces a check that actually runs.

Installed as .git/hooks/pre-commit. Scans the staged content, not the worktree,
because those differ exactly when it matters.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Short values would match by coincidence; a snowflake is 17-20 digits and a
# token is far longer, so nothing legitimate is this long by accident.
MIN_LENGTH = 12


def secrets() -> dict[str, str]:
    found: dict[str, str] = {}
    try:
        lines = (ROOT / ".env").read_text().splitlines()
    except OSError:
        return found
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= MIN_LENGTH and not value.startswith("/"):
            found[key.strip()] = value
    return found


def staged_files() -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    return [f for f in out.stdout.split("\n") if f]


def main() -> int:
    values = secrets()
    if not values:
        return 0
    leaks: list[tuple[str, str]] = []
    for path in staged_files():
        blob = subprocess.run(
            ["git", "show", f":{path}"], capture_output=True, cwd=ROOT, check=False
        ).stdout.decode("utf-8", "replace")
        leaks += [(name, path) for name, value in values.items() if value in blob]

    if leaks:
        print("REFUSING TO COMMIT -- a value from .env appears in staged content:", file=sys.stderr)
        for name, path in leaks:
            print(f"  {name} -> {path}", file=sys.stderr)
        print(
            "\nReplace it with a placeholder. Never commit these, even to a private repo.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
