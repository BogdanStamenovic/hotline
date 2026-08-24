"""The actual call: join a Discord voice channel, listen, think out loud, answer.

The shape is a loop per speaker -- packets in, VAD segments them, Whisper turns a
segment into text, the router answers, Piper speaks it -- but three details are
what make it feel like talking to someone rather than querying a machine.

**The gate is at the sink, on user id.** py-cord hands out one stream per speaker,
so the filter goes there, before a single sample is transcribed. Filtering only by
guild or channel would mean anyone who joins the call gets a root shell on this
machine, because these sessions run with permissions bypassed and `%wheel
NOPASSWD: ALL` is in place. Everyone else's audio is dropped where it arrives.

**Dead air is narrated, not filled.** A tool call can run for thirty seconds and
every published voice-agent pattern hits this and suggests hold music. The stream
already carries `tool_use` events and, better, `task_summary` sentences written for
a person -- "Reading the nginx config", "Running the test suite". Speaking those as
they happen turns a wait into presence, and costs nothing.

**Barge-in is real.** If you start talking while it is speaking, it stops. Not at
the end of the sentence -- immediately, by clearing the playback queue. A voice
agent you cannot interrupt is exhausting, and interrupting is how people signal
that the answer has gone the wrong way.
"""

from __future__ import annotations

import array
import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any, ClassVar

import discord
import numpy as np
from discord import sinks

from .audio import SILENCE_TO_END, Segmenter, Speaker, Transcriber, stereo48_to_mono16
from .errors import HotlineError
from .fresh import Event
from .pool import SessionPool

log = logging.getLogger("hotline.voice")

FRAME_BYTES = 3840  # 20 ms of 48 kHz stereo 16-bit, what discord.AudioSource wants
SILENCE = b"\x00" * FRAME_BYTES

# How long a turn must be running before it is worth saying something. Below this
# the answer arrives first anyway and narration would just talk over it.
NARRATE_AFTER = 3.0
# Minimum gap between spoken narration lines, so a burst of tool calls does not
# become a stream of chatter.
NARRATE_EVERY = 4.0

_PATCHED = False


_undecryptable = 0


def _log_undecryptable(
    decryptor: Any,
    packet: Any,
    header: bytes,
    payload: bytes,
    rotated: bool = False,
) -> None:
    """Say what an undecryptable packet actually looked like.

    A bare `CryptoError` says nothing about which of the several things that have
    to line up -- key, associated data, CSRC list, extension, mode -- did not.
    Without this you cannot tell a stale key from a malformed header, and the two
    have opposite fixes. Rate-limited because a broken call produces fifty of
    these a second.
    """
    global _undecryptable
    _undecryptable += 1
    if _undecryptable > 12 and _undecryptable % 250:
        return
    state = getattr(decryptor.client, "_connection", None)
    dave = getattr(state, "dave_session", None)
    who = None
    if state is not None:
        who = getattr(state, "ssrc_user_map", {}).get(packet.ssrc)
    log.error(
        "undecryptable voice packet #%d%s: ssrc=%s user=%s mode=%s cc=%d ext=%s "
        "pad=%s hdr=%dB payload=%dB dave_ready=%s dave_proto=%s",
        _undecryptable,
        " (after rebuild)" if rotated else "",
        packet.ssrc,
        who,
        decryptor.mode,
        packet.cc,
        packet.extended,
        packet.padding,
        len(header),
        len(payload),
        None if dave is None else getattr(dave, "ready", "?"),
        None if state is None else getattr(state, "dave_protocol_version", None),
    )


