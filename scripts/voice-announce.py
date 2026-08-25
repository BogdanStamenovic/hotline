#!/usr/bin/env python3
"""Say something over the real Discord voice transport, and prove it arrived.

The acceptance test Bogdan set is that the system announces its own completion
*through itself* -- "drive the finished voice path end to end and have it speak
the completion message", not a synthetic wav and not a text message. He is away,
so nobody is in the channel to hear it. That leaves a gap the loopback harness
does not fill: `voice-loopback-test.py` proves hotline can *hear*, because the
sentinel talks and hotline answers. Nothing proves what hotline *says* survives
Opus, RTP and DAVE on the way out.

So this runs the loop the other way round. `hotline` speaks the announcement
through Piper into the real voice channel; `hotline-sentinel` sits in the same
channel with the same gated sink and the same VAD, receives it over the real
transport, and transcribes it on the GPU. What comes out the far end is compared
with what went in.

That is deliberately NOT a routed turn -- no session, no pool, no model deciding
what to say. The message is fixed and known, because the question being answered
here is "did these exact words traverse the pipeline intelligibly", and a routed
answer would make the content a second variable. The loopback harness already
covers the routed direction.

Usage:
    scripts/voice-announce.py "the thing to say"
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import discord

from hotline.audio import Segmenter, Speaker, Transcriber, stereo48_to_mono16
from hotline.config import load_env
from hotline.voice import GatedSink, install_receive_fixes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("announce")


async def main(message: str) -> int:
    load_env()
    guild_id = int(os.environ["DISCORD_GUILD_ID"])
    voice_id = int(os.environ["DISCORD_VOICE_CHANNEL_ID"])

    intents = discord.Intents.default()
    intents.voice_states = True
    # Roles swapped relative to the loopback harness: hotline is the one talking.
    announcer = discord.Client(intents=intents)
    witness = discord.Client(intents=intents)

    ready, count = asyncio.Event(), {"n": 0}

    async def arrived(who: str, client: discord.Client) -> None:
        count["n"] += 1
        log.info("%s ready as %s (%s)", who, client.user, client.user.id)
        if count["n"] == 2:
            ready.set()

    @announcer.event
    async def on_ready() -> None:
        await arrived("announcer", announcer)

    @witness.event
    async def on_ready() -> None:  # noqa: F811
        await arrived("witness", witness)

    asyncio.create_task(announcer.start(os.environ["HOTLINE_BOT_TOKEN"]))
    asyncio.create_task(witness.start(os.environ["SENTINEL_BOT_TOKEN"]))
    await asyncio.wait_for(ready.wait(), 60)

    loop = asyncio.get_running_loop()
    transcriber, speaker = Transcriber(), Speaker()
    await loop.run_in_executor(None, transcriber.load)
    await loop.run_in_executor(None, speaker.load)
    log.info("models warm")

    heard: list[str] = []
    queue: asyncio.Queue[tuple[int, bytes]] = asyncio.Queue()
    segmenters: dict[int, Segmenter] = {}

    frames = {"n": 0}

    async def consume() -> None:
        # Wrapped, because a bare create_task swallows the exception until garbage
        # collection -- which `voice.py` documents as how a dead consumer looked
        # like "the audio never arrived" for an hour. It did it to me too.
        try:
            await consume_loop()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("consumer died")
            raise

    async def consume_loop() -> None:
        while True:
            user, pcm = await queue.get()
            frames["n"] += 1
            mono = stereo48_to_mono16(pcm)
            if mono.size == 0:
                continue
            for utterance in segmenters.setdefault(user, Segmenter()).feed(mono):
                text = await loop.run_in_executor(None, transcriber.transcribe, utterance.audio)
                if text.strip():
                    log.info("HEARD (%.1fs): %r", utterance.seconds, text)
                    heard.append(text.strip())

    witness_channel = witness.get_guild(guild_id).get_channel(voice_id)
    witness_vc = await witness_channel.connect()
    install_receive_fixes()

    def rejected(user: int) -> None:
        # Never silence this. The gate rejecting an unmapped ssrc is
        # indistinguishable from "no audio arrived" unless it says so.
        log.warning("REJECTED audio from %s (allowed: %s)", user, announcer.user.id)

    sink = GatedSink(
        loop,
        {announcer.user.id},
        lambda user, pcm: queue.put_nowait((user, pcm)),
        rejected,
    )
    sink.init(witness_vc)
    witness_vc.start_recording(sink, lambda *a: None)
    task = asyncio.create_task(consume())
    log.info("witness listening for %s only", announcer.user.id)

    announce_channel = announcer.get_guild(guild_id).get_channel(voice_id)
    announce_vc = await announce_channel.connect()
    await asyncio.sleep(2)

    log.info("ANNOUNCING: %r", message)
    pcm = await loop.run_in_executor(None, speaker.to_discord, message)
    announce_vc.play(discord.PCMAudio(io.BytesIO(pcm)))
    while announce_vc.is_playing():
        await asyncio.sleep(0.2)
    log.info("finished speaking; waiting for the tail to arrive")

    # Discord stops sending the instant a stream ends, and the segmenter closes an
    # utterance on trailing SILENCE -- so with nothing arriving it sits on a
    # complete sentence forever. `VoiceCall` has a stale-utterance closer for
    # exactly this; the first version of this script did not, which is why a run
    # with 453 gated pcm chunks still reported that nothing arrived. The audio was
    # never the problem. Give the queue a moment to drain, then flush explicitly.
    await asyncio.sleep(4)
    for segmenter in segmenters.values():
        leftover = segmenter.flush()
        if leftover is not None:
            text = await loop.run_in_executor(None, transcriber.transcribe, leftover.audio)
            if text.strip():
                log.info("HEARD on flush (%.1fs): %r", leftover.seconds, text)
                heard.append(text.strip())

    task.cancel()
    with __import__("contextlib").suppress(Exception):
        witness_vc.stop_recording()
    await announce_vc.disconnect(force=True)
    await witness_vc.disconnect(force=True)
    await announcer.close()
    await witness.close()

    log.info("pcm chunks that passed the gate: %d", frames["n"])
    transcript = " ".join(heard)
    log.info("=" * 70)
    log.info("SAID:  %s", message)
    log.info("HEARD: %s", transcript or "(nothing)")
    if not transcript:
        log.error("NOTHING ARRIVED -- the announcement did not survive the transport")
        return 1
    # Whisper punctuates and capitalises to its own taste, so a word-level
    # similarity is the honest measure rather than equality.
    ratio = SequenceMatcher(None, message.lower().split(), transcript.lower().split()).ratio()
    log.info("word similarity: %.0f%%", ratio * 100)
    log.info("=" * 70)
    return 0 if ratio > 0.6 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(" ".join(sys.argv[1:]) or "Test announcement.")))
