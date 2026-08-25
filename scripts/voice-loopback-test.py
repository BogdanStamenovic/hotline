#!/usr/bin/env python3
"""End-to-end voice test with no human in the room.

Bogdan is away and the voice path cannot be tested by typing. But there are *two*
Discord bots in this guild -- `hotline` and `hotline-sentinel` -- and a bot can
transmit audio as well as receive it. So the sentinel joins the voice channel and
speaks a phrase through Piper, and hotline receives it over the real Discord voice
transport, segments it, transcribes it on the GPU, routes it, and speaks the answer
back.

That exercises everything a real call does -- Opus, RTP, DAVE end-to-end
encryption, the per-user sink, VAD segmentation, Whisper, the router and Piper --
with the single exception of a human larynx. It is also what found the py-cord
receive bug: without two bots there was no way to put known audio in one end and
look at what came out the other.

Usage:
    scripts/voice-loopback-test.py ["a phrase to say" ...]

A test harness, not part of the product, which is why it lives in scripts/ and
builds its own clients rather than reaching into the running daemon.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import discord

from hotline.audio import Speaker, Transcriber
from hotline.config import load_env
from hotline.pool import SessionPool
from hotline.router import Router
from hotline.voice import VoiceCall

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("loopback")

PHRASES = [
    "Session list.",
    "What is in the data directory? Answer in one short sentence.",
]


async def main(phrases: list[str]) -> int:
    load_env()
    guild_id = int(os.environ["DISCORD_GUILD_ID"])
    voice_id = int(os.environ["DISCORD_VOICE_CHANNEL_ID"])
    listener_token = os.environ["HOTLINE_BOT_TOKEN"]
    speaker_token = os.environ["SENTINEL_BOT_TOKEN"]

    intents = discord.Intents.default()
    intents.voice_states = True
    listener = discord.Client(intents=intents)
    talker = discord.Client(intents=intents)

    ready = asyncio.Event()
    both = {"n": 0}

    @listener.event
    async def on_ready() -> None:
        both["n"] += 1
        log.info("listener ready as %s (%s)", listener.user, listener.user.id)
        if both["n"] == 2:
            ready.set()

    @talker.event
    async def on_ready() -> None:  # noqa: F811
        both["n"] += 1
        log.info("talker ready as %s (%s)", talker.user, talker.user.id)
        if both["n"] == 2:
            ready.set()

    asyncio.create_task(listener.start(listener_token))
    asyncio.create_task(talker.start(speaker_token))
    await asyncio.wait_for(ready.wait(), 60)

    speaker_id = talker.user.id
    guild = listener.get_guild(guild_id)
    channel = guild.get_channel(voice_id)
    log.info("voice channel: %s", channel)

    log.info("warming models…")
    transcriber, speaker = Transcriber(), Speaker()
    loop = asyncio.get_running_loop()
    began = time.monotonic()
    await loop.run_in_executor(None, transcriber.load)
    await loop.run_in_executor(None, speaker.load)
    log.info("models warm in %.1fs", time.monotonic() - began)

    pool = SessionPool(router=Router(default_cwd="/home/bodas/data"), cwd="/home/bodas/data")
    call = VoiceCall(
        pool=pool,
        transcriber=transcriber,
        speaker=speaker,
        # The whole point of the harness: allow the sentinel's audio through the
        # gate that normally only Bogdan passes.
        allowed={speaker_id},
        key="loopback-test",
        log_fn=lambda m: log.info("[call] %s", m),
    )
    await call.join(channel)
    log.info("listener joined and is recording")

    talker_guild = talker.get_guild(guild_id)
    talker_channel = talker_guild.get_channel(voice_id)
    talker_vc = await talker_channel.connect()
    log.info("talker joined")
    await asyncio.sleep(2)

    for phrase in phrases:
        log.info("SAYING: %r", phrase)
        pcm = await loop.run_in_executor(None, speaker.to_discord, phrase)
        # One play() per utterance rather than pushing into an always-on silent
        # stream. A play/stop cycle makes py-cord send the speaking transitions
        # Discord uses to decide whether to forward a stream at all.
        talker_vc.play(discord.PCMAudio(io.BytesIO(pcm)))
        while talker_vc.is_playing():
            await asyncio.sleep(0.2)
        await asyncio.sleep(3)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if len(call.transcript) >= 2 and call.transcript[-1][0] == "claude":
                break
            await asyncio.sleep(0.5)
        await asyncio.sleep(2)

    log.info("=" * 60)
    if not call.transcript:
        log.error("NOTHING WAS HEARD -- the audio never made it through")
    for who, what in call.transcript:
        log.info("%-7s %s", who + ":", what)
    log.info("=" * 60)

    await call.leave()
    await talker_vc.disconnect(force=True)
    await pool.close()
    _clean_up("loopback-test")
    await listener.close()
    await talker.close()
    return 0 if call.transcript else 1


def _clean_up(key: str) -> None:
    """Take the harness's own session and channel away again.

    `pool.close()` deliberately leaves sessions running -- that is right for the
    daemon, whose whole point is that a restart costs nobody their context, and
    wrong for a test. Every run of this harness used to leave an `hl-loopback-test`
    pane behind, which showed up in Bogdan's session list looking like a real
    agent he had forgotten about.

    It got worse when sessions started auto-enrolling: the pane now also has a
    registry record and a real Discord channel in his server. A test that
    accumulates channels is the bug the conftest guard was written for, one layer
    out, where that guard cannot reach.
    """
    from hotline import tmuxen
    from hotline.agents import Registry
    from hotline.ccsocks import discover
    from hotline.channels import from_env as channels_from_env

    name = tmuxen.tmux_name(key)
    ours = {s.session_id for s in discover(include_self=True, include_programmatic=True)
            if s.tmux_session == name}
    registry = Registry()
    manager = channels_from_env()
    for session_id in ours:
        agent = registry.get(session_id)
        if agent is None:
            continue
        if agent.channel_id is not None and manager is not None:
            with contextlib.suppress(Exception):
                manager.delete(agent.channel_id)
        registry.forget(session_id)
        log.info("cleaned up registry record and channel for %s", agent.name)
    if tmuxen.kill(name):
        log.info("killed the harness session %s", name)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:] or PHRASES)))
