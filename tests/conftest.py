from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway ~/.claude plus a throwaway runtime spool.

    Both are pointed at by environment variable rather than by patching module
    globals, because that is the same path the real hook scripts take -- so the
    tests exercise the configuration surface instead of going around it.
    """
    home = tmp_path / "claude"
    (home / "sessions").mkdir(parents=True)
    (home / "projects").mkdir(parents=True)
    monkeypatch.setenv("HOTLINE_CLAUDE_HOME", str(home))
    monkeypatch.setenv("HOTLINE_RUNTIME", str(tmp_path / "run"))
    return home
