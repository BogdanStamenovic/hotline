from __future__ import annotations

from pathlib import Path

from helpers import make_session

from hotline.cli import main


def test_version(capsys) -> None:
    try:
        main(["--version"])
    except SystemExit:
        pass
    assert "hotline" in capsys.readouterr().out


def test_nothing_to_say_is_a_usage_error(fake_claude: Path, capsys) -> None:
    assert main([]) == 2
    assert "nothing to say" in capsys.readouterr().err


def test_unknown_flag_is_a_usage_error_not_a_crash(fake_claude: Path, capsys) -> None:
    assert main(["--nope"]) == 2
    assert "error" in capsys.readouterr().err


def test_list_prints_sessions_newest_first(fake_claude: Path, capsys) -> None:
    make_session(fake_claude, 100, "data-d6", "/home/bodas/data", "aaa", started_at=1000)
    make_session(fake_claude, 300, "uxo-7f", "/home/bodas/data/uxonews", "ccc", started_at=3000)
    assert main(["--list"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert "uxo-7f" in lines[0] and "newest" in lines[0]
    assert "data-d6" in lines[1] and "oldest" in lines[1]


def test_list_with_nothing_live(fake_claude: Path, capsys) -> None:
    assert main(["--list"]) == 0
    assert "no live Claude sessions" in capsys.readouterr().err


def test_unresolvable_target_exits_one(fake_claude: Path, capsys) -> None:
    assert main(["--to", "ghost", "hello"]) == 1
    assert "error" in capsys.readouterr().err


def test_install_hook(fake_claude: Path, capsys) -> None:
    (fake_claude / "settings.json").write_text("{}")
    assert main(["--install-hook"]) == 0
    err = capsys.readouterr().err
    assert "Stop hook installed" in err
    assert "PreToolUse guard installed" in err


def test_install_hook_can_skip_the_guard(fake_claude: Path, capsys) -> None:
    (fake_claude / "settings.json").write_text("{}")
    assert main(["--install-hook", "--no-guard"]) == 0
    assert "skipped" in capsys.readouterr().err


# ---- control phrases are answered by hotline, not by a model ------------
#
# `hotline "session kill data-b1"` used to spawn a fresh session and ask it to
# kill something. Cleaning up two stray sessions that way made two more, plus a
# hung shell.


def test_a_control_phrase_never_reaches_a_model(monkeypatch: pytest.MonkeyPatch) -> None:
    import hotline.cli as cli_module

    def explode(*args: object, **kwargs: object):
        raise AssertionError("a control phrase was sent to a model")

    monkeypatch.setattr(cli_module.Router, "ask_fresh", explode)
    monkeypatch.setattr(cli_module.Router, "sessions", lambda self: [])
    assert cli_module.main(["session", "list"]) == 0


def test_kill_without_a_real_session_falls_through_to_a_question(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """"kill the process on port 9999" is a question. Answering it with a
    resolution error would make the feature eat ordinary sentences."""
    import hotline.cli as cli_module
    from hotline.errors import SessionNotFound
    from hotline.fresh import Reply

    asked: list[str] = []

    async def fake_fresh(self, text, narrator=None, cwd=None, timeout=300.0):
        asked.append(text)
        return Reply(text="that would be lsof")

    def no_such(self, spec):
        raise SessionNotFound(spec)

    monkeypatch.setattr(cli_module.Router, "resolve", no_such)
    monkeypatch.setattr(cli_module.Router, "ask_fresh", fake_fresh)
    assert cli_module.main(["kill", "the", "process", "on", "port", "9999"]) == 0
    assert asked == ["kill the process on port 9999"]
