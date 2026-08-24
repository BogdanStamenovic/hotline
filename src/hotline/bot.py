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

from .agents import Agent
from .channels import PREFIX as CHANNEL_PREFIX
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
        if not self.channel_id or message.channel.id == self.channel_id:
            return True
        # An agent's own channel counts too. The author-id and guild checks above
        # are the security model and are untouched; this only widens *which* of
        # Bogdan's channels are listened to, and only to channels the registry
        # says belong to an agent that is still working -- not to anything merely
        # named like one.
        return self._agent_for_text(message.channel.id) is not None

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
        # Typing in an agent's own channel is how you say which agent you mean.
        # Without this the message keys a conversation that has never been bound,
        # falls through to `own`, and answers from a freshly spawned session with
        # no idea what it is -- the "it spawned an agent telling me you are not
        # available" failure, one layer down.
        owner = self._agent_for_text(message.channel.id)
        if owner is not None:
            self.pool.bind(key, owner.name, owner.session_id)
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
            header = "" if route.mode in ("fresh", "own") else f"*{route.mode} → {route.target}*\n"
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

    async def deliver(self, key: str, text: str) -> None:
        """Push something into the channel nobody is currently waiting on.

        The stand-in tells the sender their message is queued and that the answer
        will be relayed. This is what performs that relay -- and it is also the
        only way anything in hotline can speak without having been spoken to,
        which is why it is deliberately narrow: a conversation key maps to exactly
        one channel, and anything that does not parse is logged rather than
        guessed at.
        """
        if not key.startswith("discord-"):
            return
        try:
            channel_id = int(key.removeprefix("discord-"))
        except ValueError:
            self.log(f"cannot relay to {key!r}: not a channel id")
            return
        channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            self.log(f"cannot relay to {key!r}: channel is gone or cannot be posted to")
            return
        for part in chunk(text):
            with contextlib.suppress(discord.HTTPException):
                await channel.send(part)
        self.log(f"relayed {len(text)} chars to {key}")

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

    def _agent_for_voice(self, channel_id: int) -> Agent | None:
        """Which agent owns this voice channel, if any."""
        return self._agent_owning(channel_id, "voice_channel_id")

    def _agent_for_text(self, channel_id: int) -> Agent | None:
        """Which agent owns this text channel, if any.

        Its absence was the bug: an agent's channel was created, kept in step with
        its task and deleted on `done`, and nothing ever read it. Every message
        typed into one failed the gate and was logged as ignored, so the channels
        were write-only -- the feature looked complete from the outside because
        the channel appeared.
        """
        return self._agent_owning(channel_id, "channel_id")

    @staticmethod
    def _agent_owning(channel_id: int, field: str) -> Agent | None:
        from .agents import Registry

        for agent in Registry().agents.values():
            if getattr(agent, field) == channel_id and not agent.done:
                return agent
        return None

    async def _join_voice(
        self,
        message: discord.Message | None = None,
        channel: discord.VoiceChannel | None = None,
    ) -> None:
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
            if channel is None:
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
            # An agent's own voice channel means you have already said who you
            # want to talk to by walking into it. Binding here saves a "connect"
            # you would otherwise have to say out loud, and is the reason a voice
            # channel per agent is worth anything at all.
            agent = self._agent_for_voice(channel.id)
            greeting = "Hotline. What do you need?"
            if agent is not None:
                self.pool.bind(call.key, agent.name, agent.session_id)
                greeting = f"{agent.name} here. {agent.task}. What do you need?"

            try:
                await call.join(channel)
                self.call = call
            finally:
                self._joining = False
            await call.say(greeting)
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

        after_channel = after.channel
        target = (
            after_channel
            if isinstance(after_channel, discord.VoiceChannel) and self._is_ours(after_channel)
            else None
        )
        left = self._is_ours(before.channel) and target is None
        if target is not None and self.call is None:
            self.log(f"bogdan joined {target.name}; picking up")
            await self._join_voice(channel=target)
        elif left and self.call is not None:
            self.log("bogdan left the voice channel; hanging up")
            await self._leave_voice()

    def _is_ours(self, channel: object) -> bool:
        """The configured channel, or any agent's own one.

        Only one call runs at a time -- one GPU, one Whisper, one Piper, and
        Bogdan can only stand in one room anyway -- so agents get a voice channel
        each and the hardware constraint resolves itself: whichever one he walks
        into is the call.
        """
        if not isinstance(channel, discord.VoiceChannel):
            return False
        if self.voice_channel_id and channel.id == self.voice_channel_id:
            return True
        return channel.name.startswith(CHANNEL_PREFIX)

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

