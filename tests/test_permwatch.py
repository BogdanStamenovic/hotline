"""The watcher's behaviour around the detector: gates, dedupe, and safety.

The safety tests here are the important ones. A watcher that can answer a
permission prompt has removed the guard those prompts exist to be, so "it does
not do that" is asserted against the source rather than trusted.
"""

from __future__ import annotations

import time
import tokenize
from pathlib import Path

import pytest

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


# ---- where a delegated prompt's command actually lives ---------------------


def _transcripts(tmp_path, parent_records, subagent_records):
    """Lay out a session transcript and a subagent file the way the CLI does.

    The shape is copied from a live observation on 2026-09-22 rather than
    imagined: a subagent was driven into a real Bash permission prompt, and the
    parent held 1 tool_use with 1 result and nothing unmatched while
    `<session>/subagents/agent-*.jsonl` held the real command, unmatched.
    """
    import json as _json

    project = tmp_path / "-some-project"
    project.mkdir(parents=True)
    session_id = "sid-parent"
    (project / f"{session_id}.jsonl").write_text(
        "\n".join(_json.dumps(r) for r in parent_records)
    )
    subagents = project / session_id / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-abc.jsonl").write_text(
        "\n".join(_json.dumps(r) for r in subagent_records)
    )
    return session_id


def _use(uid, name, payload, ts):
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {"content": [{"type": "tool_use", "id": uid, "name": name, "input": payload}]},
    }


def _result(uid, ts):
    return {
        "type": "user",
        "timestamp": ts,
        "message": {"content": [{"type": "tool_result", "tool_use_id": uid}]},
    }


def test_a_subagents_command_is_found(tmp_path, monkeypatch) -> None:
    """The prompt renders in the parent's pane; the command is in another file."""
    from hotline import config, transcript

    session_id = _transcripts(
        tmp_path,
        parent_records=[
            _use("t1", "Task", {"prompt": "go and do it"}, "2026-09-22T20:00:00.000Z"),
            _result("t1", "2026-09-22T20:00:05.000Z"),
        ],
        subagent_records=[
            {
                "type": "assistant",
                "isSidechain": True,
                "timestamp": "2026-09-22T20:00:10.000Z",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t2",
                            "name": "Bash",
                            "input": {"command": "echo hello > /tmp/marker"},
                        }
                    ]
                },
            }
        ],
    )
    monkeypatch.setattr(config, "projects_dir", lambda: tmp_path)
    monkeypatch.setattr(transcript, "projects_dir", lambda: tmp_path)

    found = transcript.dangling_tool_use(session_id)
    assert found is not None, "a delegated prompt must not report no command at all"
    assert found.name == "Bash"
    assert "echo hello" in found.summarise()
    assert found.from_subagent


def test_a_completed_parent_call_is_never_reported_as_the_trigger(
    tmp_path, monkeypatch
) -> None:
    """The failure that made a real operator deny a real rm on bad evidence.

    In the 2026-09-22 incident the parent's last call was a harmless
    `cat > msg-lifecycle-c2.txt` that had already completed. Reporting the last
    tool_use rather than the last UNMATCHED one would have named that as the
    thing awaiting permission -- confidently, and wrongly.
    """
    from hotline import config, transcript

    session_id = _transcripts(
        tmp_path,
        parent_records=[
            _use("t1", "Bash", {"command": "cat > msg-lifecycle-c2.txt"}, "2026-09-22T20:00:00Z"),
            _result("t1", "2026-09-22T20:00:01Z"),
        ],
        subagent_records=[],
    )
    monkeypatch.setattr(config, "projects_dir", lambda: tmp_path)
    monkeypatch.setattr(transcript, "projects_dir", lambda: tmp_path)

    found = transcript.dangling_tool_use(session_id)
    assert found is None, f"reported a completed call as the trigger: {found}"


def test_the_subagent_prompt_shape_is_in_the_corpus() -> None:
    """A suite cannot notice a path it does not know about."""
    subagent = PANES / "blocked" / "subagent-bash-permission.txt"
    assert subagent.exists()
    assert "from the general-purpose agent" in subagent.read_text()
    assert detect(subagent.read_text())


def test_unreachable_tmux_is_not_reported_as_nothing_blocked(monkeypatch) -> None:
    """An absence in a view that was never rendered is not a signal.

    This project's signature failure, and this module would have committed it:
    `panes()` returned an empty list both when tmux had no panes and when it
    could not be reached, so a watcher with no tmux at all reported the same
    cheerful nothing as a healthy box.
    """
    monkeypatch.setattr(tmuxen, "panes", lambda: None)
    with pytest.raises(permwatch.TmuxUnreachable):
        permwatch.survey()