def install_receive_fixes() -> None:
    """Repair py-cord 2.8's inbound audio path. Idempotent; call before recording.

    py-cord warns that "voice reception is currently broken due to Discord's DAVE
    protocol" (their issue #3139). That is not what is wrong. DAVE negotiates and
    the transport decrypts fine -- 54 of 56 packets in a real call. What is broken
    is the last line of the AEAD decrypt:

        return result[8:]

    The 8 is an RTP extension preamble plus one extension word, and it is stripped
    **unconditionally** -- including from packets with no extension at all, where
    it eats the first eight bytes of the Opus payload. Discord sends ext=False for
    ordinary speech, so every real audio packet decrypted perfectly and then
    decoded to digital silence. Peak amplitude 0.0000, no error anywhere, and a
    library warning pointing confidently at the wrong cause.

    Stripping the extension only when there *is* one takes the same call from
    silence to peak 0.99.

    The second fix covers `cc > 0`: py-cord builds the AEAD associated data from
    `data[:12]` and never includes the CSRC list, so any packet carrying CSRCs
    fails to decrypt outright. Two packets in that same call. Rebuilt here from
    the real header length.
    """
    global _PATCHED
    if _PATCHED:
        return

    from discord.voice.receive import reader as _reader

    def _decrypt(decryptor: Any, packet: Any) -> bytes:
        packet.adjust_rtpsize()
        nonce = packet.nonce + b"\x00" * 20

        header = bytes(packet.header)
        raw = getattr(packet, "_hotline_raw", None)
        if packet.cc and raw is not None:
            # The associated data must be the whole RTP header, CSRC list
            # included. py-cord builds it from data[:12] and never puts the CSRCs
            # back, so any packet carrying them fails outright.
            header = bytes(raw[: 12 + 4 * packet.cc]) + header[12:]

        payload = packet.decrypted_data or packet.data
        try:
            result: bytes = decryptor.box.decrypt(payload, header, nonce)
        except Exception:
            # Discord rotates the transport key when the set of participants
            # changes -- someone joining a call the bot is already sitting in.
            # py-cord has an `update_secret_key` for exactly this and **nothing
            # ever calls it**, so the decryptor keeps a stale box and every
            # subsequent packet fails to decrypt. Symptom: a call that works
            # perfectly when the bot joins last, and is stone deaf when it joins
            # first. Rebuild from the connection's current key and retry once.
            #
            # `_hotline_key` is seeded in __init__ below, so "the key changed" is
            # a real comparison. It used to be seeded only here, which meant the
            # first failure of *any* kind found None, rebuilt the box with the
            # identical key, logged "key rotated", and failed again -- a
            # confident wrong diagnosis printed on top of an unchanged bug.
            current = bytes(decryptor.client.secret_key)
            if current == getattr(decryptor, "_hotline_key", None):
                _log_undecryptable(decryptor, packet, header, payload)
                raise
            decryptor._hotline_key = current
            decryptor.box = decryptor._make_box(current)
            log.info("voice key rotated; rebuilt the decryptor")
            try:
                result = decryptor.box.decrypt(payload, header, nonce)
            except Exception:
                _log_undecryptable(decryptor, packet, header, payload, rotated=True)
                raise

        if packet.extended:
            # `update_extended_header` already returns the correct payload offset,
            # including the rtpsize adjustment -- py-cord computes it and then
            # throws it away in favour of a hardcoded 8. Eight is right only for a
            # single 32-bit extension word. Bots send one; real Discord clients
            # send several, so a human's audio decoded to a corrupted Opus stream
            # while a bot's decoded perfectly.
            result = result[packet.update_extended_header(result) :]

        if packet.padding and result:
            # RFC 3550 s5.1: the final byte counts the trailing padding, itself
            # included. Leaving it in feeds junk to Opus.
            pad = result[-1]
            if 0 < pad <= len(result):
                result = result[:-pad]

        return result

    # setattr, not assignment: mypy rejects rebinding a method, and this is
    # deliberately monkeypatching a third-party class.
    setattr(  # noqa: B010
        _reader.PacketDecryptor,
        "_decrypt_rtp_aead_xchacha20_poly1305_rtpsize",
        _decrypt,
    )

    # Seed the key the box was actually built from, so the rotation check above
    # compares against something real instead of None.
    _dec_init = _reader.PacketDecryptor.__init__

    def _keep_key(self: Any, mode: Any, secret_key: bytes, client: Any) -> None:
        _dec_init(self, mode, secret_key, client)
        self._hotline_key = bytes(secret_key)

    setattr(_reader.PacketDecryptor, "__init__", _keep_key)  # noqa: B010

    _upd = _reader.PacketDecryptor.update_secret_key

    def _upd_tracked(self: Any, secret_key: bytes) -> None:
        _upd(self, secret_key)
        self._hotline_key = bytes(secret_key)

    setattr(_reader.PacketDecryptor, "update_secret_key", _upd_tracked)  # noqa: B010

    # Keep the raw bytes: RTPPacket.__init__ slices them away immediately, and
    # the CSRC list is needed to rebuild the associated data above.
    from discord.voice.packets import rtp as _rtp

    _rtp_init = _rtp.RTPPacket.__init__

    def _keep_raw(packet: Any, data: bytes) -> None:
        packet._hotline_raw = data
        _rtp_init(packet, data)

    setattr(_rtp.RTPPacket, "__init__", _keep_raw)  # noqa: B010

    # A single corrupt frame must not end the call. py-cord lets OpusError escape
    # `pop_data`, which kills the packet-router thread, whose `finally` calls
    # `stop_recording()` -- so one damaged packet permanently deafens the bot with
    # no way back. That is unacceptable even with perfect decoding: real networks
    # lose and mangle packets. Skip the frame and keep listening.
    from discord import opus as _opus

    _pop = _opus.PacketDecoder.pop_data

    def _safe_pop(decoder: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return _pop(decoder, *args, **kwargs)
        except _opus.OpusError as exc:
            log.debug("dropped a corrupt opus frame: %s", exc)
            return None

    setattr(_opus.PacketDecoder, "pop_data", _safe_pop)  # noqa: B010

    # On hangup py-cord's router calls stop_recording() from its own `finally`,
    # after we have already stopped it, and the resulting RecordingException is
    # raised on a daemon thread with a full traceback. Nothing is wrong -- but a
    # clean hangup that prints a traceback trains you to ignore tracebacks.
    from discord.voice.client import VoiceClient as _VC

    _stop = _VC.stop_recording

    def _quiet_stop(client: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return _stop(client, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            log.debug("stop_recording during teardown: %s", exc)
            return None

    setattr(_VC, "stop_recording", _quiet_stop)  # noqa: B010
    _PATCHED = True
    log.info("installed py-cord receive fixes (pycord#3139 is a red herring)")


class StreamSource(discord.AudioSource):
    """A queue of PCM that can be emptied instantly.

    py-cord pulls 20 ms frames from `read()` on its own thread. Making that a
    queue rather than a file is what allows barge-in: dropping everything queued
    stops the voice mid-word, which is the only interruption that feels like an
    interruption.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._lock = asyncio.Lock()
        self.finished = False

    def push(self, pcm: bytes) -> None:
        self._buffer.extend(pcm)

    def clear(self) -> int:
        dropped = len(self._buffer)
        self._buffer.clear()
        return dropped

    @property
    def pending_seconds(self) -> float:
        return len(self._buffer) / (FRAME_BYTES * 50)

    def read(self) -> bytes:
        if not self._buffer:
            # Silence rather than b"": returning empty tells py-cord the source is
            # exhausted and it tears the player down, which would mean restarting
            # a player for every sentence.
            return SILENCE
        frame = bytes(self._buffer[:FRAME_BYTES])
        del self._buffer[:FRAME_BYTES]
        if len(frame) < FRAME_BYTES:
            frame += b"\x00" * (FRAME_BYTES - len(frame))
        return frame

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        self._buffer.clear()


class GatedSink(sinks.Sink):
    """Receives per-user PCM and forwards only the permitted speaker's.

    `write` runs on py-cord's decoder thread, so nothing here may block or touch
    the event loop directly -- it hands the bytes over with
    `call_soon_threadsafe` and gets out of the way.

    The odd attributes below exist because py-cord 2.8.1's receive path is
    half-migrated and `discord.sinks.Sink` no longer satisfies it. py-cord warns
    that "voice reception is currently broken due to Discord's DAVE protocol"
    (their issue #3139) -- but that warning is broader than the truth. DAVE
    decryption works: RTP packets arrive decrypted and are handed to the classic
    `sink.write(data, source)` in `PacketRouter`. What is actually missing is
    three attributes the newer machinery expects and the old base class never
    grew:

      __sink_listeners__   the separate SinkEventRouter registers speaking
                           start/stop callbacks from it; empty is fine, this
                           does its own VAD and never used those events
      walk_children()      the same router walks nested sinks; there are none
      is_opus()            the decoder asks whether to hand over Opus or PCM.
                           False means "decode it for me", which is what we want

    Without them `start_recording` raises AttributeError before a single packet
    is delivered, which looks exactly like the advertised DAVE breakage and is
    not.
    """

    # See the class docstring. Not decoration -- reception fails without these.
    __sink_listeners__: ClassVar[list[tuple[str, str]]] = []

    def walk_children(self) -> list[GatedSink]:
        return []

    def is_opus(self) -> bool:
        return False

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        allowed: set[int],
        on_audio: Callable[[int, bytes], None],
        on_rejected: Callable[[int], None],
    ) -> None:
        super().__init__()
        self.loop = loop
        self.allowed = allowed
        self.on_audio = on_audio
        self.on_rejected = on_rejected
        self._warned: set[int] = set()
        self.packets = 0

    def write(self, data: object, user: object) -> None:
        """py-cord 2.8 hands over a `VoiceData` and a Member, not bytes and an int.

        The old signature (`bytes, int`) is what every sink example still shows,
        and silently doing the wrong thing with it is why this looked like the
        DAVE breakage for so long. Both shapes are accepted so a py-cord upgrade
        in either direction does not break the call.
        """
        self.packets += 1
        pcm = getattr(data, "pcm", None)
        if pcm is None and isinstance(data, (bytes, bytearray)):
            pcm = bytes(data)
        speaker = getattr(user, "id", user)
        if not isinstance(speaker, int) or not pcm:
            return

        if speaker not in self.allowed:
            if speaker not in self._warned:
                self._warned.add(speaker)
                self.loop.call_soon_threadsafe(self.on_rejected, speaker)
            return
        self.loop.call_soon_threadsafe(self.on_audio, speaker, bytes(pcm))

    def cleanup(self) -> None:
        self.finished = True


class VoiceCall:
    """One live call in one channel."""

    def __init__(
        self,
        pool: SessionPool,
        transcriber: Transcriber,
        speaker: Speaker,
        allowed: set[int],
        key: str,
        log_fn: Callable[[str], None] | None = None,
    ) -> None:
        self.pool = pool
        self.transcriber = transcriber
        self.speaker = speaker
        self.allowed = allowed
        self.key = key
        self.log = log_fn or (lambda message: log.info(message))
        self.client: discord.VoiceClient | None = None
        self.source = StreamSource()
        self.segmenters: dict[int, Segmenter] = {}
        self.transcript: list[tuple[str, str]] = []
        self._queue: asyncio.Queue[tuple[int, bytes]] = asyncio.Queue()
        self._tasks: list[asyncio.Task[None]] = []
        self._busy = False
        self._speaking_since = 0.0
        self._frames = 0
        self._last_audio: dict[int, float] = {}

    # ---- lifecycle ------------------------------------------------------

    async def join(self, channel: discord.VoiceChannel) -> None:
        loop = asyncio.get_running_loop()
        self.client = await channel.connect()
        self.client.play(self.source)
        sink = GatedSink(loop, self.allowed, self._on_audio, self._on_rejected)
        # py-cord 2.8 never calls `sink.init(vc)` from `start_recording`, and the
        # opus decoder asserts on `sink.client`. Without this every packet dies in
        # an AssertionError on the router thread, which then tears down recording
        # entirely -- and looks, from outside, exactly like the advertised DAVE
        # breakage.
        install_receive_fixes()
        sink.init(self.client)
        self.client.start_recording(sink, self._on_recording_done)
        self._tasks.append(asyncio.create_task(self._consume()))
        self._tasks.append(asyncio.create_task(self._close_stale_utterances()))
        self.log(f"joined {channel.name}; listening only to {sorted(self.allowed)}")

    async def leave(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        if self.client is not None:
            with contextlib.suppress(Exception):
                self.client.stop_recording()
            with contextlib.suppress(Exception):
                await self.client.disconnect(force=True)
            self.client = None

    def _on_recording_done(self, *args: object) -> None:
        """py-cord calls this synchronously from a worker thread.

        Declaring it `async` produced "coroutine was never awaited" and, worse,
        meant the router's teardown path silently did nothing.
        """
        self.log("recording stopped")

    # ---- receiving ------------------------------------------------------

    def _on_rejected(self, user: int) -> None:
        """Someone who is not Bogdan is talking. Say so once, then stay silent.

        Worth surfacing rather than dropping quietly: from the caller's side an
        ignored voice is indistinguishable from a broken bot.
        """
        self.log(f"ignoring audio from user {user} (not in the allowed set)")

    def _on_audio(self, user: int, pcm: bytes) -> None:
        self._last_audio[user] = time.monotonic()
        self._queue.put_nowait((user, pcm))

    async def _close_stale_utterances(self) -> None:
        """End an utterance when the packets simply stop.

        Discord does not keep sending silence once you stop talking -- the stream
        goes away entirely. The segmenter ends an utterance on trailing *silence*,
        so with nothing arriving it would wait forever holding a complete
        sentence. This is what actually closes almost every real utterance.
        """
        while True:
            await asyncio.sleep(0.2)
            now = time.monotonic()
            for user, segmenter in list(self.segmenters.items()):
                if not segmenter.speaking:
                    continue
                if now - self._last_audio.get(user, 0.0) < SILENCE_TO_END:
                    continue
                utterance = segmenter.flush()
                if utterance is not None:
                    self._tasks.append(
                        asyncio.create_task(self._handle(utterance.audio, utterance.seconds))
                    )

    async def _consume(self) -> None:
        try:
            await self._consume_loop()
        except asyncio.CancelledError:
            raise
        except Exception:
            # A bare create_task swallows this until garbage collection, which is
            # how a dead consumer looked like "the audio never arrived" for an
            # hour. Never again.
            log.exception("voice consumer died")
            raise

    async def _consume_loop(self) -> None:
        while True:
            user, pcm = await self._queue.get()
            mono = stereo48_to_mono16(pcm)
            if mono.size == 0:
                continue
            self._frames += 1
            segmenter = self.segmenters.setdefault(user, Segmenter())

            # Barge-in: any speech at all while we are talking stops us. Checked
            # before segmentation finishes, because waiting for a complete
            # utterance would mean talking over the whole interruption.
            if self.source.pending_seconds > 0.2 and self._is_speech(mono):
                dropped = self.source.clear()
                if dropped:
                    self.log(f"barge-in: dropped {dropped / (FRAME_BYTES * 50):.1f}s of speech")

            for utterance in segmenter.feed(mono):
                asyncio.create_task(self._handle(utterance.audio, utterance.seconds))

    def _is_speech(self, mono: np.ndarray) -> bool:
        """Cheap energy gate for barge-in. Deliberately not the VAD: this runs on
        every packet, and being slightly wrong costs an unnecessary pause, not a
        wrong answer."""
        if mono.size == 0:
            return False
        return bool(np.sqrt(np.mean(mono.astype(np.float64) ** 2)) > 0.02)

    # ---- one turn -------------------------------------------------------

    async def _handle(self, audio: np.ndarray, seconds: float) -> None:
        loop = asyncio.get_running_loop()
        began = time.monotonic()
        text = await loop.run_in_executor(None, self.transcriber.transcribe, audio)
        text = text.strip()
        if not text or len(text) < 2:
            return
        self.transcript.append(("you", text))
        self.log(f"heard ({seconds:.1f}s, stt {time.monotonic() - began:.2f}s): {text!r}")

        if self._busy:
            # One turn at a time. Discarding is better than queueing: by the time
            # the first finishes, a spoken follow-up is usually stale.
            await self.say("Hang on, still working on the last one.")
            return

        self._busy = True
        turn_started = time.monotonic()
        spoken: list[str] = []
        last_narration = [turn_started]

        def narrate(event: Event) -> None:
            if event.kind not in ("tool", "summary"):
                return
            now = time.monotonic()
            if now - turn_started < NARRATE_AFTER or now - last_narration[0] < NARRATE_EVERY:
                return
            last_narration[0] = now
            spoken.append(event.detail)
            asyncio.run_coroutine_threadsafe(self.say(event.detail), loop)

        try:
            _route, reply = await self.pool.ask(self.key, text, narrator=narrate, timeout=900.0)
            answer = reply.text
            if reply.notice:
                answer = f"Heads up, {reply.notice}. {answer}"
        except HotlineError as exc:
            answer = f"That didn't work. {exc}"
        except Exception as exc:
            log.exception("voice turn failed")
            answer = f"Something broke on my side. {type(exc).__name__}."
        finally:
            self._busy = False

        self.transcript.append(("claude", answer))
        self.log(f"answered in {time.monotonic() - turn_started:.1f}s ({len(answer)} chars)")
        await self.say(speakable(answer))

    # ---- speaking -------------------------------------------------------

    async def say(self, text: str) -> None:
        if not text.strip():
            return
        loop = asyncio.get_running_loop()
        pcm = await loop.run_in_executor(None, self.speaker.to_discord, text)
        self.source.push(pcm)
        self._speaking_since = time.monotonic()


def speakable(text: str, limit: int = 1200) -> str:
    """Turn a written answer into something worth listening to.

    Markdown read aloud is unbearable -- "hash hash Results, star star three star
    star failures" -- and a model told to be brief still reaches for a bullet list.
    This is a last line of defence over the system prompt, not a replacement for it.
    """
    lines: list[str] = []
    in_code = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("```"):
            in_code = not in_code
            if in_code:
                lines.append("Here's the code, I'll put it in the channel.")
            continue
        if in_code:
            continue
        line = line.lstrip("#").strip()
        if line.startswith(("- ", "* ", "+ ")):
            line = line[2:].strip()
        line = line.replace("**", "").replace("`", "")
        if line:
            lines.append(line)

    spoken = " ".join(lines).strip()
    if len(spoken) <= limit:
        return spoken
    cut = spoken.rfind(". ", 0, limit)
    if cut < limit // 2:
        cut = limit
    return spoken[: cut + 1] + " There's more — I've put the rest in the channel."


def pcm_to_array(pcm: bytes) -> array.array:
    """Only used by tests and debugging; kept here so the shape is documented."""
    values = array.array("h")
    values.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    return values
