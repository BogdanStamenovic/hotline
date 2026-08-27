"""The guard for the sixteen rows that reached his phone.

Deliberately in its own file with **no local fixtures**. `test_mirror.py` has
to turn the mirror back on to exercise it, so the assertion cannot live there:
it would only ever see that file's own override and would pass no matter what
`conftest` did.
"""
from __future__ import annotations

import os


def test_the_mirror_is_off_for_the_whole_suite():
    """`mirror.mirror_sent` defaults to loopback, which on this machine is his
    LIVE hotline-ios daemon. The first suite run after the mirror was wired
    into `Pager.page` posted sixteen fixture strings into his real app. If the
    switch in `conftest.isolated_state` is ever dropped, this fails here rather
    than showing up later as junk in his transcript."""
    assert os.environ.get("HOTLINE_MIRROR") == "0"


def test_a_page_in_the_suite_writes_nothing_to_the_phone(monkeypatch):
    """Belt and braces: prove it at the call, not only at the env var."""
    from hotline import mirror

    called: list[tuple[str, str]] = []

    def fail(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("the suite opened a socket to the phone")

    monkeypatch.setattr(mirror.urllib.request, "urlopen", fail)
    assert mirror.mirror_sent("data-1e", "this must not leave the process") is False
    assert called == []
