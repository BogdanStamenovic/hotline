"""The watcher's behaviour around the detector: gates, dedupe, and safety.

The safety tests here are the important ones. A watcher that can answer a
permission prompt has removed the guard those prompts exist to be, so "it does
not do that" is asserted against the source rather than trusted.
"""

from __future__ import annotations

import time
import tokenize
from pathlib import Path

from hotline import permwatch, tmuxen
from hotline.permprompt import detect
from hotline.permwatch import Blocked, Ledger

PANES = Path(__file__).parent / "panes"


def _code_only(path: Path) -> str:
    """The module's executable text, with comments and strings removed.

    The first version of these tests grepped the raw file and failed on this
    module's own docstrings, which necessarily *name* the things it must not do
    -- "send-keys is forbidden", "no shell=True". Grepping prose for a
    capability is the same mistake `guard.py` records making: it blocked four
    legitimate calls whose only sin was writing about the commands it guards.
    So the check is against code, and the documentation is free to explain
    itself.
    """
    kept: list[str] = []
    with path.open("rb") as fh:
        for token in tokenize.tokenize(fh.readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            kept.append(token.string)
    return " ".join(kept)


SOURCE = _code_only(Path(permwatch.__file__))


# ---- safety ---------------------------------------------------------------


def test_the_watcher_cannot_type_into_a_pane() -> None:
    """`send-keys` in any form is the thing this module must never reach for.

    Checked against the source because the guarantee is structural: it holds
    because the code does not contain the capability, not because a branch
    happens not to be taken.
    """
    for forbidden in ("send_keys", "send_command", "interrupt", "Escape"):
        assert forbidden not in SOURCE, f"permwatch must not be able to {forbidden}"


def test_the_watcher_imports_only_read_only_tmux_verbs() -> None:
    used = {name for name in ("panes", "capture") if f"tmuxen . {name}" in SOURCE}
    assert used == {"panes", "capture"}
    for writer in ("spawn", "kill", "send_command", "interrupt"):
        assert f"tmuxen . {writer}" not in SOURCE


def test_no_auto_answer_configuration_exists() -> None:
    """There is deliberately no safe-list, allowlist or auto-approve setting."""
    for knob in ("auto_approve", "autoapprove", "allowlist", "safe_list", "answer("):
        assert knob not in SOURCE


def test_messages_are_never_composed_into_a_shell() -> None:
    """Backticks in a pane capture must not become command substitution.

    On 2026-09-19 an agent's message mentioned a script path in backticks and
    the shell ran it. Every message this module sends is scraped from a pane
    that was running commands, so it is the worst possible thing to interpolate.
    """
    assert "shell" not in SOURCE
    assert "os . system" not in SOURCE
    assert "os . popen" not in SOURCE


# ---- gates ----------------------------------------------------------------


def _blocked(name: str = "pw-test", fixture: str = "bash-permission.txt", **kw) -> Blocked:
    pane = tmuxen.Pane(session=name, target=f"{name}:0.0", pid=999, command="claude")
    prompt = detect((PANES / "blocked" / fixture).read_text())
    return Blocked(pane=pane, prompt=prompt, **kw)


def test_a_non_claude_pane_is_never_examined() -> None:
    """The forged-pane defence, at the layer that actually implements it."""
    assert not tmuxen.Pane("s", "s:0.0", 1, "sleep").is_claude
    assert not tmuxen.Pane("s", "s:0.0", 1, "cat").is_claude
    assert tmuxen.Pane("s", "s:0.0", 1, "claude").is_claude


def test_grace_period_holds_a_fresh_prompt_back(tmp_path, monkeypatch) -> None:
    """A human at the keyboard answers in seconds; that is not an incident."""
    now = time.time()
    blocked = _blocked(since=now - 5)
    monkeypatch.setattr(permwatch, "survey", lambda **kw: [blocked])
    ledger = Ledger(path=tmp_path / "l.json")
    sent = permwatch.sweep(ledger, {}, grace=45, dry_run=True)
    assert sent == []


def test_a_persistent_prompt_escalates(tmp_path, monkeypatch) -> None:
    blocked = _blocked(since=time.time() - 300)
    monkeypatch.setattr(permwatch, "survey", lambda **kw: [blocked])
    ledger = Ledger(path=tmp_path / "l.json")
    sent = permwatch.sweep(ledger, {}, grace=45, dry_run=True)
    assert len(sent) == 1


def test_the_same_prompt_is_not_escalated_twice(tmp_path, monkeypatch) -> None:
    blocked = _blocked(since=time.time() - 300)
    monkeypatch.setattr(permwatch, "survey", lambda **kw: [blocked])
    ledger = Ledger(path=tmp_path / "l.json")
    assert len(permwatch.sweep(ledger, {}, grace=45, dry_run=True)) == 1
    assert permwatch.sweep(ledger, {}, grace=45, dry_run=True) == []
    assert permwatch.sweep(ledger, {}, grace=45, dry_run=True) == []


def test_a_reminder_comes_back_after_the_long_interval(tmp_path, monkeypatch) -> None:
    blocked = _blocked(since=time.time() - 300)
    monkeypatch.setattr(permwatch, "survey", lambda **kw: [blocked])
    ledger = Ledger(path=tmp_path / "l.json")
    permwatch.sweep(ledger, {}, grace=45, dry_run=True)
    ledger.sent[blocked.key]["at"] -= 3700  # an hour passes
    assert len(permwatch.sweep(ledger, {}, grace=45, remind_after=3600, dry_run=True)) == 1


def test_reminders_can_be_switched_off(tmp_path, monkeypatch) -> None:
    blocked = _blocked(since=time.time() - 300)
    monkeypatch.setattr(permwatch, "survey", lambda **kw: [blocked])
    ledger = Ledger(path=tmp_path / "l.json")
    permwatch.sweep(ledger, {}, grace=45, remind_after=0, dry_run=True)
    ledger.sent[blocked.key]["at"] -= 86400
    assert permwatch.sweep(ledger, {}, grace=45, remind_after=0, dry_run=True) == []


def test_reminders_stop_rather_than_nag_forever(tmp_path, monkeypatch) -> None:
    blocked = _blocked(since=time.time() - 300)
    monkeypatch.setattr(permwatch, "survey", lambda **kw: [blocked])
    ledger = Ledger(path=tmp_path / "l.json")
    for _ in range(permwatch.MAX_REMINDERS + 3):
        permwatch.sweep(ledger, {}, grace=45, remind_after=3600, dry_run=True)
        ledger.sent[blocked.key]["at"] -= 3700
    assert ledger.sent[blocked.key]["count"] <= permwatch.MAX_REMINDERS + 1


def test_an_answered_prompt_is_forgotten(tmp_path, monkeypatch) -> None:
    """The same prompt later is a new incident, not one with a stale clock."""
    blocked = _blocked(since=time.time() - 300)
    ledger = Ledger(path=tmp_path / "l.json")
    monkeypatch.setattr(permwatch, "survey", lambda **kw: [blocked])
    permwatch.sweep(ledger, {}, grace=45, dry_run=True)
    assert blocked.key in ledger.sent
    monkeypatch.setattr(permwatch, "survey", lambda **kw: [])
    permwatch.sweep(ledger, {}, grace=45, dry_run=True)
    assert ledger.sent == {}


def test_the_ledger_survives_a_restart(tmp_path, monkeypatch) -> None:
    """Restarting the unit must not re-announce everything on the box."""
    blocked = _blocked(since=time.time() - 300)
    monkeypatch.setattr(permwatch, "survey", lambda **kw: [blocked])
    first = Ledger(path=tmp_path / "l.json")
    permwatch.sweep(first, {}, grace=45, dry_run=True)
    second = Ledger(path=tmp_path / "l.json")
    assert blocked.key in second.sent
    assert permwatch.sweep(second, {}, grace=45, dry_run=True) == []


def test_a_sweep_survives_one_broken_pane(tmp_path, monkeypatch) -> None:
    """Dying on one pane stops the watch on every other pane."""

    def boom(**kw):
        raise RuntimeError("tmux went away")

    monkeypatch.setattr(permwatch, "survey", boom)
    assert permwatch.run(once=True, dry_run=True) == 0


# ---- the report -----------------------------------------------------------


def test_the_report_carries_what_the_operator_had_to_dig_for() -> None:
    blocked = _blocked(since=time.time() - 600)
    report = blocked.report()
    assert "Do you want to proceed?" in report
    assert "pw-test" in report
    assert "tmux attach -t pw-test" in report
    assert "10 min" in report
    assert "fixtureC/.git" in report  # the triggering command, from the pane context


def test_an_unregistered_agent_still_gets_reported() -> None:
    """The folder-trust case has no agent, no session and no transcript.

    It is also the case most in need of escalating: nothing else on the box can
    see it at all, so falling back to a pid is not a degradation, it is the
    entire value.
    """
    blocked = _blocked(fixture="folder-trust.txt", since=time.time() - 120)
    report = blocked.report()
    assert "pid 999" in report
    assert "trust" in report.lower()


def test_a_blocked_operator_is_not_told_about_itself(monkeypatch) -> None:
    """The operator is an agent in a pane and can be stuck behind its own prompt.

    Injecting the news into the session that cannot read its inbox is the
    original failure with an extra step, so this case skips straight past it.
    """
    from hotline.agents import Agent

    op = Agent(session_id="sid-op", name="hotline-80", task="operator", authority="sys-admin")
    blocked = _blocked(since=time.time() - 300, agent=op)
    monkeypatch.setattr(permwatch, "operator", lambda registry=None: op)
    routed: list[list[str]] = []
    monkeypatch.setattr(permwatch, "_run", lambda argv, text, **kw: (routed.append(argv), (True, ""))[1])
    where = permwatch.notify(blocked)
    assert "Bogdan" in where
    assert "the operator is the blocked agent" in where
    assert not any("--to" in argv for argv in routed), "must not inject into the stuck operator"
