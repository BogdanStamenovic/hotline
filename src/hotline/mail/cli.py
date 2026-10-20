"""hotline-mail: the operator's read-only inbox, and the expected-mail board."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
import time
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import load_env
from ..errors import HotlineError
from . import creds as creds_mod
from .board import CANCELLED, CLOSED, OPEN, RECEIVED, STATUSES, Board, db_path
from .inbox import Inbox, Summary, save_attachments
from .notice import DiscordBoard, board_channel, render_board, run_check
from .untrusted import fence, one_line

BODY_LIMIT = 20_000

GATE_NOTE = (
    "This is a guard against honest mistakes, not a security boundary: every "
    "session here runs as the same user and could read ~/.msmtprc directly."
)


def operator_refusal() -> str | None:
    """Why this process may not read the inbox, or None if it may.

    His rule, 2026-10-20: "email go[es] through you" -- the operator reads mail
    and decides what to pass on. Identity is hotline's existing model: a Claude
    session is whoever the registry says its session id is, and only a live
    sys-admin record counts. A process with no session id is a person's shell
    or a systemd unit, and is let through; the gate is aimed at agents.
    """
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not sid:
        return None
    from ..agents import Registry

    record = Registry().get(sid)
    if record is not None and record.privileged and not record.done:
        return None
    who = record.name if record else sid[:8]
    return (
        f"hotline-mail: {who} is not the operator. Mail goes through the operator "
        "(hotline-80): ask it with `hotline --to hotline-80 --no-wait ...`, or post "
        "an expectation with `hotline-mail expect` and it will tell the operator. " + GATE_NOTE
    )


_REL = re.compile(r"^\+(\d+(?:\.\d+)?)([hdw])$")


def parse_when(text: str, now: float | None = None, end_of_day: bool = False) -> float:
    """YYYY-MM-DD, YYYY-MM-DD HH:MM (local time), or +N[h|d|w] from now.

    A bare date is the start of that day, or its end for a deadline: "by
    10-27" means mail on the 27th is still on time.
    """
    now = now or time.time()
    rel = _REL.match(text.strip())
    if rel:
        return now + float(rel.group(1)) * {"h": 3600, "d": 86400, "w": 604800}[rel.group(2)]
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
        if fmt == "%Y-%m-%d" and end_of_day:
            parsed = parsed.replace(hour=23, minute=59)
        return parsed.timestamp()
    raise HotlineError(f"cannot read date {text!r}: use YYYY-MM-DD, 'YYYY-MM-DD HH:MM' or +3d")


def _stamp(epoch: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch)) if epoch else "-"


def open_inbox_factory() -> Any:
    creds = creds_mod.load()

    def factory() -> AbstractContextManager[Inbox]:
        return Inbox.open(creds)

    return factory, creds.user


def _rows(mail: list[Summary]) -> str:
    if not mail:
        return "(no messages)"
    return "\n".join(
        f"uid {m.uid:>7}  {_stamp(m.received)}  {one_line(m.from_, 60):<60}  "
        f"{one_line(m.subject, 110)}"
        for m in reversed(mail)
    )


def _print_list(mail: list[Summary], mailbox: str) -> None:
    print(f"{len(mail)} message(s) in {mailbox!r}, newest first:")
    print(fence(_rows(mail)))


def _describe(e: Any, now: float) -> str:
    state = e.status + (" OVERDUE" if e.overdue(now) else "")
    lines = [
        f"#{e.id} [{state}] {e.why}",
        (
            f"   rules: {e.rules()} · since {_stamp(e.since)} · due {_stamp(e.due)}"
            f" · requested by {e.agent or '-'} · posted by {e.posted_by or '-'}"
        ),
    ]
    if e.status == RECEIVED:
        lines.append(f"   received {_stamp(e.received_at)} · uid {e.msg_uid} in {e.msg_mailbox!r}")
        lines.append(
            "   "
            + fence(
                f"From: {one_line(e.msg_from or '')}\nSubject: {one_line(e.msg_subject or '')}\n"
                f"Matched: {one_line(e.matched or '', 300)}"
            ).replace("\n", "\n   ")
        )
    if e.note:
        lines.append(f"   note: {e.note}")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hotline-mail",
        description="Read-only view of Bogdan's inbox (operator only), and a board of "
        "expected mail.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("recent", help="newest messages (operator only)")
    r.add_argument("--mailbox", default="INBOX")
    r.add_argument("-n", "--limit", type=int, default=20)

    s = sub.add_parser("search", help="search by from/subject/since/text (operator only)")
    s.add_argument("--from", dest="from_")
    s.add_argument("--subject")
    s.add_argument("--since", help="YYYY-MM-DD or +Nd style (date granularity on the server)")
    s.add_argument("--text", help="anywhere in headers or body")
    s.add_argument("--mailbox", default="INBOX")
    s.add_argument("-n", "--limit", type=int, default=20)

    sh = sub.add_parser("show", help="one message: headers, text body, attachments listed")
    sh.add_argument("uid", type=int)
    sh.add_argument("--mailbox", default="INBOX")
    sh.add_argument("--full", action="store_true", help=f"do not cut the body at {BODY_LIMIT}")
    sh.add_argument("--save-attachments", metavar="DIR", type=Path)

    sub.add_parser("mailboxes", help="folders and their special-use flags (operator only)")

    e = sub.add_parser("expect", help="post an expected email on the board")
    e.add_argument("--why", required=True, help="what this is, as a person would say it")
    e.add_argument("--from", dest="from_", help="regex on the From header (name and address)")
    e.add_argument("--subject", help="regex on the Subject")
    e.add_argument("--by", help="deadline: YYYY-MM-DD, 'YYYY-MM-DD HH:MM', +3d")
    e.add_argument("--since", help="count mail from this time (default: 24h before now)")
    e.add_argument("--agent", help="who asked for it (a note on the board; nobody else is told)")
    e.add_argument("--dry-run", action="store_true")

    ls = sub.add_parser("list", help="the board: open, received, overdue")
    ls.add_argument("--status", choices=[*STATUSES, "overdue", "all"], default=None)

    c = sub.add_parser("check", help="match new mail now, notify, refresh the board")
    c.add_argument(
        "--dry-run",
        action="store_true",
        help="match against a copy of the board; nothing is marked, posted or sent",
    )
    c.add_argument("--no-discord", action="store_true")

    for name, help_ in (("close", "done with it"), ("cancel", "no longer wanted")):
        x = sub.add_parser(name, help=f"{name} an expectation ({help_})")
        x.add_argument("id", type=int)
        x.add_argument("--note")

    sub.add_parser("status", help="counts, last check, where the board is")
    return p


INBOX_COMMANDS = {"recent", "search", "show", "mailboxes", "check"}


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    load_env()
    try:
        if args.command in INBOX_COMMANDS:
            refusal = operator_refusal()
            if refusal:
                print(refusal, file=sys.stderr)
                return 1
        return _run(args)
    except HotlineError as exc:
        print(f"hotline-mail: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def _run(args: argparse.Namespace) -> int:
    now = time.time()
    if args.command in ("recent", "search", "show", "mailboxes"):
        factory, _ = open_inbox_factory()
        with factory() as inbox:
            if args.command == "recent":
                _print_list(inbox.recent(args.mailbox, args.limit), args.mailbox)
            elif args.command == "search":
                mail = inbox.search(
                    args.mailbox,
                    args.limit,
                    from_=args.from_,
                    subject=args.subject,
                    text=args.text,
                    since=parse_when(args.since) if args.since else None,
                )
                _print_list(mail, args.mailbox)
            elif args.command == "mailboxes":
                for flags, name in inbox.mailboxes():
                    print(f"{name}  {' '.join(sorted(f for f in flags if f.startswith(chr(92))))}")
            else:
                msg = inbox.show(args.uid, args.mailbox)
                body = msg.text
                cut = ""
                if not args.full and len(body) > BODY_LIMIT:
                    body, cut = body[:BODY_LIMIT], f"\n[... cut at {BODY_LIMIT} chars; --full]"
                atts = "\n".join(
                    f"  {one_line(a.filename, 120)}  ({a.content_type}, {a.size} bytes)"
                    for a in msg.attachments
                )
                content = "\n".join(f"{k}: {one_line(v, 500)}" for k, v in msg.headers)
                content += f"\n\n{body}{cut}"
                if atts:
                    content += f"\n\nAttachments (not saved):\n{atts}"
                print(f"uid {args.uid} in {args.mailbox!r}, {msg.summary.size} bytes")
                print(fence(content))
                if args.save_attachments and msg.raw is not None:
                    for path in save_attachments(msg.raw, args.save_attachments):
                        print(f"saved {path}")
        return 0

    board = Board()
    if args.command == "expect":
        posted_by = os.environ.get("HOTLINE_AGENT") or _session_label()
        kwargs: dict[str, Any] = {
            "from_re": args.from_,
            "subject_re": args.subject,
            "due": parse_when(args.by, end_of_day=True) if args.by else None,
            "agent": args.agent,
            "posted_by": posted_by,
            "since": parse_when(args.since) if args.since else None,
        }
        if args.dry_run:
            with tempfile.TemporaryDirectory() as tmp:
                exp = Board(Path(tmp) / "mail.db").expect(args.why, **kwargs)
            print("would post:\n" + _describe(exp, now).replace(f"#{exp.id}", "#new", 1))
            return 0
        exp = board.expect(args.why, **kwargs)
        print(_describe(exp, now))
        print(
            "the next check (hotlined, every ~5 min) matches it; `hotline-mail check` runs one now"
        )
        return 0

    if args.command == "list":
        if args.status in (None, "all"):
            rows = board.all() if args.status == "all" else board.all([OPEN, RECEIVED])
        elif args.status == "overdue":
            rows = [e for e in board.open() if e.overdue(now)]
        else:
            rows = board.all([args.status])
        for e in rows:
            print(_describe(e, now))
        if not rows:
            print("board is empty" if args.status is None else f"no {args.status} expectations")
        return 0

    if args.command in ("close", "cancel"):
        exp = board.finish(args.id, CLOSED if args.command == "close" else CANCELLED, args.note)
        print(_describe(exp, now))
        return 0

    if args.command == "status":
        rows = board.all()
        counts = {s: sum(1 for e in rows if e.status == s) for s in STATUSES}
        overdue = sum(1 for e in rows if e.overdue(now))
        last = board.meta("last_check")
        print(f"board {db_path()}: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
        print(f"overdue: {overdue}")
        print(f"last check: {_stamp(float(last)) if last else 'never'}")
        if board.meta("last_error"):
            print(f"last error: {board.meta('last_error')}")
        print(
            f"board message: channel {board.meta('board_channel') or '-'}, "
            f"message {board.meta('board_message') or '-'}"
        )
        return 0

    # check
    try:
        factory, own = open_inbox_factory()
    except HotlineError as exc:
        print(f"hotline-mail: no inbox ({exc}); matching skipped", file=sys.stderr)
        factory, own = None, ""
    if args.dry_run:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "mail.db"
            if board.path.exists():
                shutil.copy(board.path, copy)
            preview = Board(copy)

            def show(text: str) -> tuple[bool, str]:
                print("--- would tell the operator ---\n" + text)
                return True, "dry-run"

            result = run_check(preview, open_inbox=factory, own_address=own, notify=show)
            print(result.line())
            print("--- board would read ---\n" + render_board(preview, now, result.error))
        return 0
    discord, channel = _discord(board, args.no_discord)
    result = run_check(board, open_inbox=factory, own_address=own, discord=discord, channel=channel)
    print(result.line())
    for line in result.told:
        print(f"  told {line}")
    return 1 if result.error else 0


def _session_label() -> str | None:
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not sid:
        return None
    from ..agents import Registry

    record = Registry().get(sid)
    return record.name if record else sid[:8]


def _discord(board: Board, disabled: bool = False) -> tuple[DiscordBoard | None, int | None]:
    if disabled:
        return None, None
    from ..channels import from_env

    manager = from_env()
    channel = board_channel()
    if manager is None or channel is None:
        return None, None
    return DiscordBoard(manager._call, board), channel


def check_once(log: Any) -> None:
    """The daemon's entry point: one full pass, logged, never raising."""
    board = Board()
    if not board.open() and board.meta("board_message") is None and not board.all():
        return  # nothing has ever been expected; stay entirely quiet
    try:
        factory, own = open_inbox_factory()
    except HotlineError as exc:
        factory, own = None, ""
        log(f"mail: no inbox credentials: {exc}")
    discord, channel = _discord(board)
    result = run_check(board, open_inbox=factory, own_address=own, discord=discord, channel=channel)
    if result.arrived or result.overdue or result.error or result.board not in ("unchanged",):
        log(result.line() + "".join(f"; told {t}" for t in result.told))


if __name__ == "__main__":
    raise SystemExit(main())
