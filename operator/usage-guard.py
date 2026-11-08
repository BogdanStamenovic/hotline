#!/usr/bin/env python3
"""Stop every agent when the Claude weekly usage reaches a threshold, until it resets.

Bogdan 2026-11-08 (Discord 1569051228372209666): "keep an eye on the usage i need
you to stop all the agent at 95 till tomorrow".

Stops by killing the tmux session of every Claude pane except the operator's:
transcripts survive, so agents can be resumed, but whatever a pane was running dies.
While tripped it keeps polling and re-kills, because any message or wake task revives
an hl-* agent, and keepalive.timer is stopped so never-sleep agents stay down.
At the reset it restarts keepalive.timer, tells the operator, and exits.

Not covered: Claude outside tmux (his remote sessions) and the call agent's API use.
Lift it early: touch ~/.local/state/usage-guard/release
"""
import argparse, datetime as dt, json, os, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

HOME = Path.home()
STATE = HOME / ".local/state/usage-guard"
OPERATOR_TMUX = "hotline"
ENV = {**os.environ, "PATH": f"{HOME}/.claude/bin:{HOME}/.local/bin:/usr/local/bin:{os.environ.get('PATH', '/usr/bin:/bin')}"}


def usage():
    token = json.load(open(HOME / ".claude/.credentials.json"))["claudeAiOauth"]["accessToken"]
    req = urllib.request.Request(
        "https://api.anthropic.com/api/oauth/usage",
        headers={"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20", "User-Agent": "claude-code/usage-guard"},
    )
    week = json.load(urllib.request.urlopen(req, timeout=15))["seven_day"]
    return float(week["utilization"]), dt.datetime.fromisoformat(week["resets_at"])


def say(text):
    with tempfile.NamedTemporaryFile("w", suffix=".msg", delete=False) as f:
        f.write(text)
    r = subprocess.run(["hotline-say", "--agent", "hotline-80", "--file", f.name], env=ENV, capture_output=True, text=True)
    os.unlink(f.name)
    if r.returncode:
        print("hotline-say failed:", r.stderr, file=sys.stderr, flush=True)


def targets():
    out = subprocess.run(["tmux", "list-panes", "-a", "-F", "#{session_name} #{pane_pid}"], capture_output=True, text=True).stdout
    found = set()
    for line in out.splitlines():
        session, pid = line.split()
        if session == OPERATOR_TMUX:
            continue
        args = subprocess.run(["ps", "-o", "args=", "-p", pid], capture_output=True, text=True).stdout
        if "claude-code/bin/claude" in args:
            found.add(session)
    return sorted(found)


def stop(dry):
    names = targets()
    if not dry:
        subprocess.run(["systemctl", "--user", "stop", "keepalive.timer"])
        for s in names:
            subprocess.run(["tmux", "kill-session", "-t", s])
    return names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=95)
    ap.add_argument("--warn", type=float, default=90)
    ap.add_argument("--every", type=int, default=120)
    ap.add_argument("--dry-run", action="store_true", help="one pass: report, kill nothing")
    ap.add_argument("--quiet", action="store_true", help="no start announcement")
    a = ap.parse_args()
    STATE.mkdir(parents=True, exist_ok=True)
    release = STATE / "release"
    release.unlink(missing_ok=True)

    if a.dry_run:
        pct, reset = usage()
        print(f"usage {pct}% resets {reset}; trip={pct >= a.threshold}; would stop: {targets()}")
        return

    pct, reset = usage()
    # resets_at rolls forward a week once this one passes, so the end is fixed here.
    first_reset = reset
    if not a.quiet:
        say(f"Usage guard is on: the weekly limit is at {pct:.0f}% and it checks every {a.every // 60} min. At {a.threshold:.0f}% it stops every agent until the reset at {reset.astimezone():%H:%M %a}.")
    tripped = warned = False
    fails = 0
    while True:
        try:
            pct, reset = usage()
            fails = 0
        except Exception as exc:
            fails += 1
            print("usage read failed:", exc, file=sys.stderr, flush=True)
            if fails == 3:
                say(f"Usage guard can't read usage ({exc}). It stays running, but it can't see the limit until this clears.")
            time.sleep(a.every)
            continue
        print(f"{dt.datetime.now():%H:%M:%S} {pct}% tripped={tripped}", flush=True)
        if tripped and (dt.datetime.now(dt.timezone.utc) >= until or pct < a.threshold - 20):
            break
        if release.exists():
            say("Usage guard lifted by hand.")
            break
        if not tripped and pct >= a.warn and not warned:
            warned = True
            say(f"Usage is at {pct:.0f}%. Agents get stopped at {a.threshold:.0f}%.")
        if not tripped and pct >= a.threshold:
            tripped, until = True, reset
            names = stop(False)
            (STATE / "stopped.json").write_text(json.dumps({"at": time.time(), "stopped": names}))
            say(f"Usage hit {pct:.0f}%, so I stopped all agents until {until.astimezone():%H:%M %a}: {', '.join(names) or 'none were running'}. Their conversations are saved, but anything they were running died. Your calls still work and still count toward the limit.")
        elif tripped:
            again = stop(False)
            if again:
                print("re-stopped", again, flush=True)
        if not tripped and dt.datetime.now(dt.timezone.utc) >= first_reset:
            say("Weekly usage reset without reaching the limit. Usage guard is off.")
            return
        time.sleep(a.every)
    subprocess.run(["systemctl", "--user", "start", "keepalive.timer"])
    subprocess.run(["hotline", "--to", "hotline-80", "--no-wait", "usage guard: reset reached, keepalive restarted. Revive the agents listed in ~/.local/state/usage-guard/stopped.json that keepalive does not cover, and tell Bogdan."], env=ENV)


if __name__ == "__main__":
    main()
