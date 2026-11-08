#!/usr/bin/env python3
"""At a weekly Claude usage threshold, interrupt active agents and tell them to wind down.

Bogdan 2026-11-08: stop at 95 (Discord 1569051228372209666), then refined to no full
stop (1569056818888445953): "it just needs to interrupt all the active agents and tell
them wind down for the night, push, that's it. And leave them like that so I can
override them with a message".

So this fires once and exits. It kills nothing and leaves keepalive alone. Idle agents
get no message, because waking them would itself spend usage. An interrupted agent
stays reachable, and any message restarts it.
"""
import argparse, datetime as dt, json, os, re, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

HOME = Path.home()
OPERATOR = "hotline-80"
ACTIVE = {"busy", "shell"}
GUILD = next(l.split("=", 1)[1].strip().strip('"') for l in open(HOME / "data/hotline/.env") if l.startswith("DISCORD_GUILD_ID="))
WARRANT = f"https://discord.com/channels/{GUILD}/1541610683240554527/1569056818888445953"
WIND_DOWN = (
    "From Bogdan, via the usage guard: the weekly Claude limit hit {pct:.0f}%. Wind down for the night: "
    "commit and push what you have, and that's it. Don't start anything new, don't spawn agents, and stop any "
    "background subagents. Then stay idle. He may override this with a message, and if he does, follow him."
)
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
    r = subprocess.run(["hotline-say", "--agent", OPERATOR, "--file", f.name], env=ENV, capture_output=True, text=True)
    os.unlink(f.name)
    if r.returncode:
        print("hotline-say failed:", r.stderr, file=sys.stderr, flush=True)


def active():
    """[(name, pid)] of agents doing something, read from hotline --list."""
    out = subprocess.run(["hotline", "--list"], env=ENV, capture_output=True, text=True).stdout
    found = []
    for line in out.splitlines():
        m = re.match(r"(\S+) .*\(pid (\d+), [^)]*\)\s+(\w+)", line)
        if m and m[1] != OPERATOR and m[3] in ACTIVE:
            found.append((m[1], int(m[2])))
    return found


def pane_of(pid):
    out = subprocess.run(["tmux", "list-panes", "-a", "-F", "#{pane_pid} #{pane_id}"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        p, pane = line.split()
        if int(p) == pid:
            return pane
    return None


def wind_down(pct):
    done, failed = [], []
    for name, pid in active():
        pane = pane_of(pid)
        if pane:
            subprocess.run(["tmux", "send-keys", "-t", pane, "Escape"])
            time.sleep(1)
        r = subprocess.run(
            ["hotline", "--to", name, "--no-wait", "--warrant", WARRANT, WIND_DOWN.format(pct=pct)],
            env=ENV, capture_output=True, text=True,
        )
        (done if r.returncode == 0 and pane else failed).append(name)
        print(name, pane, r.returncode, r.stderr[:200], flush=True)
    return done, failed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=95)
    ap.add_argument("--warn", type=float, default=90)
    ap.add_argument("--every", type=int, default=120)
    ap.add_argument("--dry-run", action="store_true", help="one pass: report, touch nothing")
    a = ap.parse_args()

    pct, reset = usage()
    if a.dry_run:
        print(f"usage {pct}% resets {reset}; trip={pct >= a.threshold}; would wind down: {active()}")
        return
    # resets_at rolls forward a week once this one passes, so the end is fixed here.
    first_reset = reset
    warned = False
    fails = 0
    while dt.datetime.now(dt.timezone.utc) < first_reset:
        try:
            pct, _ = usage()
            fails = 0
        except Exception as exc:
            fails += 1
            print("usage read failed:", exc, file=sys.stderr, flush=True)
            if fails == 3:
                say(f"Usage guard can't read usage ({exc}). It stays running, but it can't see the limit until this clears.")
            time.sleep(a.every)
            continue
        print(f"{dt.datetime.now():%H:%M:%S} {pct}%", flush=True)
        if pct >= a.warn and not warned:
            warned = True
            say(f"Usage is at {pct:.0f}%. Active agents get told to wind down at {a.threshold:.0f}%.")
        if pct >= a.threshold:
            done, failed = wind_down(pct)
            note = f" Couldn't fully reach: {', '.join(failed)}." if failed else ""
            say(f"Usage hit {pct:.0f}%. I interrupted the active agents and told them to push and wind down: {', '.join(done) or 'none were active'}.{note} Nothing was killed, and a message from you overrides it. The limit resets {first_reset.astimezone():%H:%M %a}.")
            return
        time.sleep(a.every)
    say("Weekly usage reset without reaching the threshold. Usage guard is off.")


if __name__ == "__main__":
    main()
