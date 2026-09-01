"""The AskUserQuestion -> Discord bridge (hotline/ask.py).

The failure it removes: a headless agent calls AskUserQuestion, the interactive
picker opens, nobody is at the terminal, and the session hangs forever with the
injected reply stuck inside the menu. This checks that the hook stays silent for
his own sessions, relays to the right channel for a spawned agent, feeds his
answer back as a deny, and hands the agent a fallback when he never replies.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hotline import ask
from hotline.agents import Registry


class FakePager:
    def __init__(self, replies_by_poll):
        # A list of reply-lists, returned one per replies_since() call.
        self._replies = list(replies_by_poll)
        self.sent = []
        self.calls = 0

    def send(self, channel_id, content):
        self.sent.append((channel_id, content))
        return "anchor-1"

    def replies_since(self, channel_id, after):
        i = min(self.calls, len(self._replies) - 1)
        self.calls += 1
        return self._replies[i] if self._replies else []


@pytest.fixture
def spawned(monkeypatch):
    monkeypatch.setenv("HOTLINE_SPAWNED", "1")


@pytest.fixture
def registry(tmp_path: Path) -> Registry:
    reg = Registry(path=tmp_path / "agents.json")
    agent = reg.declare("sid-af", "data-af", "build wd_gen")
    agent.channel_id = 424242
    return reg


def _payload(session_id="sid-af"):
    return {
        "tool_name": "AskUserQuestion",
        "session_id": session_id,
        "tool_input": {
            "questions": [
                {
                    "question": "Public or private?",
                    "header": "Repo visibility",
                    "options": [
                        {"label": "public", "description": "anyone can see it"},
                        {"label": "private", "description": "invite only"},
                    ],
                    "multiSelect": False,
                }
            ]
        },
    }


def test_his_own_session_is_never_intercepted(registry, monkeypatch):
    # No HOTLINE_SPAWNED -> he is at the keyboard; the picker must render normally.
    monkeypatch.delenv("HOTLINE_SPAWNED", raising=False)
    pager = FakePager([["private"]])
    assert ask.decide(_payload(), registry=registry, pager=pager, wait=1) is None
    assert pager.sent == []  # nothing relayed


def test_a_non_ask_tool_is_ignored(spawned, registry):
    pager = FakePager([["x"]])
    payload = _payload()
    payload["tool_name"] = "Bash"
    assert ask.decide(payload, registry=registry, pager=pager, wait=1) is None
    assert pager.sent == []


def test_an_unregistered_session_is_left_alone(spawned, registry):
    pager = FakePager([["private"]])
    out = ask.decide(_payload("sid-unknown"), registry=registry, pager=pager, wait=1)
    assert out is None
    assert pager.sent == []


def test_his_answer_comes_back_as_a_deny_carrying_it(spawned, registry):
    pager = FakePager([[], ["make it private"]])  # empty poll, then his reply
    out = ask.decide(_payload(), registry=registry, pager=pager, wait=10, sleep=lambda s: None)
    assert out is not None
    hso = out["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny"
    assert "make it private" in hso["permissionDecisionReason"]
    # relayed to the agent's channel, and the question text is in the message
    assert pager.sent and pager.sent[0][0] == "424242"
    assert "Public or private?" in pager.sent[0][1]
    assert "private" in pager.sent[0][1]  # options listed


def test_no_reply_hands_the_agent_a_fallback_not_a_hang(spawned, registry):
    # Clock runs out with no reply: still a deny, but one that tells the agent to
    # take the safe option rather than sit on the picker.
    ticks = iter([0.0, 0.0, 100.0, 200.0])
    pager = FakePager([[]])
    out = ask.decide(
        _payload(), registry=registry, pager=pager, wait=1,
        sleep=lambda s: None, now=lambda: next(ticks),
    )
    assert out is not None
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "did not reply" in reason and "safest" in reason


def test_format_lists_every_option():
    msg = ask.format_questions(
        [{"question": "Pick one", "header": "H",
          "options": [{"label": "a", "description": "the a"}, {"label": "b", "description": ""}],
          "multiSelect": True}],
        "data-af",
    )
    assert "data-af is asking you a question" in msg
    assert "Pick one" in msg and "the a" in msg and "**b**" in msg
    assert "pick more than one" in msg
