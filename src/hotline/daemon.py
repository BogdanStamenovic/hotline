"""`hotlined` -- the HTTP front end the iPhone Shortcut talks to.

The Shortcut loop is already proven on this network by pigion-todo: dictate
on-device, POST `{text, session_id, client}`, read `response`, speak it, repeat.
No audio leaves the phone and no GPU is involved. This serves the same shape at
`/api/v1/claude` so the recipe is a copy with a different URL.

Two things are less obvious than they look.

**A turn can take minutes; a phone will not wait.** So a request has a *soft*
timeout. When it expires the turn is not cancelled -- it is shielded and left
running, and the caller gets "still working". The next thing they say attaches to
that same in-flight turn and collects the answer. Cancelling would throw away
several minutes of real work because someone's handset got bored.

**Authentication is by source address first.** There is no shared secret to hand
the phone unless Bogdan sets one, and a secret that has to be transmitted to be
useful is worse than a tailnet allowlist he already controls. `HOTLINE_API_KEY` is
honoured when present, as a second factor rather than the only one.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from .config import load_env
from .errors import HotlineError
from .fresh import Event
from .httpd import HttpError, Request, Server
from .pool import SessionPool
from .router import Router, describe

DEFAULT_PORT = 8788
# A phone gives up long before a hard timeout would. Answer within this and the
# conversation feels alive; past it, say so and keep working.
DEFAULT_SOFT_TIMEOUT = 100.0
DEFAULT_HARD_TIMEOUT = 900.0

PENDING_REPLY = "Still working on that one. Ask me again in a moment and I'll have the answer."

# Appended to every pooled session's system prompt. hotline's answers get spoken by
# a phone or read on a phone screen, and the default behaviour -- investigate
# thoroughly, then explain at length -- is wrong for both. Bogdan's words, watching
# a pooled session answer him over Discord: "You are just typing bash over and over
# again." Overridable with $HOTLINE_SYSTEM_PROMPT.
CONVERSATIONAL_PREAMBLE = """\
You are answering over a voice call or a phone-sized chat window, not in a terminal.

Answer in one or two short paragraphs, or a few short bullets. No headings, no
tables, no code blocks unless code is genuinely the answer. Say the answer first.

Do not investigate beyond what was asked. If a question can be answered from what
you already know, answer it -- do not run commands to prove it. Use tools when the
answer genuinely requires looking something up or changing something, and then use
as few as will do.

If something is ambiguous, ask one short question instead of guessing and
explaining at length. If you cannot do something, say so plainly in a sentence.

This is a conversation. Match the length of the question."""


def _seconds(value: object, default: float, low: float, high: float) -> float:
    """Clamp a caller-supplied duration. These come off the wire, and an
    unbounded hard timeout would let one request pin a `claude` process forever."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if number <= 0:
        return default
    return max(low, min(high, number))


def _allowlist() -> set[str]:
    raw = os.environ.get("HOTLINE_ALLOW_IPS", "")
    entries = {item.strip() for item in raw.split(",") if item.strip()}
    entries |= {"127.0.0.1", "::1"}
    return entries


