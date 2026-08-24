"""The Discord text bridge.

Message Bogdan's private guild (or DM the bot) and get a real answer from a Claude
session. Runs inside `hotlined` as a task so it shares the session pool with the
phone path -- the same conversation state, one set of `claude` processes.

**The gate is the whole security model.** This bot can run anything: the sessions
it drives use `bypassPermissions`, and `%wheel NOPASSWD: ALL` is in place, so a
message that gets through here is root-equivalent on this machine. Every message is
therefore checked against the author's user id first, and against the guild and
channel ids after that. Guild membership is not sufficient and never was -- anyone
invited to the server would inherit a shell.

**Narration matters here for the same reason it matters on a call.** A turn can run
for a minute with nothing to show. Instead of silence, the bot posts one message
and edits it as `tool_use` and `task_summary` events arrive, so there is always
something on screen saying what is being done. Edits are throttled, because
Discord's per-channel edit limit is low and hitting it turns live narration into a
stalled one.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

import discord

from .config import page_claim
from .errors import HotlineError
from .fresh import Event
from .pool import SessionPool
from .text import MAX_MESSAGE, chunk

if TYPE_CHECKING:  # the voice extra is optional; only the type is needed here
    from .voice import VoiceCall

# A page-claim older than this is assumed to belong to a process that died, so
# the bridge un-mutes itself rather than staying silent forever.
CLAIM_MAX_AGE = 2400.0

EDIT_INTERVAL = 2.0  # Discord allows ~5 edits / 5s per channel. Stay well under.
THINKING = "…"


class Narration:
    """A single message that keeps saying what is happening, then gets out of the way."""

    def __init__(self, message: discord.Message) -> None:
        self.message = message
        self.lines: list[str] = []
        self.last_edit = 0.0
        self.dirty = False

    def add(self, event: Event) -> None:
        if event.kind not in ("tool", "summary"):
            return
        # `task_summary` arrives right after the `tool_use` it describes and says
        # the same thing in words a person would use. Prefer it, and replace rather
        # than stack, so the list reads as steps and not as noise.
        if event.kind == "summary" and self.lines:
            self.lines[-1] = f"· {event.detail}"
        else:
            self.lines.append(f"· {event.detail}")
        del self.lines[:-6]
        self.dirty = True

    async def flush(self, force: bool = False) -> None:
        now = time.monotonic()
        if not self.dirty or (not force and now - self.last_edit < EDIT_INTERVAL):
            return
        self.dirty = False
        self.last_edit = now
        body = "\n".join(self.lines)[:MAX_MESSAGE] or THINKING
        with contextlib.suppress(discord.HTTPException):
            await self.message.edit(content=body)


class HotlineBot(discord.Bot):
    def __init__(
        self,
        pool: SessionPool,
        user_id: int,
        guild_id: int | None,
        channel_id: int | None,
        log: Callable[[str], None],
        voice_channel_id: int | None = None,
        voice: bool = True,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        # voice_states is not privileged; without it the bot never learns that
        # Bogdan joined the channel and can only be summoned by typing.
        intents.voice_states = True
        super().__init__(intents=intents)
        self.pool = pool
        self.user_id = user_id
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.log = log
        self.voice_channel_id = voice_channel_id
        self.voice_enabled = voice
        # Typed loosely on purpose: `hotline.voice` imports numpy, torch and
        # faster-whisper, and this module must import cleanly without the voice
        # extra so the text bridge works on its own.
        self.call: VoiceCall | None = None
        self._joining = False
        self._call_lock = asyncio.Lock()

    async def on_ready(self) -> None:
        who = f"{self.user} ({self.user.id})" if self.user else "?"
        self.log(f"discord connected as {who}; gated on user {self.user_id}")
        # If he is already sitting in the voice channel, no state-update event is
        # ever coming -- joining is an edge, and we missed it. Without this a
        # restart mid-call leaves him talking to a bot that will never pick up,
        # with nothing in the log to say why.
        await self._pick_up_if_already_waiting()

    async def _pick_up_if_already_waiting(self) -> None:
        channel = await self._voice_channel()
        if channel is None or self.call is not None:
            return
        if any(member.id == self.user_id for member in channel.members):
            self.log("bogdan is already in the voice channel; picking up")
            await self._join_voice()

    @staticmethod
    def page_outstanding() -> bool:
        """True while `hotline-page` is waiting for an answer in this channel.

        The pager and the bridge share a channel, so without this every reply to a
        page ("public", "yes, go ahead") would also be handed to a Claude session
        as a fresh instruction -- which is both noisy and, with bypass on, a bad
        way to find out about an ambiguity.
        """
        try:
            written = float(page_claim().read_text())
        except (OSError, ValueError):
            return False
        return (time.time() - written) < CLAIM_MAX_AGE

    def permitted(self, message: discord.Message) -> bool:
        if message.author.bot or message.author.id != self.user_id:
            return False
        if isinstance(message.channel, discord.DMChannel):
            return True
        if self.guild_id and (message.guild is None or message.guild.id != self.guild_id):
            return False
        return not (self.channel_id and message.channel.id != self.channel_id)

    async def on_message(self, message: discord.Message) -> None:
        if not self.permitted(message):
            if not message.author.bot:
                self.log(
                    f"ignored message from {message.author} ({message.author.id}) "
                    f"in {message.channel}"
                )
            return

        text = message.content.strip()
        if not text:
            return
        if self.page_outstanding():
            self.log(f"page outstanding; leaving {text[:60]!r} for the pager")
            return
        if text.lower() in ("!sessions", "!status"):
            await self._status(message)
            return
        if text.lower() in ("!join", "!call"):
            await self._join_voice(message)
            return
        if text.lower() in ("!leave", "!hangup"):
            await self._leave_voice(message)
            return

        key = f"discord-{message.channel.id}"
        placeholder = await message.channel.send(THINKING)
        narration = Narration(placeholder)
        pending: list[Event] = []

        def narrate(event: Event) -> None:
            # Called from the turn's own coroutine; keep it non-blocking and let
            # the flusher below do the rate-limited I/O.
            pending.append(event)

        async def flusher() -> None:
            while True:
                await asyncio.sleep(0.5)
                while pending:
                    narration.add(pending.pop(0))
                await narration.flush()

        flush_task = asyncio.create_task(flusher())
        began = time.monotonic()
        try:
            async with message.channel.typing():
                route, reply = await self.pool.ask(key, text, narrator=narrate, timeout=900.0)
            body = reply.text
            header = "" if route.mode == "fresh" else f"*{route.mode} → {route.target}*\n"
            if reply.notice:
                header = f"⚠️ *{reply.notice}.*\n" + header
        except HotlineError as exc:
            body = f"That didn't work.\n```\n{type(exc).__name__}: {exc}\n```"
            header = ""
        except Exception as exc:  # noqa: BLE001 - a crash here must still answer
            self.log(f"turn crashed: {type(exc).__name__}: {exc}")
            body = f"Something broke on my side.\n```\n{type(exc).__name__}: {exc}\n```"
            header = ""
        finally:
            flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await flush_task

        elapsed = time.monotonic() - began
        parts = chunk(header + body)
        with contextlib.suppress(discord.HTTPException):
            await placeholder.edit(content=parts[0])
        for part in parts[1:]:
            with contextlib.suppress(discord.HTTPException):
                await message.channel.send(part)
        self.log(f"{key}: answered in {elapsed:.1f}s ({len(body)} chars, {len(parts)} parts)")

    async def _status(self, message: discord.Message) -> None:
        live = self.pool.router.sessions()
        stats = self.pool.stats()
        lines = ["**Live Claude sessions**"]
        lines += [f"· {s.name} — pid {s.pid}, `{s.cwd}` ({s.status or '?'})" for s in live] or [
            "· none"
        ]
        lines.append(f"\n**Pool**: {stats['conversations']} conversation(s)")
        for entry in stats["keys"]:
            lines.append(
                f"· `{entry['key']}` — {entry['turns']} turns, "
                f"idle {entry['idle_seconds']}s"
            )
        await message.channel.send("\n".join(lines)[:MAX_MESSAGE])

    # ---- voice ---------------------------------------------------------

    async def _voice_channel(self) -> discord.VoiceChannel | None:
        if not self.voice_channel_id:
            return None
        channel = self.get_channel(self.voice_channel_id)
        return channel if isinstance(channel, discord.VoiceChannel) else None

    async def _join_voice(self, message: discord.Message | None = None) -> None:
        """Join the call and warm the models.

        Loading Whisper takes seconds and about 1.5 GB of VRAM, so it happens on
        joining rather than at startup -- the text and phone paths should not pay
        for a GPU they never use.
        """
        async with self._call_lock:
            if self.call is not None or self._joining:
                if message:
                    await message.channel.send("Already on the call.")
                return
            # Claim the slot before the slow part. Loading models takes seconds,
            # and without this every event arriving in that window starts another
            # join.
            self._joining = True
            if not self.voice_enabled:
                if message:
                    await message.channel.send("Voice is disabled (no `voice` extra installed).")
                return
            channel = await self._voice_channel()
            if channel is None:
                if message:
                    await message.channel.send(
                        "No voice channel configured — set DISCORD_VOICE_CHANNEL_ID."
                    )
                return

            try:
                from .audio import Speaker, Transcriber
                from .voice import VoiceCall
            except ImportError as exc:
                if message:
                    await message.channel.send(f"Voice extra not installed: `{exc}`")
                return

            if message:
                await message.channel.send(f"Joining **{channel.name}** — loading models…")
            transcriber, speaker = Transcriber(), Speaker()
            loop = asyncio.get_running_loop()
            began = time.monotonic()
            await loop.run_in_executor(None, transcriber.load)
            await loop.run_in_executor(None, speaker.load)
            self.log(f"voice models warm in {time.monotonic() - began:.1f}s")

            call = VoiceCall(
                pool=self.pool,
                transcriber=transcriber,
                speaker=speaker,
                allowed=self._allowed_speakers(),
                key=f"voice-{channel.id}",
                log_fn=self.log,
            )
            try:
                await call.join(channel)
                self.call = call
            finally:
                self._joining = False
            await call.say("Hotline. What do you need?")
            if message:
                await message.channel.send("On the call. Talk to me.")

    def _allowed_speakers(self) -> set[int]:
        """Whose audio is transcribed at all.

        Defaults to Bogdan alone. HOTLINE_VOICE_ALLOWED_IDS widens it, which is
        how the two-bot end-to-end test works -- and is the single most dangerous
        setting here, because anyone in this set can speak into a root shell.
        """
        allowed = {self.user_id}
        extra = os.environ.get("HOTLINE_VOICE_ALLOWED_IDS", "")
        allowed |= {int(x) for x in extra.replace(" ", "").split(",") if x.isdigit()}
        return allowed

    async def _leave_voice(self, message: discord.Message | None = None) -> None:
        async with self._call_lock:
            call = self.call
            self.call = None
        if call is None:
            if message:
                await message.channel.send("Not on a call.")
            return
        await call.leave()
        lines = [f"**{who}:** {what}" for who, what in call.transcript[-12:]]
        body = "Call ended.\n\n" + ("\n".join(lines) if lines else "_nothing was said_")
        if message:
            for part in chunk(body):
                await message.channel.send(part)

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Answer the phone. Bogdan joining the channel *is* the call starting."""
        if member.id != self.user_id or not self.voice_channel_id:
            return
        # This event also fires for mute, deafen, video and stream changes. Acting
        # on those made three overlapping joins race each other, which killed the
        # packet-router thread and tore recording down one second after joining --
        # the call connected, said hello, and then heard nothing at all.
        if (
            before.channel is not None
            and after.channel is not None
            and before.channel.id == after.channel.id
        ):
            return

        joined = after.channel is not None and after.channel.id == self.voice_channel_id
        left = before.channel is not None and before.channel.id == self.voice_channel_id
        if joined and self.call is None:
            self.log("bogdan joined the voice channel; picking up")
            await self._join_voice()
        elif left and not joined and self.call is not None:
            self.log("bogdan left the voice channel; hanging up")
            await self._leave_voice()

