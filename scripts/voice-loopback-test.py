#!/usr/bin/env python3
"""End-to-end voice test with no human in the room.

Bogdan is away and the voice path cannot be tested by typing. But there are *two*
Discord bots in this guild -- `hotline` and `hotline-sentinel` -- and a bot can
transmit audio as well as receive it. So the sentinel joins the voice channel and
speaks a phrase through Piper, and hotline receives it over the real Discord voice
transport, segments it, transcribes it on the GPU, routes it, and speaks the answer
back.

That exercises everything the real call does -- Opus, RTP, DAVE end-to-end
encryption, the per-user sink, VAD segmentation, Whisper, the router, Piper -- with
the single exception of a human larynx.

Usage:
    scripts/voice-loopback-test.py ["a phrase to say" ...]

It is a test harness, not part of the product, which is why it lives in scripts/
and constructs its own clients rather than reaching into the running daemon.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import discord  # noqa: E402

from hotline.audio import Speaker, Transcriber  # noqa: E402
from hotline.config import load_env  # noqa: E402
from hotline.pool import SessionPool  # noqa: E402
from hotline.router import Router  # noqa: E402
from hotline.voice import FRAME_BYTES, StreamSource, VoiceCall  # noqa: E402

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
    source = StreamSource()
    talker_vc.play(source)
    log.info("talker joined and is transmitting")
    await asyncio.sleep(2)

    for phrase in phrases:
        log.info("SAYING: %r", phrase)
        pcm = await loop.run_in_executor(None, speaker.to_discord, phrase)
        source.push(pcm)
        # Wait for playback plus a beat of silence so the VAD closes the utterance,
        # then give the turn time to run.
        await asyncio.sleep(len(pcm) / (FRAME_BYTES * 50) + 3)
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
    await listener.close()
    await talker.close()
    return 0 if call.transcript else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:] or PHRASES)))