def build_server(pool: SessionPool, host: str, port: int, verbose: bool = False) -> Server:
    started = time.monotonic()
    allow = _allowlist()
    api_key = os.environ.get("HOTLINE_API_KEY") or None

    def log(message: str) -> None:
        print(f"[hotlined] {message}", file=sys.stderr, flush=True)

    server = Server(host, port, log=log)

    def authorise(request: Request) -> None:
        if request.peer not in allow:
            log(f"refused {request.method} {request.path} from {request.peer}")
            raise HttpError(403, "not an allowed source address")
        if api_key is not None and request.headers.get("x-hotline-key") != api_key:
            log(f"bad key from {request.peer}")
            raise HttpError(401, "bad or missing X-Hotline-Key")

    @server.route("GET", "/health")
    async def health(request: Request) -> tuple[int, dict[str, object]]:
        # Deliberately unauthenticated and free of detail: Pigion needs to know
        # whether this machine is awake before it has any reason to be trusted.
        return 200, {"ok": True, "uptime_seconds": round(time.monotonic() - started, 1)}

    @server.route("GET", "/api/v1/sessions")
    async def sessions(request: Request) -> tuple[int, dict[str, object]]:
        authorise(request)
        return 200, {
            "live": [describe(s) for s in pool.router.sessions()],
            "pool": pool.stats(),
        }

    @server.route("POST", "/api/v1/bind")
    async def bind(request: Request) -> tuple[int, dict[str, object]]:
        """Point a conversation at a session, from outside the conversation.

        Sticky routing already existed, but only Bogdan could set it, by typing
        `connect <name>` -- and the name is derived (`hotline-3b`), changes
        between runs, and is not something he has any reason to know. So every
        Discord message went to a freshly spawned session with no context, which
        answered his questions about the build by saying it had never heard of it.

        Letting the session claim the conversation inverts that: the agent that
        wants the traffic asks for it, and he just types.
        """
        authorise(request)
        body = request.json()
        key = str(body.get("key") or "").strip()
        target = str(body.get("session") or "").strip()
        if not key:
            raise HttpError(400, "key is required")
        if not target:
            return 200, {"key": key, "attached_to": None, "released": pool.release(key)}
        try:
            session = pool.router.resolve(target)
        except HotlineError as exc:
            raise HttpError(404, str(exc)) from exc
        pool.bind(key, session.name, session.session_id)
        return 200, {"key": key, "attached_to": session.name, "pid": session.pid}

    @server.route("POST", "/api/v1/voice")
    async def voice(request: Request) -> tuple[int, dict[str, object]]:
        """Make the bot join or leave the voice channel, and say something.

        Exists so the voice path can be exercised without waiting for a human to
        join -- during this build the only way to test a join was to ask Bogdan to
        get on a call, which made every iteration cost him a trip.
        """
        authorise(request)
        payload = request.json()
        action = str(payload.get("action") or "status")
        bot = getattr(server, "bot", None)
        if bot is None:
            raise HttpError(503, "the discord bot is not running")
        if action == "join":
            await bot._join_voice()
            if payload.get("say"):
                await bot.call.say(str(payload["say"]))
            return 200, {"joined": bot.call is not None}
        if action == "say":
            if bot.call is None:
                raise HttpError(409, "not on a call")
            await bot.call.say(str(payload.get("say") or ""))
            return 200, {"said": True}
        if action == "leave":
            await bot._leave_voice()
            return 200, {"joined": False}
        return 200, {
            "joined": bot.call is not None,
            "transcript": getattr(bot.call, "transcript", []),
        }

    @server.route("POST", "/api/v1/claude")
    async def claude(request: Request) -> tuple[int, dict[str, object]]:
        authorise(request)
        payload = request.json()
        text = str(payload.get("text") or "").strip()
        if not text:
            raise HttpError(400, "missing 'text'")
        key = str(payload.get("session_id") or f"anon-{request.peer}")
        soft = _seconds(payload.get("soft_timeout"), DEFAULT_SOFT_TIMEOUT, 1.0, 600.0)
        hard = _seconds(payload.get("timeout"), DEFAULT_HARD_TIMEOUT, soft, 3600.0)

        narration: list[str] = []

        def narrate(event: Event) -> None:
            if event.kind in ("tool", "summary"):
                narration.append(event.detail)
            if verbose:
                log(f"  ... {event.detail}")

        began = time.monotonic()
        try:
            outcome = await pool.ask_soft(
                key, text, narrator=narrate, soft_timeout=soft, hard_timeout=hard
            )
        except HotlineError as exc:
            log(f"{key}: {type(exc).__name__}: {exc}")
            # 200 with the error as the spoken response: the Shortcut speaks
            # whatever is in `response` and has no way to render a status code, so
            # a 500 here is silence on the phone.
            return 200, {"response": f"That didn't work. {exc}", "error": type(exc).__name__}

        if outcome is None:
            log(f"{key}: still working after {soft:.0f}s")
            return 200, {"response": PENDING_REPLY, "pending": True, "narration": narration}

        route, reply = outcome
        log(f"{key}: {route.mode} -> {len(reply.text)} chars in {time.monotonic() - began:.1f}s")
        spoken = reply.text
        if reply.notice:
            # Spoken first, because on the phone this is the only way the caller
            # learns their context is gone.
            spoken = f"Heads up — {reply.notice}. {spoken}"
        return 200, {
            "response": spoken,
            "notice": reply.notice,
            "route": route.mode,
            "target": route.target,
            "narration": narration,
            "claude_session_id": reply.session_id,
            "elapsed_seconds": round(time.monotonic() - began, 1),
        }

    return server


