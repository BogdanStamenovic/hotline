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
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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


def wake_upstream() -> None:
    """Phase 5 plugs the magic packet in here.

    Left as an explicit no-op rather than omitted, so the place it belongs is
    obvious, and so the Phase 2 behaviour -- say honestly that the machine is down
    -- is what happens until then.
    """
    return None


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
            wake_upstream()
            # 200, not 503: the Shortcut speaks whatever is in `response` and has
            # no way to render a status code, so an error status is silence.
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


def main() -> int:
    log(f"upstream={UPSTREAM} allow={sorted(ALLOW)} key={'set' if API_KEY else 'unset'}")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log(f"listening on 0.0.0.0:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
