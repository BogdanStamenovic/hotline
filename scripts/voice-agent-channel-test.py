#!/usr/bin/env python3
"""Does walking into an agent's own voice channel reach THAT agent?

The per-agent voice channel is only worth building if joining one binds the call
to the agent that owns it -- otherwise it is a room with a name on the door and a
stranger inside. `bot.py` does that binding, and it had never been verified: the
channel got created, which made the feature look finished from the outside, which
is the same shape as the write-only text channels bug.

This does what the bot does, without needing Bogdan in the room. It creates (or
reuses) a voice channel owned by a named agent, joins it as `hotline`, resolves
the owner the way the bot does, binds the conversation to that agent's session,
and then has the sentinel ask a question only that specific session can answer.

The answer is the test. A fresh session cannot know what the target was told
privately, so if the reply contains the secret the binding worked, and if it
comes back as a generic answer the call reached a stranger.

Usage:
    scripts/voice-agent-channel-test.py AGENT-NAME "question" "expected-substring"
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

from hotline.agents import Registry
from hotline.audio import Speaker, Transcriber
from hotline.channels import from_env as channels_from_env
from hotline.config import load_env
from hotline.pool import SessionPool
from hotline.router import Router
from hotline.voice import VoiceCall

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("agentvoice")


async def main(agent_name: str, question: str, expected: str) -> int:
    load_env()
    guild_id = int(os.environ["DISCORD_GUILD_ID"])

    registry = Registry()
    agent = registry.by_name(agent_name)
    if agent is None:
        log.error("no agent called %r; --declare one first", agent_name)
        return 2

    manager = channels_from_env()
    if manager is None:
        log.error("Discord is not configured")
        return 2
    # Lazily, exactly as `hotline --voice` does it.
    if not agent.voice_channel_id:
        agent.voice_channel_id = manager.create_voice(agent.name)
        registry.save()
        log.info("created voice channel for %s: %s", agent.name, agent.voice_channel_id)
    voice_id = int(agent.voice_channel_id)

    intents = discord.Intents.default()
    intents.voice_states = True
    listener, talker = discord.Client(intents=intents), discord.Client(intents=intents)
    ready, count = asyncio.Event(), {"n": 0}

    async def arrived(who: str, client: discord.Client) -> None:
        count["n"] += 1
        log.info("%s ready as %s (%s)", who, client.user, client.user.id)
        if count["n"] == 2:
            ready.set()

    @listener.event
    async def on_ready() -> None:
        await arrived("listener", listener)

    @talker.event
    async def on_ready() -> None:  # noqa: F811
        await arrived("talker", talker)

    asyncio.create_task(listener.start(os.environ["HOTLINE_BOT_TOKEN"]))
    asyncio.create_task(talker.start(os.environ["SENTINEL_BOT_TOKEN"]))
    await asyncio.wait_for(ready.wait(), 60)

    channel = listener.get_guild(guild_id).get_channel(voice_id)
    if channel is None:
        log.error("voice channel %s is not visible to the bot", voice_id)
        return 1
    log.info("agent voice channel: %s (%s)", channel.name, channel.id)

    loop = asyncio.get_running_loop()
    transcriber, speaker = Transcriber(), Speaker()
    await loop.run_in_executor(None, transcriber.load)
    await loop.run_in_executor(None, speaker.load)

    pool = SessionPool(router=Router(default_cwd="/home/bodas/data"), cwd="/home/bodas/data")
    call = VoiceCall(
        pool=pool,
        transcriber=transcriber,
        speaker=speaker,
        allowed={talker.user.id},
        key=f"voice-{channel.id}",
        log_fn=lambda m: log.info("[call] %s", m),
    )
    # The line under test. `bot.py` does exactly this on join.
    owner = _agent_owning(registry, channel.id)
    if owner is None:
        log.error("channel %s resolved to NO owning agent -- binding cannot happen", channel.id)
        return 1
    log.info("channel resolves to owner %r (session %s)", owner.name, owner.session_id[:8])
    pool.bind(call.key, owner.name, owner.session_id)

    await call.join(channel)
    talker_vc = await talker.get_guild(guild_id).get_channel(voice_id).connect()
    await asyncio.sleep(2)

    log.info("ASKING: %r", question)
    pcm = await loop.run_in_executor(None, speaker.to_discord, question)
    talker_vc.play(discord.PCMAudio(io.BytesIO(pcm)))
    while talker_vc.is_playing():
        await asyncio.sleep(0.2)

    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if call.transcript and call.transcript[-1][0] == "claude":
            break
        await asyncio.sleep(0.5)

    log.info("=" * 70)
    for who, what in call.transcript:
        log.info("%-7s %s", who + ":", what)
    log.info("=" * 70)

    answer = next((w for who, w in reversed(call.transcript) if who == "claude"), "")
    hit = expected.lower() in answer.lower()
    log.info("token %r present in the SPOKEN answer: %s", expected, "YES" if hit else "NO")
    # Two ways this check has been wrong, both worth knowing before trusting it.
    #
    # It once passed on a refusal: the agent declined to say the word and the
    # substring matched inside its explanation of why. A token in the answer is
    # not the same as the token BEING the answer.
    #
    # And it once failed on a correct answer: the agent replied through its own
    # harness's peer channel rather than as turn output, so the word never
    # travelled the path this reads. That is not a binding failure -- it is an
    # answer this harness cannot see, and reporting it as failure would be a lie
    # in the safer-sounding direction.
    #
    # The binding itself is established by something sturdier than the token:
    # whether the reply shows knowledge only the bound session could have. A
    # fresh session has no history to draw on and says so.
    log.info(
        "NOTE: a miss here means the token did not come back over VOICE. It does "
        "not by itself mean the call reached the wrong session -- read the answer "
        "and judge whether only the bound agent could have written it."
    )

    await call.leave()
    with contextlib.suppress(Exception):
        await talker_vc.disconnect(force=True)
    await pool.close()
    await listener.close()
    await talker.close()
    return 0 if hit else 1


def _agent_owning(registry: Registry, channel_id: int):
    for candidate in registry.agents.values():
        if candidate.voice_channel_id and int(candidate.voice_channel_id) == channel_id:
            return candidate
    return None


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3])))
