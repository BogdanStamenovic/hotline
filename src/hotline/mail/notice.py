"""Making the board visible, and telling the operator when something changes.

**Where the board lives: one pinned message in the operator's own channel,
edited in place.** Not a new channel, because hotline's sidebar is arranged by
`layout.arrange` around agent channels and a guild has a hard 500-channel cap;
a channel holding a single message is a lot of furniture for one message. Not
a post per check, because that is the spam he asked not to get. The operator's
channel because mail goes through the operator and nobody else ("email go[es]
through you"), so the board belongs where the operator and Bogdan already
look. `HOTLINE_MAIL_CHANNEL_ID` moves it somewhere else without code changes.

The pinned message is refreshed when its content changes, and otherwise at
most every `STALE_AFTER` seconds, so the "checked at" line is a liveness signal
-- an edit is silent in Discord, so that costs nobody a notification.

**Who is told: the operator, only.** An arrival or a first-time overdue goes to
whoever holds sys-admin, by `hotline --to NAME --no-wait`, as kind="service".
If that fails it falls back to one short message in the board channel, without
the fenced block, because a Discord reader is a person, not a model.

Never in Discord: bodies, To/Cc, or anything from mail nobody expected. Only
the From, Subject and Date of a message that matched an expectation, flattened
and markdown-escaped, with mentions disabled at the API as well.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import HotlineError
from .board import RECEIVED, Board, Expectation
from .inbox import Inbox, Summary
from .untrusted import discord_safe, fence, one_line

STALE_AFTER = 30 * 60.0
RECENT_DAYS = 7
SCAN_CAP = 500  # newest messages examined per mailbox per check
DISCORD_LIMIT = 2000
CHANNEL_ENV = "HOTLINE_MAIL_CHANNEL_ID"


def _when(epoch: float | None) -> str:
    return time.strftime("%m-%d %H:%M UTC", time.gmtime(epoch)) if epoch else "-"


def _day(epoch: float | None) -> str:
    return time.strftime("%m-%d", time.gmtime(epoch)) if epoch else "-"


def _ours(text: str | None, limit: int = 100) -> str:
    """Board text written by agents (why, patterns): trusted, but still one line."""
    return one_line(text or "", limit).replace("`", "'")


def render_board(board: Board, now: float | None = None, error: str | None = None) -> str:
    now = now or time.time()
    waiting = board.open()
    recent = [e for e in board.all([RECEIVED]) if (e.received_at or 0) > now - RECENT_DAYS * 86400]
    lines = [
        "📬 **Mail board**: email somebody is waiting for",
        (
            f"-# one message, edited in place by hotline-mail · checked {_when(now)}"
            f" · {len(waiting)} waiting, {len(recent)} received in {RECENT_DAYS}d"
        ),
    ]
    if error:
        lines.append(f"⚠️ last check failed: {discord_safe(error, 150)}")
    lines.append(f"**Waiting ({len(waiting)})**")
    if not waiting:
        lines.append("-# nothing expected right now")
    for e in waiting:
        late = f" · ⚠️ **OVERDUE** (due {_day(e.due)})" if e.overdue(now) else ""
        due = f" · by {_day(e.due)}" if e.due and not late else ""
        who = f" · for {_ours(e.agent, 40)}" if e.agent else ""
        lines.append(f"`#{e.id}` {_ours(e.why)} · `{_ours(e.rules(), 80)}`{due}{who}{late}")
    lines.append(f"**Received ({len(recent)})**")
    if not recent:
        lines.append("-# nothing yet")
    for e in sorted(recent, key=lambda x: x.received_at or 0, reverse=True):
        lines.append(
            f"`#{e.id}` {_ours(e.why)} ✅ {_when(e.received_at)}\n"
            f"  -# from {discord_safe(e.msg_from or '', 80)} · {discord_safe(e.msg_subject or '', 100)}"
        )
    return _fit(lines)


def _fit(lines: list[str]) -> str:
    out: list[str] = []
    for i, line in enumerate(lines):
        trial = "\n".join([*out, line])
        if len(trial) > DISCORD_LIMIT - 60:
            out.append(f"-# … {len(lines) - i} more line(s): `hotline-mail list`")
            break
        out.append(line)
    return "\n".join(out)


# ---- who the operator is ---------------------------------------------------


def operator() -> Any:
    """Whoever holds sys-admin now, resolved per call (the role moves on respawn)."""
    from ..agents import Registry

    working = [a for a in Registry().privileged() if not a.done]
    return max(working, key=lambda a: a.declared_at) if working else None


def board_channel() -> int | None:
    explicit = os.environ.get(CHANNEL_ENV, "").strip()
    if explicit:
        return int(explicit)
    op = operator()
    return int(op.channel_id) if op is not None and op.channel_id else None


# ---- Discord ---------------------------------------------------------------


class DiscordBoard:
    """The pinned board message, plus the rare fallback post."""

    def __init__(self, call: Callable[..., Any], board: Board) -> None:
        self.call = call
        self.board = board

    def _post(self, channel: int, content: str) -> str:
        sent = self.call(
            f"/channels/{channel}/messages",
            "POST",
            {"content": content, "allowed_mentions": {"parse": []}},
        )
        return str(sent["id"])

    def publish(self, channel: int, content: str, now: float | None = None) -> str:
        """Create, or edit in place. Returns what it did, for the log."""
        now = now or time.time()
        digest = hashlib.sha256(content.split("\n", 2)[-1].encode()).hexdigest()[:16]
        where = self.board.meta("board_channel")
        message = self.board.meta("board_message")
        last = float(self.board.meta("board_published") or 0)
        same = digest == self.board.meta("board_digest")
        if message and where == str(channel):
            if same and now - last < STALE_AFTER:
                return "unchanged"
            try:
                self.call(
                    f"/channels/{channel}/messages/{message}",
                    "PATCH",
                    {"content": content, "allowed_mentions": {"parse": []}},
                )
                self._remember(channel, message, digest, now)
                return "edited"
            except HotlineError as exc:
                if "(404)" not in str(exc):
                    raise
                # Deleted by hand, or the channel was rebuilt: post a fresh one.
        message = self._post(channel, content)
        try:
            self.call(f"/channels/{channel}/pins/{message}", "PUT")
        except HotlineError as exc:
            # Unpinned still works, it is just harder to find.
            print(f"hotline-mail: could not pin the board: {exc}", file=sys.stderr)
        self._remember(channel, message, digest, now)
        return "posted"

    def _remember(self, channel: int, message: str, digest: str, now: float) -> None:
        self.board.set_meta("board_channel", str(channel))
        self.board.set_meta("board_message", message)
        self.board.set_meta("board_digest", digest)
        self.board.set_meta("board_published", str(now))

    def fallback(self, channel: int, text: str) -> str:
        return self._post(channel, text)


# ---- notifications ---------------------------------------------------------


@dataclass
class Event:
    kind: str  # "arrived" | "overdue"
    exp: Expectation
    mail: Summary | None = None
    matched: str = ""

    def for_operator(self) -> str:
        """The text an agent reads. Our facts outside the fence, theirs inside."""
        e = self.exp
        head = [
            f"[hotline-mail] expectation #{e.id} {self.kind.upper()}: {_ours(e.why, 200)}",
            f"rules: {e.rules()}" + (f" · requested by {e.agent}" if e.agent else ""),
        ]
        if self.kind == "overdue":
            head.append(
                f"due {_when(e.due)} and nothing matching has arrived. Flagged once; "
                f"`hotline-mail close {e.id}` or `cancel {e.id}` when you have decided."
            )
            return "\n".join(head)
        assert self.mail is not None
        head.append(
            f"read it: hotline-mail show {self.mail.uid} --mailbox {self.mail.mailbox!r}"
            " (operator only; pass on what the requester needs, not the mail)"
        )
        theirs = "\n".join(
            [
                f"From: {one_line(self.mail.from_)}",
                f"Subject: {one_line(self.mail.subject)}",
                f"Date: {one_line(self.mail.date)}",
                f"Why it matched: {one_line(self.matched, 300)}",
            ]
        )
        return "\n".join(head) + "\n\n" + fence(theirs)

    def for_discord(self) -> str:
        e = self.exp
        if self.kind == "overdue":
            return f"⚠️ mail expectation `#{e.id}` is overdue: {_ours(e.why)}"
        assert self.mail is not None
        return (
            f"📬 expected mail arrived for `#{e.id}` {_ours(e.why)}\n"
            f"-# from {discord_safe(self.mail.from_, 80)} · {discord_safe(self.mail.subject, 100)}"
        )


def _hotline_bin() -> str:
    sibling = Path(sys.executable).with_name("hotline")
    return str(sibling) if sibling.exists() else (shutil.which("hotline") or "hotline")


def tell_operator(text: str) -> tuple[bool, str]:
    op = operator()
    if op is None:
        return False, "no sys-admin agent is registered"
    env = {**os.environ, "HOTLINE_SERVICE": "hotline-mail"}
    # A unit's own session id would make this read as kind="agent".
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    try:
        # argv with the text as one element, never a shell string: an email
        # subject with backticks in it would otherwise be executed.
        done = subprocess.run(
            [_hotline_bin(), "--to", op.name, "--no-wait", text],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{op.name}: {exc}"
    if done.returncode != 0:
        return False, f"{op.name}: {(done.stderr or done.stdout).strip()[:160]}"
    return True, op.name


# ---- one pass --------------------------------------------------------------


@dataclass
class CheckResult:
    scanned: int = 0
    arrived: list[int] = field(default_factory=list)
    overdue: list[int] = field(default_factory=list)
    told: list[str] = field(default_factory=list)
    board: str = "skipped"
    error: str | None = None

    def line(self) -> str:
        parts = [f"scanned {self.scanned}", f"arrived {self.arrived or '-'}"]
        parts += [f"overdue {self.overdue or '-'}", f"board {self.board}"]
        if self.error:
            parts.append(f"error {self.error}")
        return "mail check: " + ", ".join(parts)


def scan(inbox: Inbox, since: float) -> list[Summary]:
    found: list[Summary] = []
    for mailbox in inbox.watch_mailboxes():
        found += inbox.search(mailbox, limit=SCAN_CAP, since=since)
    return found


def run_check(
    board: Board,
    *,
    open_inbox: Callable[[], Any] | None,
    own_address: str = "",
    discord: DiscordBoard | None = None,
    channel: int | None = None,
    notify: Callable[[str], tuple[bool, str]] = tell_operator,
    now: float | None = None,
) -> CheckResult:
    """One pass: match, announce, flag overdue, refresh the board.

    `open_inbox` is a context-manager factory, or None when there are no
    credentials -- in which case matching is skipped and said so on the board,
    and everything else still runs.
    """
    now = now or time.time()
    result = CheckResult()
    events: list[Event] = []
    pending = board.open()
    if pending and open_inbox is None:
        result.error = "no IMAP credentials"
    elif pending:
        assert open_inbox is not None
        try:
            with open_inbox() as inbox:
                mail = scan(inbox, min(e.since for e in pending))
        except (HotlineError, OSError) as exc:
            result.error = f"{type(exc).__name__}: {exc}"[:200]
        else:
            result.scanned = len(mail)
            for exp, msg, why in board.match(mail, own_address):
                result.arrived.append(exp.id)
                events.append(Event("arrived", exp, msg, why))
    for exp in board.newly_overdue(now):
        result.overdue.append(exp.id)
        events.append(Event("overdue", exp))

    for event in events:
        ok, where = notify(event.for_operator())
        if not ok and discord is not None and channel is not None:
            try:
                discord.fallback(channel, event.for_discord())
                where = f"board channel ({where})"
                ok = True
            except HotlineError as exc:
                where = f"nowhere: {where}; discord {exc}"
        result.told.append(f"#{event.exp.id} {event.kind} -> {where}")
        # Marked even when delivery failed: the board shows it either way, and
        # an unmarked failure would re-announce every five minutes forever.
        if event.kind == "arrived":
            board.mark_notified(event.exp.id)
        else:
            board.mark_overdue_flagged(event.exp.id)

    board.set_meta("last_check", str(now))
    board.set_meta("last_error", result.error)
    if discord is not None and channel is not None:
        try:
            result.board = discord.publish(channel, render_board(board, now, result.error), now)
        except HotlineError as exc:
            result.board = f"failed: {exc}"[:200]
    return result
