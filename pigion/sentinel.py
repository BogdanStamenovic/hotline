"""Pigion's ear on Discord: notice Bogdan joining the voice channel, wake the box.

archserver has the GPU and gets powered off. Pigion has 36 days of uptime and
about 80 MB of free RAM. So the thing that has to be awake to notice a call is
here, and it does exactly one job.

**Hand-rolled gateway client, deliberately.** A full `discord.py` with default
caching is 60-150 MB resident, which this machine does not have. This subscribes
to `GUILD_VOICE_STATES` alone (intent `1 << 7`), caches nothing, and ignores every
event but `VOICE_STATE_UPDATE`. ~25 MB, and it runs as a thread inside
`frontdoor.py` rather than a second process -- two Python interpreters on a 415 MB
Pi is roughly 12 MB wasted for no reason.

It never thinks. It receives, wakes, and gets out of the way.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Callable

import websockets

log = logging.getLogger("sentinel")

GATEWAY = "wss://gateway.discord.gg/?v=10&encoding=json"
GUILD_VOICE_STATES = 1 << 7

OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11


class Sentinel:
    def __init__(
        self,
        token: str,
        user_id: int,
        voice_channel_id: int,
        on_join: Callable[[], None],
        on_leave: Callable[[], None] | None = None,
    ) -> None:
        self.token = token
        self.user_id = user_id
        self.voice_channel_id = voice_channel_id
        self.on_join = on_join
        self.on_leave = on_leave
        self._present = False
        self._seq: int | None = None
        self._stop = threading.Event()

    # ---- the gateway ----------------------------------------------------

    async def _heartbeat(self, socket: object, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            await socket.send(json.dumps({"op": OP_HEARTBEAT, "d": self._seq}))  # type: ignore[attr-defined]

    async def _session(self) -> None:
        async with websockets.connect(GATEWAY, max_size=2**18) as socket:
            hello = json.loads(await socket.recv())
            interval = hello["d"]["heartbeat_interval"] / 1000.0
            beat = asyncio.create_task(self._heartbeat(socket, interval))

            await socket.send(
                json.dumps(
                    {
                        "op": OP_IDENTIFY,
                        "d": {
                            "token": self.token,
                            "intents": GUILD_VOICE_STATES,
                            "properties": {"os": "linux", "browser": "hotline", "device": "pigion"},
                        },
                    }
                )
            )
            log.info("identified; watching voice channel %s for user %s",
                     self.voice_channel_id, self.user_id)

            try:
                async for raw in socket:
                    payload = json.loads(raw)
                    if payload.get("s") is not None:
                        self._seq = payload["s"]
                    op = payload.get("op")
                    if op == OP_DISPATCH and payload.get("t") == "VOICE_STATE_UPDATE":
                        self._on_voice_state(payload.get("d") or {})
                    elif op in (OP_RECONNECT, OP_INVALID_SESSION):
                        log.info("gateway asked us to reconnect (op %s)", op)
                        return
            finally:
                beat.cancel()

    def _on_voice_state(self, data: dict) -> None:
        try:
            user = int(data.get("user_id") or 0)
        except (TypeError, ValueError):
            return
        if user != self.user_id:
            return
        channel = data.get("channel_id")
        here = channel is not None and int(channel) == self.voice_channel_id

        # Edge-triggered. Discord sends VOICE_STATE_UPDATE for mute, deafen, video
        # and streaming too, and firing a wake on every one of those would send a
        # magic packet each time he touched his microphone.
        if here and not self._present:
            self._present = True
            log.info("bogdan joined the voice channel")
            try:
                self.on_join()
            except Exception:
                log.exception("on_join failed")
        elif not here and self._present:
            self._present = False
            log.info("bogdan left the voice channel")
            if self.on_leave:
                try:
                    self.on_leave()
                except Exception:
                    log.exception("on_leave failed")

    async def _run(self) -> None:
        delay = 5.0
        while not self._stop.is_set():
            try:
                await self._session()
                delay = 5.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("gateway session ended: %s; retrying in %.0fs", exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 300.0)

    def start(self) -> threading.Thread:
        """Run the gateway in its own thread with its own loop.

        The front door is a threading HTTP server, so there is no event loop to
        join. A thread keeps the two entirely independent: the proxy must keep
        answering even if Discord is unreachable, which is exactly when archserver
        being down matters most.
        """

        def target() -> None:
            asyncio.run(self._run())

        thread = threading.Thread(target=target, name="sentinel", daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()