async def _sweep_forever(log: Callable[[str], None], every: float = 600.0) -> None:
    """Delete the channels of agents whose three days are up.

    Ten minutes is deliberately coarse. The deadline is measured in days, nothing
    downstream cares about the difference between deleting a channel now and
    deleting it at teatime, and each pass costs a Discord round trip.
    """
    from .agents import Registry
    from .channels import from_env as channels_from_env

    while True:
        await asyncio.sleep(every)
        try:
            swept = await asyncio.to_thread(lambda: Registry().sweep(channels_from_env(), log=log))
        except Exception as exc:  # noqa: BLE001 - a sweep must never take the daemon down
            log(f"agent sweep failed: {type(exc).__name__}: {exc}")
            continue
        if swept:
            log(f"swept {len(swept)} expired agent(s): {', '.join(swept)}")


def _relay_to(bot: Any, log: Callable[[str], None]) -> Callable[[str, str], Awaitable[None]]:
    """Route an unsolicited message to whichever transport its key belongs to.

    A voice call is spoken to; Discord is posted to. The phone gets neither, and
    says so in the log rather than pretending: HTTP has no way to push, so a relay
    for a phone conversation has nowhere to go until the caller asks again.
    """

    async def relay(key: str, text: str) -> None:
        call = getattr(bot, "call", None)
        if call is not None and getattr(call, "key", None) == key:
            await call.say(text)
            return
        if key.startswith("discord-"):
            await bot.deliver(key, text)
            return
        log(f"nowhere to relay {len(text)} chars for {key!r} (no push channel)")

    return relay


async def serve(host: str, port: int, cwd: str | None, verbose: bool, discord: bool) -> None:
    # Only #general holds messages for confirmation. A per-agent channel is
    # unambiguous by construction -- that channel *is* that agent -- so asking
    # there would be ceremony with no question behind it.
    general = os.environ.get("DISCORD_TEXT_CHANNEL_ID", "").strip()
    pool = SessionPool(
        router=Router(default_cwd=cwd),
        cwd=cwd,
        append_system_prompt=os.environ.get("HOTLINE_SYSTEM_PROMPT", CONVERSATIONAL_PREAMBLE),
        confirm_keys={f"discord-{general}"} if general else set(),
    )
    pool.start()
    server = build_server(pool, host, port, verbose=verbose)

    def log(message: str) -> None:
        print(f"[hotlined] {message}", file=sys.stderr, flush=True)

    bot_task: asyncio.Task[None] | None = None
    if discord:
        # Imported here, not at module scope: py-cord lives in the optional
        # `discord` extra, and the phone path must run without it installed.
        try:
            from .bot import build_bot, run_bot
        except ImportError as exc:
            log(f"discord requested but py-cord is not installed ({exc}); skipping")
        else:
            bot = build_bot(pool, log)
            if bot is None:
                log("discord not configured (HOTLINE_BOT_TOKEN / DISCORD_USER_ID unset)")
            else:
                token = os.environ["HOTLINE_BOT_TOKEN"]
                bot_task = asyncio.create_task(run_bot(bot, token, log))
                server.bot = bot  # type: ignore[attr-defined]
                # Wired after construction because the pool has to exist first.
                # This is how an answer from a session that was busy reaches
                # Bogdan without him asking a second time.
                pool.deliver = _relay_to(bot, log)

    sweeper = asyncio.create_task(_sweep_forever(log))

    try:
        await server.serve_forever()
    finally:
        sweeper.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweeper
        if bot_task is not None:
            bot_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await bot_task
        await pool.close()
        await server.close()


def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    import logging

    load_env()
    # Without this, every exception inside py-cord goes to a logger with no
    # handler and vanishes. A voice call died silently three times before I
    # noticed the only reason I had no traceback was that nobody had configured
    # logging.
    logging.basicConfig(
        level=os.environ.get("HOTLINE_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    logging.getLogger("discord").setLevel(os.environ.get("HOTLINE_DISCORD_LOG_LEVEL", "WARNING"))
    parser = argparse.ArgumentParser(prog="hotlined", description=__doc__.splitlines()[0])
    parser.add_argument("--host", default=os.environ.get("HOTLINE_HOST", "0.0.0.0"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("HOTLINE_PORT", DEFAULT_PORT))
    )
    parser.add_argument("--cwd", default=os.environ.get("HOTLINE_CWD") or None)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--no-discord",
        action="store_true",
        help="serve HTTP only, even if a bot token is configured",
    )
    args = parser.parse_args(argv)

    print(
        f"[hotlined] allow={sorted(_allowlist())} "
        f"key={'set' if os.environ.get('HOTLINE_API_KEY') else 'unset'}",
        file=sys.stderr,
    )
    try:
        asyncio.run(serve(args.host, args.port, args.cwd, args.verbose, not args.no_discord))
    except KeyboardInterrupt:
        return 0
    return 0