def build_bot(pool: SessionPool, log: Callable[[str], None]) -> HotlineBot | None:
    """None when Discord is not configured -- the phone path must still work."""
    token = os.environ.get("HOTLINE_BOT_TOKEN")
    user_id = os.environ.get("DISCORD_USER_ID")
    if not token or not user_id:
        return None

    def as_int(name: str) -> int | None:
        raw = os.environ.get(name)
        return int(raw) if raw and raw.isdigit() else None

    return HotlineBot(
        pool=pool,
        user_id=int(user_id),
        guild_id=as_int("DISCORD_GUILD_ID"),
        channel_id=as_int("DISCORD_TEXT_CHANNEL_ID"),
        log=log,
        voice_channel_id=as_int("DISCORD_VOICE_CHANNEL_ID"),
        voice=os.environ.get("HOTLINE_VOICE", "1") not in ("0", "false", "no"),
    )


async def run_bot(bot: HotlineBot, token: str, log: Callable[[str], None]) -> None:
    """Keep the gateway up without ever taking the HTTP server down with it.

    py-cord reconnects on its own for transient trouble, but a token problem or a
    library bug raises out of `start()`. If that happens the phone path must
    survive, so this logs loudly and backs off rather than propagating.
    """
    delay = 5.0
    while True:
        try:
            await bot.start(token)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log(f"discord bot died: {type(exc).__name__}: {exc}; retrying in {delay:.0f}s")
        else:
            log(f"discord bot exited cleanly; retrying in {delay:.0f}s")
        with contextlib.suppress(Exception):
            await bot.close()
        await asyncio.sleep(delay)
        delay = min(delay * 2, 300.0)

