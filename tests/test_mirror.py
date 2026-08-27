"""The send-to-the-phone mirror.

Its whole contract is that it cannot hurt the path that already works, and that
when it does fail it says so somewhere a person can look.
"""
from __future__ import annotations

import json

import pytest

from hotline import mirror


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    # `conftest.isolated_state` turns the mirror off for the whole suite so no
    # test can post to his live phone. This file is the one that has to
    # exercise it, so it opts back in explicitly -- and every URL below points
    # at a dead port or a fake, never at the default.
    monkeypatch.setenv("HOTLINE_MIRROR", "1")
    return tmp_path


def test_nothing_mirrored_yet_is_not_a_fault():
    assert mirror.read_state() == {"delivered": 0, "failed": 0}


def test_a_failed_mirror_never_raises_and_is_counted():
    """The Discord send is the path that works. If this can throw, a phone that
    is merely switched off takes the page down with it."""
    ok = mirror.mirror_sent("data-1e", "anything", url="http://127.0.0.1:1")
    assert ok is False

    state = mirror.read_state()
    assert state["failed"] == 1
    assert state["delivered"] == 0
    assert state.get("last_error"), "a failure with no error recorded is invisible"
    assert state.get("last_failure_at")


def test_failures_accumulate_rather_than_overwrite():
    for _ in range(3):
        mirror.mirror_sent("data-1e", "anything", url="http://127.0.0.1:1")
    assert mirror.read_state()["failed"] == 3


def test_an_empty_message_is_not_sent_and_is_not_a_failure():
    """Nothing to mirror is not the same as a mirror that broke, and counting
    it as one would leave `mirror_degraded` true forever."""
    assert mirror.mirror_sent("data-1e", "   ") is False
    assert mirror.mirror_sent("", "something") is False
    assert mirror.read_state()["failed"] == 0


def test_a_delivered_mirror_is_counted(monkeypatch):
    posted: dict[str, object] = {}

    class Response:
        status = 200

        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(request, timeout=None):
        posted["url"] = request.full_url
        posted["body"] = json.loads(request.data)
        return Response()

    monkeypatch.setattr(mirror.urllib.request, "urlopen", fake_urlopen)
    assert mirror.mirror_sent("data-1e", "Deploy is done.", url="http://phone") is True
    assert posted["url"] == "http://phone/api/v1/agents/sent"
    assert posted["body"] == {"agent": "data-1e", "text": "Deploy is done."}

    state = mirror.read_state()
    assert state["delivered"] == 1 and state["failed"] == 0


def test_the_kill_switch_is_not_a_failure(monkeypatch):
    """Turning the mirror off must not look like a mirror that broke, or
    `mirror_degraded` is true for everyone who disabled it on purpose."""
    monkeypatch.setenv("HOTLINE_MIRROR", "0")
    assert mirror.mirror_sent("data-1e", "anything", url="http://127.0.0.1:1") is False
    assert mirror.read_state() == {"delivered": 0, "failed": 0}
