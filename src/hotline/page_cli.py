"""`hotline-page` -- ask Bogdan something and block until he answers.

Prints his reply on stdout and nothing else, so it composes:

    answer=$(hotline-page "may I spend money on a UI agency?")

Exit codes: 0 he answered, 1 the page could not be delivered, 2 usage error,
3 delivered but nobody answered before the timeout. Three is separate from one on
purpose -- "he did not reply" and "Discord is broken" call for entirely different
behaviour from the agent that asked.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import NoReturn

from . import __version__
from .config import load_env
from .pager import DEFAULT_TIMEOUT, PagerError, from_env


class _UsageError(Exception):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _UsageError(message)


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="hotline-page",
        description="Page Bogdan on Discord and wait for his reply.",
    )
    parser.add_argument("reason", nargs="*", help="what you need from him, in one or two sentences")
    parser.add_argument("--context", default="", help="extra detail, shown in a code block")
    parser.add_argument(
        "--source", default="an agent", help="who is asking, e.g. 'the hotline build'"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        metavar="SEC",
        help=f"give up after SEC (default {DEFAULT_TIMEOUT:.0f})",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="post the page and exit without waiting for an answer",
    )
    parser.add_argument(
        "--no-siren",
        action="store_true",
        help="never fire the physical siren, however long he takes",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress non-error output")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except _UsageError as exc:
        print(f"hotline-page: error: {exc}", file=sys.stderr)
        return 2

    reason = " ".join(args.reason).strip()
    if not reason:
        print("hotline-page: error: say what you need from him", file=sys.stderr)
        return 2

    def log(message: str) -> None:
        if not args.quiet:
            print(message, file=sys.stderr)

    load_env()
    try:
        pager = from_env()
    except PagerError as exc:
        print(f"hotline-page: error: {exc}", file=sys.stderr)
        return 1

    ladder = None
    if args.no_siren:
        from .pager import build_ladder

        ladder = [step for step in build_ladder(args.timeout) if step[1] != "siren"]

    try:
        result = pager.page(
            reason,
            context=args.context,
            timeout=args.timeout,
            ladder=ladder,
            source=args.source,
            wait=not args.no_wait,
        )
    except PagerError as exc:
        print(f"hotline-page: error: {exc}", file=sys.stderr)
        return 1

    if args.no_wait:
        log(f"posted to channel {result.channel_id} (not waiting)")
        return 0

    log(f"waited {result.waited_seconds:.0f}s, escalations: {', '.join(result.escalations)}")
    if not result.answered:
        log("no answer")
        return 3
    print(result.reply)
    return 0
