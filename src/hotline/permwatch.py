"""The service that notices a blocked agent and tells somebody.

`permprompt.detect` reads one pane. This is everything around it: which panes to
look at, how long a prompt has to persist before it counts, who gets told, and
how not to tell them the same thing forever.

**It never answers a prompt and never writes to a pane.** Not "1", not Escape,
not a safe-list, not a configurable auto-approve. A watcher that answers
permission prompts has deleted the guard the prompts exist to be, and it would
do it on the strength of a terminal scrape -- the least trustworthy input in the
system. The only tmux verbs imported here are `panes` and `capture`, both
read-only; `tmuxen.send_command` and `tmuxen.interrupt` are deliberately not
imported, and `tests/test_permwatch.py` fails if that changes.

**Three gates before anything is sent**, because a watcher nobody trusts gets
switched off and then protects nothing:

  1. the pane's foreground process must be `claude`. A shell that has `cat`-ed a
     captured prompt produces bytes indistinguishable from a real one -- checked
     on 2026-09-22, not assumed -- and this is the only thing that separates
     them;
  2. the prompt must still be there `GRACE` seconds later. Somebody sitting at
     the keyboard answers in a few seconds, and that is not an incident;
  3. the pane is captured once more immediately before the message is composed.
     Between the grace period ending and the send there is a window in which the
     prompt gets answered, and an escalation about a prompt that is already
     resolved is exactly the noise that trains people to ignore the channel.

**One notification per distinct prompt, plus a slow reminder.** Keyed on the
prompt's fingerprint, so a spinner animating above it or somebody arrowing
between the options does not re-notify. The reminder is the arguable half and it
is deliberately on: the whole premise of this module is that the thing meant to
notice a stuck agent can itself be stuck, and a single fire-and-forget message
into a wedged operator's inbox reproduces the original failure exactly. One
repeat an hour is cheap; a lost-forever notification is not. `REMIND_AFTER = 0`
turns it off.

**Who gets told** is resolved at run time from the agent registry -- whichever
agent currently holds `sys-admin` -- rather than hardcoded, because that role
moves between sessions and a hardcoded name would silently stop existing.

**When the operator cannot be told**, the message goes to Bogdan's own channel
as text. That covers the operator being absent, being blocked itself, or the
injection failing. What this does NOT do is ring his phone: a build agent stuck
at 3am is not worth waking him for, and the decision about which blocks are is
his to make, not this module's to assume.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import tmuxen
from .agents import Agent, Registry
from .ccsocks import LiveSession, discover
from .config import state_dir
from .permprompt import Prompt, detect
from .transcript import PendingTool, dangling_tool_use

log = logging.getLogger(__name__)


class TmuxUnreachable(RuntimeError):
    """tmux could not be consulted, so this pass saw nothing and proves nothing."""

# How long a prompt must stand before it is anybody's problem. A human at the
# keyboard clears one in a couple of seconds; an agent never clears one at all.
GRACE = 45.0

# Poll interval. The existing precedent in this project is the watchdog's
# five-minute timer, which is right for "did the worker die" and wrong here: a
# blocked agent is burning wall-clock on a build somebody is waiting for, and
# five minutes of that is the thing being fixed. A pass costs one `list-panes`
# plus one `capture-pane` per claude pane -- microseconds of work against panes
# that are by definition doing nothing -- so the cheap end of the range is
# affordable. Measured on 2026-09-22 over 7 claude panes: 18 ms median per pass
# (min 17, max 19, n=7), so a 12s interval spends 0.15% of one core.
INTERVAL = 12.0

# Silence between repeats of the same unanswered prompt. See the module
# docstring for why this is not zero.
REMIND_AFTER = 3600.0

# A prompt nobody has answered after this long has outlived the reminder's
# usefulness; stop repeating rather than nag forever.
MAX_REMINDERS = 4


@dataclass(frozen=True)
class Blocked:
    """One agent, stuck on one prompt, with everything needed to act on it."""

    pane: tmuxen.Pane
    prompt: Prompt
    # The hotline agent behind the pane, when there is one. A session blocked at
    # the folder-trust prompt has written no descriptor and no registry record,
    # so this is legitimately None -- and that is the case most in need of
    # escalating, because nothing else on the box can see it at all.
    agent: Agent | None = None
    session: LiveSession | None = None
    pending: PendingTool | None = None
    # When this exact prompt was first seen on this pane.
    since: float = 0.0

    @property
    def key(self) -> str:
        """Dedupe identity: this prompt, on this pane."""
        return f"{self.pane.session}:{self.prompt.fingerprint}"

    @property
    def who(self) -> str:
        if self.agent:
            return self.agent.name
        if self.session:
            return self.session.name
        return f"pid {self.pane.pid}"

    def report(self, now: float | None = None) -> str:
        """The message a human or an operator actually receives.

        Written to answer the question the operator had to answer by hand on
        2026-09-22 -- *what command triggered this* -- without them having to go
        and look. The pane context and the pending tool call are both included
        because they fail in opposite directions: the pane has the guard's
        reason but wraps and truncates the command, and the transcript has the
        command verbatim but not the reason.
        """
        now = time.time() if now is None else now
        waited = max(0.0, now - (self.since or now))
        lines = [
            f"BLOCKED AGENT: {self.who} is waiting on a prompt it cannot answer.",
            "",
            f"  agent   : {self.who}"
            + (f" -- {self.agent.task}" if self.agent and self.agent.task else ""),
            f"  tmux    : {self.pane.session}   (attach: tmux attach -t {self.pane.session})",
            f"  pid     : {self.pane.pid}",
            f"  blocked : {waited / 60:.0f} min ({waited:.0f}s)",
            "",
            "THE PROMPT:",
        ]
        lines += [f"  {line}" for line in self.prompt.describe().splitlines()]
        lines += [f"  [{self.prompt.footer}]"]
        if self.pending:
            lines += [
                "",
                f"WHAT TRIGGERED IT ({self.pending.name} call, still unanswered"
                + (", raised by a SUBAGENT" if self.pending.from_subagent else "")
                + "):",
                f"  {self.pending.summarise()}",
            ]
        if self.prompt.context:
            lines += ["", "PANE ABOVE THE PROMPT:"]
            lines += [f"  {line}" for line in self.prompt.context.splitlines() if line.strip()]
        lines += [
            "",
            (
                "Nothing has been typed into that pane and nothing will be --"
                " permwatcher only ever looks. Answering it is yours."
            ),
        ]
        return "\n".join(lines)


# ---- who to tell -----------------------------------------------------------


def operator(registry: Registry | None = None) -> Agent | None:
    """Whoever currently holds sys-admin, or None.

    Resolved per pass rather than cached: the role moves when an operator is
    respawned and adopts the identity, and a cached answer would go on
    addressing a session that no longer exists.
    """
    registry = registry or Registry()
    working = [a for a in registry.privileged() if not a.done]
    if not working:
        return None
    # Newest declaration wins, which is the one that adopted the identity last.
    return max(working, key=lambda a: a.declared_at)


def _hotline_bin() -> str:
    """The `hotline` that belongs to this installation.

    Resolved next to the running interpreter first, because a user unit starts
    with a minimal PATH and `~/.claude/bin` is not on it -- an agent shell on
    this box reports hotline as "command not found" for exactly that reason.
    """
    sibling = Path(sys.executable).with_name("hotline")
    if sibling.exists():
        return str(sibling)
    return shutil.which("hotline") or "hotline"


def _run(argv: list[str], text: str, timeout: float = 60.0) -> tuple[bool, str]:
    """Hand `text` to a command as a single argv element.

    argv, never a shell string. A message composed into a double-quoted shell
    command has its backticks run as command substitution -- on 2026-09-19 an
    agent's status update mentioned a script path in backticks and the shell
    executed it, silently undoing a revert. Every message this module sends is
    a pane capture from an agent that was, by construction, in the middle of
    running commands, so it is the worst possible thing to interpolate into a
    shell. `subprocess.run` with a list and no `shell=True` cannot do that.
    """
    try:
        done = subprocess.run(
            [*argv, text], capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if done.returncode != 0:
        return False, (done.stderr or done.stdout or f"exit {done.returncode}").strip()
    return True, (done.stdout or "").strip()


def notify(blocked: Blocked, *, registry: Registry | None = None, dry_run: bool = False) -> str:
    """Send one escalation, and say where it went.

    Falls back from the operator to Bogdan's own channel rather than giving up,
    because the operator is a single point of failure for precisely this class
    of problem -- it is an agent in a tmux pane like any other and can be stuck
    behind its own permission prompt while holding the only copy of this news.
    """
    body = blocked.report()
    if dry_run:
        return "dry-run"

    target = operator(registry)
    reason = "no sys-admin agent is registered"

    if target is not None and blocked.agent is not None and blocked.agent.session_id == target.session_id:
        # The operator is the one that is stuck. Telling it about itself would
        # put the news in the inbox of the session that cannot read its inbox --
        # which is the original failure with an extra step.
        target, reason = None, "the operator is the blocked agent"

    if target is not None:
        ok, detail = _run([_hotline_bin(), "--to", target.name, "--no-wait"], body)
        if ok:
            return f"operator {target.name}"
        reason = f"operator {target.name} unreachable: {detail.splitlines()[0][:120]}"
        log.warning("could not reach the operator: %s", detail)

    say = shutil.which("hotline-say") or str(Path.home() / ".claude" / "bin" / "hotline-say")
    ok, detail = _run([say], f"{body}\n\n(sent here because {reason}.)")
    if ok:
        return f"Bogdan's channel -- {reason}"
    log.error("escalation failed entirely: %s", detail)
    return "NOWHERE -- both the operator and the Discord fallback failed"


def _self_name() -> str:
    """This watcher's own tmux session, so it never escalates about itself.

    Empty for the systemd unit, which has no pane and cannot see itself. It
    matters when a person or an agent runs `--status` from inside a pane: a
    watcher that reads panes on the box it is running on will eventually read
    its own, and its own pane is full of the prompts it has been printing. Same
    family as `pgrep -f` matching the shell that ran it.

    Derived from tmux rather than declared, because anything that has to be set
    by hand is a thing that will not be set by the one invocation that needed
    it. The environment variable stays as an override for tests.
    """
    declared = os.environ.get("HOTLINE_PERMWATCH_SELF")
    if declared is not None:
        return declared
    if not os.environ.get("TMUX"):
        return ""
    pane = os.environ.get("TMUX_PANE", "")
    try:
        found = subprocess.run(
            ["tmux", "display-message", "-p", *(("-t", pane) if pane else ()), "#{session_name}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return found.stdout.strip() if found.returncode == 0 else ""


# ---- looking ---------------------------------------------------------------


def survey(
    *,
    seen: dict[str, float] | None = None,
    now: float | None = None,
    skip: set[str] | None = None,
) -> list[Blocked]:
    """Every claude pane on the box that is sitting on an unanswered prompt.

    `seen` maps a Blocked.key to when that prompt was first observed and is
    updated in place, which is what makes the grace period work across passes
    without this function owning any state of its own.
    """
    now = time.time() if now is None else now
    seen = {} if seen is None else seen
    skip = skip or set()

    sessions = {s.tmux_session: s for s in discover(include_self=True, include_programmatic=True)}
    registry = Registry()
    by_session_id = {a.session_id: a for a in registry.agents.values()}

    visible = tmuxen.panes()
    if visible is None:
        # Nothing was looked at, so nothing can be concluded -- and in
        # particular no prompt may be forgotten on the strength of not having
        # been seen this pass.
        raise TmuxUnreachable("tmux could not be consulted")

    found: list[Blocked] = []
    live_keys: set[str] = set()
    for pane in visible:
        if not pane.is_claude or pane.session in skip:
            continue
        prompt = detect(tmuxen.capture(pane.target, lines=60))
        if not prompt:
            continue
        live = sessions.get(pane.session)
        agent = by_session_id.get(live.session_id) if live else None
        pending = dangling_tool_use(live.session_id) if live else None
        blocked = Blocked(pane=pane, prompt=prompt, agent=agent, session=live, pending=pending)
        # `since` is not known until the ledger of first sightings is consulted,
        # and the key needed to consult it does not depend on `since`.
        first = seen.setdefault(blocked.key, now)
        live_keys.add(blocked.key)
        found.append(replace(blocked, since=first))

    # Forget prompts that are gone, so the same prompt appearing again later is
    # a new incident rather than one inheriting an hours-old `since`.
    for stale in [k for k in seen if k not in live_keys]:
        seen.pop(stale, None)
    return found


def still_blocked(blocked: Blocked) -> bool:
    """Re-read the pane right now. Answered prompts must not escalate."""
    fresh = detect(tmuxen.capture(blocked.pane.target, lines=60))
    return bool(fresh) and fresh.fingerprint == blocked.prompt.fingerprint


# ---- remembering what has been said ----------------------------------------


def ledger_path() -> Path:
    return state_dir() / "permwatch.json"


@dataclass
class Ledger:
    """What has already been escalated, across restarts of the service.

    Durable rather than in-memory: restarting the unit must not re-announce
    every prompt currently on the box, which would turn a deploy into a burst of
    notifications about things the operator was already told about.
    """

    path: Path = field(default_factory=ledger_path)
    sent: dict[str, dict] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return
        if isinstance(raw, dict) and isinstance(raw.get("sent"), dict):
            self.sent = raw["sent"]

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"sent": self.sent}, indent=2))
            tmp.replace(self.path)
        except OSError as exc:
            log.warning("could not persist the permwatch ledger: %s", exc)

    def due(self, blocked: Blocked, *, now: float, remind_after: float = REMIND_AFTER) -> bool:
        record = self.sent.get(blocked.key)
        if record is None:
            return True
        if not remind_after:
            return False
        if record.get("count", 1) > MAX_REMINDERS:
            return False
        return now - record.get("at", 0.0) >= remind_after

    def record(self, blocked: Blocked, *, now: float, where: str) -> int:
        record = self.sent.setdefault(blocked.key, {"count": 0})
        record["count"] = record.get("count", 0) + 1
        record["at"] = now
        record["where"] = where
        return int(record["count"])

    def forget_absent(self, live: set[str]) -> None:
        for key in [k for k in self.sent if k not in live]:
            self.sent.pop(key, None)


# ---- the loop --------------------------------------------------------------


def sweep(
    ledger: Ledger,
    seen: dict[str, float],
    *,
    grace: float = GRACE,
    remind_after: float = REMIND_AFTER,
    dry_run: bool = False,
    skip: set[str] | None = None,
) -> list[tuple[Blocked, str]]:
    """One pass: look, decide, escalate what is due. Returns what was sent."""
    now = time.time()
    # Deliberately NOT caught here: `run()` logs it and tries again next pass.
    # Swallowing it would let `forget_absent` clear the ledger on a pass that
    # saw nothing because it could not look, and the next successful pass would
    # then re-announce every prompt on the box as though it were new.
    blocked_now = survey(seen=seen, now=now, skip=skip)
    ledger.forget_absent({b.key for b in blocked_now})

    sent: list[tuple[Blocked, str]] = []
    for blocked in blocked_now:
        waited = now - blocked.since
        if waited < grace:
            log.debug("%s blocked %.0fs, under the %.0fs grace", blocked.who, waited, grace)
            continue
        if not ledger.due(blocked, now=now, remind_after=remind_after):
            continue
        # Gate 3. The grace period and the ledger lookup both took time; the
        # prompt may have been answered in it.
        if not dry_run and not still_blocked(blocked):
            log.info("%s answered its prompt before the escalation went out", blocked.who)
            continue
        where = notify(blocked, dry_run=dry_run)
        count = ledger.record(blocked, now=now, where=where)
        log.warning(
            "escalated %s (%s) to %s%s",
            blocked.who,
            blocked.pane.session,
            where,
            f" [reminder {count - 1}]" if count > 1 else "",
        )
        sent.append((blocked, where))
    ledger.save()
    return sent


def run(
    *,
    interval: float = INTERVAL,
    grace: float = GRACE,
    remind_after: float = REMIND_AFTER,
    dry_run: bool = False,
    once: bool = False,
    skip: set[str] | None = None,
) -> int:
    ledger = Ledger()
    seen: dict[str, float] = {}
    log.info(
        "permwatcher up: every %.0fs, grace %.0fs, reminders %s",
        interval,
        grace,
        f"every {remind_after / 60:.0f} min" if remind_after else "off",
    )
    while True:
        try:
            sweep(
                ledger,
                seen,
                grace=grace,
                remind_after=remind_after,
                dry_run=dry_run,
                skip=skip,
            )
        except TmuxUnreachable as exc:
            # Anticipated: the box may have no tmux server between agents. A
            # warning rather than a traceback, because a stack trace every 12
            # seconds is how a log stops being read.
            log.warning("%s; nothing concluded from this pass", exc)
        except Exception:
            # A watcher that dies on one bad pane stops watching every other
            # pane, which is a worse failure than the one it is reporting.
            log.exception("sweep failed; continuing")
        if once:
            return 0
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hotline-permwatcher",
        description="Watch for agents blocked on prompts they cannot answer, and escalate.",
    )
    parser.add_argument("--interval", type=float, default=INTERVAL, help="seconds between passes")
    parser.add_argument(
        "--grace", type=float, default=GRACE, help="seconds a prompt must stand before escalating"
    )
    parser.add_argument(
        "--remind-after",
        type=float,
        default=REMIND_AFTER,
        help="seconds between repeats of the same unanswered prompt (0 disables)",
    )
    parser.add_argument(
        "--once", action="store_true", help="one pass and exit, for testing and for a timer"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be escalated, send nothing",
    )
    parser.add_argument(
        "--status", action="store_true", help="what is blocked right now, then exit"
    )
    parser.add_argument(
        "--skip",
        action="append",
        default=[],
        metavar="TMUX",
        help="a tmux session to ignore (repeatable)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    skip = set(args.skip) | ({_self_name()} if _self_name() else set())

    if args.status:
        try:
            found = survey(skip=skip)
        except TmuxUnreachable as exc:
            # Not "nothing blocked". This is the project's signature failure --
            # an absence in a view that was never rendered, read as a signal.
            print(f"cannot tell: {exc}", file=sys.stderr)
            return 1
        if not found:
            print("nothing blocked")
            return 0
        op = operator()
        print(f"operator: {op.name if op else '(none registered)'}\n")
        for blocked in found:
            print(blocked.report())
            print("-" * 72)
        return 0

    return run(
        interval=args.interval,
        grace=args.grace,
        remind_after=args.remind_after,
        dry_run=args.dry_run,
        once=args.once,
        skip=skip,
    )


if __name__ == "__main__":
    raise SystemExit(main())
