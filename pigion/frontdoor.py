#!/usr/bin/env python3
"""Pigion's front door for hotline.

Pigion is a Pi Zero 2 W with about 100 MiB of free RAM and a 36-day uptime. It is
the always-on machine, so it owns the address the iPhone talks to; archserver has
the GPU and the Claude CLI but is the machine that gets powered off.

This is deliberately the dumbest possible component. It authenticates the caller,
forwards the request to archserver over Tailscale, and returns the answer. It never
thinks, never caches, never holds a conversation. Standard library only -- Debian's
Python is PEP 668 externally-managed, and the whole point of putting this here
rather than a real framework is that it costs single-digit megabytes.

Why the phone points here and not straight at archserver: the URL inside an iPhone
Shortcut is painful to change, and in Phase 5 this same endpoint gains the job of
sending a magic packet and waiting for archserver to boot. Pointing the Shortcut
here from the start means that upgrade is invisible to the phone.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wake  # noqa: E402

UPSTREAM = os.environ.get("HOTLINE_UPSTREAM", "http://100.72.2.62:8788")
PORT = int(os.environ.get("HOTLINE_FRONTDOOR_PORT", "8788"))
API_KEY = os.environ.get("HOTLINE_API_KEY") or None
ALLOW = {
    item.strip()
    for item in os.environ.get("HOTLINE_ALLOW_IPS", "").split(",")
    if item.strip()
} | {"127.0.0.1", "::1"}
# Long enough to cover a real turn on archserver. The daemon there answers with
# "still working" well before this, so reaching it means something is genuinely wrong.
UPSTREAM_TIMEOUT = float(os.environ.get("HOTLINE_UPSTREAM_TIMEOUT", "180"))
HEALTH_TIMEOUT = 4.0

UPSTREAM_MAC = os.environ.get("HOTLINE_UPSTREAM_MAC", "")
WAKE_BROADCAST = os.environ.get("HOTLINE_WAKE_BROADCAST", "192.168.1.255")
WAKE_DEADLINE = float(os.environ.get("HOTLINE_WAKE_DEADLINE", "90"))

ASLEEP = (
    "The workstation isn't reachable right now, so I can't answer that. "
    "Try again in a minute."
)


def log(message: str) -> None:
    print(f"[frontdoor] {message}", file=sys.stderr, flush=True)


def upstream_awake() -> bool:
    try:
        with urllib.request.urlopen(f"{UPSTREAM}/health", timeout=HEALTH_TIMEOUT) as response:
            return bool(json.loads(response.read()).get("ok"))
    except (urllib.error.URLError, OSError, ValueError):
        return False


def wake_upstream(wait: bool = False, deadline: float | None = None) -> bool:
    """Broadcast a magic packet at archserver, optionally waiting for it to answer.

    Never verified end to end: archserver's ethernet port has no cable in it, so
    nothing has ever actually been woken by this. What is verified is that the
    right bytes leave Pigion. See pigion/wake.py.
    """
    if not UPSTREAM_MAC:
        log("no HOTLINE_UPSTREAM_MAC configured; cannot wake anything")
        return False
    try:
        if wait:
            woke = wake.wake_and_wait(
                UPSTREAM_MAC,
                f"{UPSTREAM}/health",
                WAKE_BROADCAST,
                deadline=WAKE_DEADLINE if deadline is None else deadline,
            )
            log(f"wake {'succeeded' if woke else 'timed out'} for {UPSTREAM_MAC}")
            return woke
        sent = wake.send(UPSTREAM_MAC, WAKE_BROADCAST)
        log(f"sent magic packet to {UPSTREAM_MAC} ({sent} bytes)")
    except (OSError, ValueError) as exc:
        log(f"wake failed: {exc}")
        return False
    return False


class Handler(BaseHTTPRequestHandler):
    server_version = "hotline-frontdoor/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        log(fmt % args)

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _authorised(self) -> bool:
        peer = self.client_address[0]
        if peer not in ALLOW:
            log(f"refused {self.path} from {peer}")
            self._send(403, {"error": "not an allowed source address"})
            return False
        if API_KEY is not None and self.headers.get("X-Hotline-Key") != API_KEY:
            log(f"bad key from {peer}")
            self._send(401, {"error": "bad or missing X-Hotline-Key"})
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's contract
        if self.path.split("?")[0] != "/health":
            self._send(404, {"error": "no route"})
            return
        self._send(200, {"ok": True, "role": "frontdoor", "upstream_awake": upstream_awake()})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?")[0] != "/api/v1/claude":
            self._send(404, {"error": "no route"})
            return
        if not self._authorised():
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send(400, {"error": "bad Content-Length"})
            return
        if length > 1 << 20:
            self._send(413, {"error": "body too large"})
            return
        body = self.rfile.read(length) if length else b"{}"

        if not upstream_awake():
            # Wait, rather than fire and forget: the caller is on the phone with a
            # question, and "hold on, waking it" then answering beats "try again
            # later" when the box takes forty seconds to boot.
            if not wake_upstream(wait=True):
                # 200, not 503: the Shortcut speaks whatever is in `response` and
                # has no way to render a status code, so an error status is silence.
                self._send(200, {"response": ASLEEP, "upstream": "unreachable"})
                return

        request = urllib.request.Request(
            f"{UPSTREAM}/api/v1/claude",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if API_KEY:
            request.add_header("X-Hotline-Key", API_KEY)
        request.add_header("X-Forwarded-For", self.client_address[0])
        try:
            with urllib.request.urlopen(request, timeout=UPSTREAM_TIMEOUT) as response:
                self._send(response.status, json.loads(response.read()))
        except urllib.error.HTTPError as exc:
            self._send(exc.code, {"error": f"upstream {exc.code}", "detail": exc.reason})
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            log(f"upstream failed: {exc}")
            self._send(200, {"response": ASLEEP, "upstream": f"error: {exc}"})
        except ValueError as exc:
            self._send(502, {"error": f"upstream sent non-JSON: {exc}"})


def start_sentinel() -> None:
    """Watch Discord for Bogdan writing while archserver is off, and wake it.

    In this process as a thread, not a second service: two Python interpreters on
    a 415 MB Pi costs about 12 MB for nothing, and this machine also runs
    pigion.service, which is in daily use and must not be squeezed.
    """
    token = os.environ.get("SENTINEL_BOT_TOKEN")
    user_id = os.environ.get("DISCORD_USER_ID")
    if not (token and user_id):
        log("sentinel not configured (needs SENTINEL_BOT_TOKEN, DISCORD_USER_ID); "
            "not watching for messages")
        return
    try:
        from sentinel import Sentinel
    except ImportError as exc:
        log(f"sentinel unavailable ({exc}); not watching for messages")
        return

    Sentinel(token, int(user_id), _waker(token)).start()
    log("sentinel watching for Bogdan's messages")


# One wake attempt per window, however many messages he sends while it boots:
# a burst of five messages must not become five notices and five wake threads.
MESSAGE_WAKE_COOLDOWN = float(os.environ.get("HOTLINE_MESSAGE_WAKE_COOLDOWN", "600"))
# A full boot to hotlined answering takes longer than the 90 s an iPhone
# Shortcut can afford to wait; nobody is blocked on this one.
MESSAGE_WAKE_DEADLINE = float(os.environ.get("HOTLINE_MESSAGE_WAKE_DEADLINE", "300"))


def post_message(token: str, channel_id: int, text: str) -> bool:
    """Say something in a channel as the sentinel bot. Never raises.

    Discord's API rejects requests without a User-Agent with a 403 that reads
    like a permissions problem, so it is always set.
    """
    request = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{channel_id}/messages",
        data=json.dumps({"content": text}).encode(),
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "hotline-sentinel (pigion, 1.0)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10):
            return True
    except (urllib.error.URLError, OSError) as exc:
        log(f"could not post to channel {channel_id}: {exc}")
        return False


def _waker(token: str) -> Callable[[int], None]:
    """Bogdan wrote something: if archserver is off, say so and wake it."""
    state = {"last": 0.0}
    lock = threading.Lock()

    def on_message(channel_id: int) -> None:
        with lock:
            if time.monotonic() - state["last"] < MESSAGE_WAKE_COOLDOWN:
                return
            if upstream_awake():
                return
            state["last"] = time.monotonic()

        def run() -> None:
            log(f"bogdan wrote in {channel_id} while archserver is down -- waking it")
            post_message(
                token,
                channel_id,
                "archserver is asleep, so I'm waking it now. It will be up in about 2 "
                "minutes, and the operator reads this channel's history when it starts, "
                "so your message is not lost.",
            )
            if wake_upstream(wait=True, deadline=MESSAGE_WAKE_DEADLINE):
                post_message(token, channel_id, "archserver is up. The operator is starting.")
            else:
                post_message(
                    token,
                    channel_id,
                    f"archserver did not answer within {MESSAGE_WAKE_DEADLINE:.0f} s of the wake. "
                    "It may still be booting; if nothing answers soon, it needs a hand.",
                )

        threading.Thread(target=run, name="message-wake", daemon=True).start()

    return on_message


def main() -> int:
    import logging

    # The sentinel logs through `logging`; without a handler its gateway errors
    # would vanish exactly the way hotlined's did.
    logging.basicConfig(
        level=os.environ.get("HOTLINE_LOG_LEVEL", "INFO"),
        format="[%(name)s] %(message)s",
        stream=sys.stderr,
    )
    logging.getLogger("websockets").setLevel("WARNING")
    log(f"upstream={UPSTREAM} allow={sorted(ALLOW)} key={'set' if API_KEY else 'unset'}")
    log(f"wake target={UPSTREAM_MAC or '(unset)'} broadcast={WAKE_BROADCAST}")
    start_sentinel()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log(f"listening on 0.0.0.0:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
