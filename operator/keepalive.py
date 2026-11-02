#!/home/bodas/data/hotline/.venv/bin/python
"""Revive agents Bogdan wants kept up, if they died (OOM, crash) or were put to sleep.

Watched: every name on the never-sleep list (`hotline --sleep never`), and only
those -- his rule 11-02, "revive only the never sleep agents". An agent counts as
down when it is registered, not --done, and its session is not live; `hotline --to` then revives it (resume, or from its handoff).
Run by keepalive.timer every 5 minutes. Bogdan's ask (11-01): "keep in mind them
and wake them up if something happens" after the stizem team was OOM-killed.
"""
import subprocess
import sys

sys.path.insert(0, "/home/bodas/data/hotline/src")
from hotline import sleep  # noqa: E402
from hotline.agents import Registry  # noqa: E402
from hotline.ccsocks import discover  # noqa: E402

HOTLINE = "/home/bodas/.claude/bin/hotline"


def main() -> int:
    reg = Registry()
    names = sleep.never_sleep()
    live = {s.session_id for s in discover()}
    revived = []
    for name in sorted(names):
        agent = reg.by_name(name)
        if agent is None or not sleep.sleeping(agent, live):
            continue
        msg = (f"hotline-80 keepalive: you ({name}) were down (crash, OOM kill, or sleep) and have been "
               "resumed automatically, because Bogdan wants you kept up. Check what you were doing "
               "(git status, running jobs) and carry on.")
        r = subprocess.run([HOTLINE, "--to", name, "--no-wait", msg], capture_output=True, text=True, timeout=120)
        revived.append(f"{name}: exit {r.returncode}")
    if revived:
        note = "keepalive revived: " + "; ".join(revived)
        print(note)
        subprocess.run([HOTLINE, "--to", "hotline-80", "--no-wait", note], capture_output=True, timeout=60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
