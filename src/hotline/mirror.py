"""Mirror a deliberate message to the phone as well as to Discord.

He asked for both to work: a message an agent chose to send him should land in
Discord *and* in the hotline-ios app, because those are the two places he
actually reads.

**Three rules this module exists to keep.**

1. *Best effort, never blocking.* Discord is the path that already works. If
   the phone's daemon is down, slow, or not installed, the Discord send must
   still happen exactly as before. Every failure here is swallowed.

2. *Silence is the danger, so failures are counted.* A mirror that fails
   quietly lets the app drift out of step with Discord with nobody able to see
   it. The counters live in a state file rather than in memory because the
   pager runs in short-lived CLI processes -- a counter in the process that
   failed dies with it, and `hotline`'s own `/health` is in a different process
   entirely. `/health` reads this file.

3. *Loopback only, so no credential is needed.* `hotline-ios` allows
   127.0.0.1 unconditionally, precisely so a local tool does not have to hold
   its API key. Nothing here reads or transmits a secret.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:8789"
TIMEOUT = 2.0


def _state_path() -> Path:
    root = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(root) / "hotline" / "mirror.json"


def read_state() -> dict[str, object]:
    """What `/health` reports. Never raises: a missing file means nothing has
    been mirrored yet, which is not a fault."""
    try:
        return json.loads(_state_path().read_text())
    except Exception:
        return {"delivered": 0, "failed": 0, "failing": 0}


def _record(ok: bool, error: str = "") -> None:
    try:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        state = read_state()
        key = "delivered" if ok else "failed"
        state[key] = int(state.get(key, 0)) + 1
        if ok:
            # **`failing` is "not working now", not "once failed".** The first
            # version counted cumulatively, so a single transient refusal --
            # the daemon restarting, a two-second timeout on a busy box -- left
            # `mirror_degraded` true for good. A health field that latches on
            # forever stops meaning anything, which is the same defect as one
            # that reads true while broken, just pointing the other way.
            state["failing"] = 0
        else:
            state["failing"] = int(state.get("failing", 0)) + 1
            state["last_error"] = error[:200]
            state["last_failure_at"] = time.time()
        path.write_text(json.dumps(state))
    except Exception:
        # Bookkeeping must not become the thing that breaks a page.
        pass


def mirror_sent(agent: str, text: str, url: str | None = None) -> bool:
    """Post one deliberate message to the phone. True if it landed.

    The return value is for tests and for callers that want to log; no caller
    is expected to act on it, and none may raise on it.
    """
    agent, text = (agent or "").strip(), (text or "").strip()
    if not agent or not text:
        return False
    # **A kill switch that tests set, because the default target is real.**
    # The default URL is loopback, which on this box is his *live* daemon. The
    # first run of the suite after this module was wired in posted sixteen
    # fixture strings -- "question", "may I push?" -- straight into his phone,
    # because the pager's tests fake Discord and had no reason to fake a
    # collaborator that did not exist when they were written. Disabled is not
    # failed, so it is not counted: counting it would leave `mirror_degraded`
    # true for anyone who had simply turned it off.
    if os.environ.get("HOTLINE_MIRROR", "").strip().lower() in ("0", "off", "false", "no"):
        return False
    base = url or os.environ.get("HOTLINE_IOS_URL") or DEFAULT_URL
    payload = json.dumps({"agent": agent, "text": text}).encode()
    request = urllib.request.Request(
        f"{base}/api/v1/agents/sent",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            ok = 200 <= response.status < 300
        _record(ok, "" if ok else f"HTTP {response.status}")
        return ok
    except urllib.error.HTTPError as exc:
        _record(False, f"HTTP {exc.code}")
    except Exception as exc:
        _record(False, f"{type(exc).__name__}: {exc}")
    return False
