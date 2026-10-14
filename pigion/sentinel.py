"""Pigion's ear on Discord: notice Bogdan writing while archserver is off, wake it.

archserver has the GPU and gets powered off. Pigion is always on with about 80 MB
of free RAM. So the thing that has to be awake to notice him is here, and it does
exactly one job.

**Hand-rolled gateway client, deliberately.** A full `discord.py` with default
caching is 60-150 MB resident, which this machine does not have. This subscribes
to GUILD_MESSAGES (`1 << 9`) and DIRECT_MESSAGES (`1 << 12`) alone, caches
nothing, and ignores every event but `MESSAGE_CREATE`. Neither intent is
privileged: the author id arrives without MESSAGE_CONTENT, and the content is
never needed, because a message from him is the whole signal. It runs as a thread
inside `frontdoor.py` rather than a second process.

Until 10-14 it watched him joining a voice channel instead. He dropped that:
cvoice places real phone calls now, so a voice join is no longer how he calls.

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
GUILD_MESSAGES = 1 << 9
DIRECT_MESSAGES = 1 << 12

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
        on_message: Callable[[int], None],
    ) -> None:
        self.token = token
        self.user_id = user_id
        self.on_message = on_message
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
                            "intents": GUILD_MESSAGES | DIRECT_MESSAGES,
                            "properties": {"os": "linux", "browser": "hotline", "device": "pigion"},
                        },
                    }
                )
            )
            log.info("identified; watching for messages from user %s", self.user_id)

            try:
                async for raw in socket:
                    payload = json.loads(raw)
                    if payload.get("s") is not None:
                        self._seq = payload["s"]
                    op = payload.get("op")
                    if op == OP_DISPATCH and payload.get("t") == "MESSAGE_CREATE":
                        self._on_message(payload.get("d") or {})
                    elif op in (OP_RECONNECT, OP_INVALID_SESSION):
                        log.info("gateway asked us to reconnect (op %s)", op)
                        return
            finally:
                beat.cancel()

    def _on_message(self, data: dict) -> None:
        try:
            author = int((data.get("author") or {}).get("id") or 0)
            channel = int(data.get("channel_id") or 0)
        except (TypeError, ValueError):
            return
        if author != self.user_id or not channel:
            return
        try:
            self.on_message(channel)
        except Exception:
            log.exception("on_message failed")

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