def test_a_pass_that_could_not_look_does_not_clear_the_ledger(tmp_path, monkeypatch) -> None:
    """Forgetting on a blind pass would re-announce everything on the next one."""
    blocked = _blocked(since=time.time() - 300)
    ledger = Ledger(path=tmp_path / "l.json")
    monkeypatch.setattr(permwatch, "survey", lambda **kw: [blocked])
    permwatch.sweep(ledger, {}, grace=45, dry_run=True)
    assert blocked.key in ledger.sent

    def blind(**kw):
        raise permwatch.TmuxUnreachable("no server")

    monkeypatch.setattr(permwatch, "survey", blind)
    with pytest.raises(permwatch.TmuxUnreachable):
        permwatch.sweep(ledger, {}, grace=45, dry_run=True)
    assert blocked.key in ledger.sent, "a blind pass must not forget what it could not see"


def test_the_loop_survives_an_unreachable_tmux() -> None:
    assert permwatch.run(once=True, dry_run=True) == 0


def test_a_held_inbound_setting_is_surfaced(tmp_path, monkeypatch) -> None:
    """`--no-wait` exits 0 on handover, and handover is not delivery.

    Claude Code holds a peer message pending UI approval unless
    crossSessionInbound is "accept". The escalation then never reaches the
    operator's transcript while permwatcher logs a successful send.
    """
    monkeypatch.setattr(permwatch.Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / ".claude").mkdir()
    settings = tmp_path / ".claude" / "settings.json"

    settings.write_text('{"crossSessionInbound": "accept"}')
    assert permwatch.inbound_warning() == ""

    settings.write_text('{"crossSessionInbound": "ask"}')
    assert "may hold escalations" in permwatch.inbound_warning()

    settings.write_text("{}")
    assert "None" in permwatch.inbound_warning()

    settings.write_text("not json at all")
    assert "cannot tell" in permwatch.inbound_warning()


def test_one_sick_pane_does_not_blind_the_whole_pass(monkeypatch) -> None:
    """A capture that raises used to discard every other pane's real block.

    tmux returns panes in a stable order, so a persistently sick pane would
    hide every session listed after it for as long as it existed -- the exact
    failure `run()`'s catch-all comment says is worse than the one being
    reported.
    """
    import subprocess as sp

    good = tmuxen.Pane("healthy", "healthy:0.0", 2, "claude")
    bad = tmuxen.Pane("sick", "sick:0.0", 1, "claude")
    blocked_text = (PANES / "blocked" / "bash-permission.txt").read_text()

    def capture(target, lines=60):
        if target == "sick:0.0":
            raise sp.TimeoutExpired("tmux capture-pane", 15)
        return blocked_text

    monkeypatch.setattr(tmuxen, "panes", lambda: [bad, good])
    monkeypatch.setattr(tmuxen, "capture", capture)
    monkeypatch.setattr(permwatch, "discover", lambda **kw: [])
    found = permwatch.survey()
    assert [b.pane.session for b in found] == ["healthy"]


def test_an_unreadable_pane_holds_the_escalation(monkeypatch) -> None:
    """Held is the safe direction: retried next pass, versus crying wolf."""
    import subprocess as sp

    def boom(target, lines=60):
        raise sp.TimeoutExpired("tmux capture-pane", 15)

    monkeypatch.setattr(tmuxen, "capture", boom)
    assert permwatch.still_blocked(_blocked()) is False


def test_the_operators_own_untracked_pane_is_recognised(monkeypatch) -> None:
    """The folder-trust case has no agent record, so session_id cannot match.

    If that pane is the operator's, the guard has to notice via tmux instead.
    """
    from hotline.agents import Agent

    op = Agent(session_id="sid-op", name="hotline-80", task="operator", authority="sys-admin")
    blocked = _blocked(name="op-pane", fixture="folder-trust.txt", since=time.time() - 300)
    assert blocked.agent is None, "the folder-trust case has no registry record"

    class FakeSession:
        session_id = "sid-op"
        tmux_session = "op-pane"

    monkeypatch.setattr(permwatch, "operator", lambda registry=None: op)
    monkeypatch.setattr(permwatch, "discover", lambda **kw: [FakeSession()])
    routed: list[list[str]] = []
    monkeypatch.setattr(
        permwatch, "_run", lambda argv, text, **kw: (routed.append(argv), (True, ""))[1]
    )
    where = permwatch.notify(blocked)
    assert "the operator is the blocked agent" in where
    assert not any("--to" in argv for argv in routed)


def test_elapsed_time_survives_a_restart(tmp_path, monkeypatch) -> None:
    """A reminder after a restart must not say "blocked: 0 min" about hours."""
    blocked = _blocked(since=time.time() - 7200)
    monkeypatch.setattr(permwatch, "survey", lambda **kw: [blocked])
    ledger = Ledger(path=tmp_path / "l.json")
    permwatch.sweep(ledger, {}, grace=45, dry_run=True)
    restarted = Ledger(path=tmp_path / "l.json")
    seeded = restarted.first_seen()
    assert blocked.key in seeded
    assert time.time() - seeded[blocked.key] > 7000
