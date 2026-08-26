"""A very small HTTP/1.1 server, written rather than imported.

This serves exactly two routes on a private tailnet. Pulling in a web framework
for that would add a dependency to a package that otherwise has none, and would
put a large piece of machinery Bogdan has not read between the phone and a shell
running with bypassed permissions. Roughly a hundred lines he can hold in his head
is the better trade -- and it keeps `aiohttp` confined to the optional Discord
extra.

It is deliberately strict: it speaks HTTP/1.1, closes every connection, caps the
request line, header block and body, and refuses anything it does not recognise.
No keep-alive, no chunked encoding, no pipelining.
"""

from __future__ import annotations

import asyncio
import json
import traceback
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from urllib.parse import parse_qs

MAX_LINE = 8192
MAX_HEADERS = 64
MAX_BODY = 1 << 20  # 1 MiB; a dictated sentence is a few hundred bytes
READ_TIMEOUT = 30.0

STATUS_TEXT = {
    200: "OK",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    408: "Request Timeout",
    413: "Payload Too Large",
    500: "Internal Server Error",
    503: "Service Unavailable",
    504: "Gateway Timeout",
}


@dataclass
class Request:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes
    peer: str
    # The query string, already parsed. It used to be split off the target and
    # thrown away, so no handler could ever see a query parameter -- routing is on
    # exact paths, which is right, but that meant `?since=41` was not merely
    # unrouted, it was unreachable. Found by `hotline-ios`, which wanted a cursor
    # on an event feed, could not get one, and worked around it rather than
    # forking the server to carry two integers.
    query: dict[str, str] = field(default_factory=dict)

    def json(self) -> dict[str, object]:
        if not self.body:
            return {}
        try:
            parsed = json.loads(self.body)
        except ValueError as exc:
            raise HttpError(400, f"body is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise HttpError(400, "body must be a JSON object")
        return parsed


class HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


Handler = Callable[[Request], Awaitable[tuple[int, dict[str, object]]]]


class Server:
    """`host` may be one address or several.

    Several matters because "reachable from his phone" and "reachable from this
    box" are different addresses and the answer is not `0.0.0.0`. Binding the
    tailnet address alone left every local caller -- a hook, a CLI, the
    statusline wrapper -- unable to reach a daemon running on the same machine;
    binding the wildcard would have fixed that by also exposing it to whatever
    wifi the machine is on. `asyncio.start_server` takes a list of hosts and
    opens one listener per address in a single `asyncio.Server`, so two explicit
    binds cost nothing and change no security posture.
    """

    def __init__(
        self,
        host: str | Sequence[str],
        port: int,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.hosts = [host] if isinstance(host, str) else list(host)
        self.host = self.hosts[0] if self.hosts else ""
        self.port = port
        self.routes: dict[tuple[str, str], Handler] = {}
        self.log = log or (lambda _m: None)
        self._server: asyncio.Server | None = None
        self.bound: list[str] = []
        """Addresses actually listening, filled by `start()`.

        Not the same list as `hosts`, and the difference is load-bearing: see
        the note in `start()`."""

    def route(self, method: str, path: str) -> Callable[[Handler], Handler]:
        def register(handler: Handler) -> Handler:
            self.routes[(method.upper(), path)] = handler
            return handler

        return register

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle, self.hosts if len(self.hosts) > 1 else self.host, self.port
        )
        # Report what is listening, not what was asked for. Given a *list*,
        # `asyncio.start_server` binds whatever it can and only raises if every
        # address fails -- so a request for [tailnet, loopback] where the tailnet
        # address does not exist yet (tailscaled still starting, at boot)
        # succeeds, binds loopback alone, and raised nothing. Logging
        # `self.hosts` then stated the daemon was reachable on an address it had
        # never bound. Do not "simplify" this back to self.hosts.
        self.bound = sorted({s.getsockname()[0] for s in (self._server.sockets or ())})
        where = ", ".join(f"http://{h}:{self.port}" for h in self.bound)
        self.log(f"listening on {where}")

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = "?"
        try:
            sock = writer.get_extra_info("peername")
            if sock:
                peer = sock[0]
            request = await asyncio.wait_for(self._read_request(reader, peer), READ_TIMEOUT)
            handler = self.routes.get((request.method, request.path))
            if handler is None:
                allowed = {m for m, p in self.routes if p == request.path}
                if allowed:
                    raise HttpError(405, f"{request.path} accepts {', '.join(sorted(allowed))}")
                raise HttpError(404, f"no route for {request.path}")
            status, payload = await handler(request)
        except TimeoutError:
            status, payload = 408, {"error": "timed out reading the request"}
        except HttpError as exc:
            status, payload = exc.status, {"error": exc.message}
        except (ConnectionResetError, BrokenPipeError):
            writer.close()
            return
        except Exception as exc:  # noqa: BLE001 - a handler bug must not kill the server
            self.log(f"handler crashed: {exc}\n{traceback.format_exc()}")
            status, payload = 500, {"error": f"{type(exc).__name__}: {exc}"}

        try:
            await self._write_response(writer, status, payload)
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ConnectionResetError):
                pass

    async def _read_request(self, reader: asyncio.StreamReader, peer: str) -> Request:
        line = await reader.readline()
        if not line:
            raise HttpError(400, "empty request")
        if len(line) > MAX_LINE:
            raise HttpError(413, "request line too long")
        parts = line.decode("latin-1").rstrip("\r\n").split()
        if len(parts) != 3:
            raise HttpError(400, "malformed request line")
        method, target, _version = parts
        path, _, raw_query = target.partition("?")
        # `keep_blank_values` so `?verbose=` is present-and-empty rather than
        # absent: a handler asking "was this flag passed" gets a different answer
        # from one asking "what is its value", and dropping it conflates them.
        query = {k: v[-1] for k, v in parse_qs(raw_query, keep_blank_values=True).items()}

        headers: dict[str, str] = {}
        for _ in range(MAX_HEADERS):
            raw = await reader.readline()
            if len(raw) > MAX_LINE:
                raise HttpError(413, "header too long")
            if raw in (b"\r\n", b"\n", b""):
                break
            name, _, value = raw.decode("latin-1").partition(":")
            headers[name.strip().lower()] = value.strip()
        else:
            raise HttpError(413, "too many headers")

        length = 0
        if "content-length" in headers:
            try:
                length = int(headers["content-length"])
            except ValueError as exc:
                raise HttpError(400, "bad Content-Length") from exc
        if length > MAX_BODY:
            raise HttpError(413, f"body over {MAX_BODY} bytes")
        if headers.get("transfer-encoding", "").lower() == "chunked":
            raise HttpError(400, "chunked bodies are not supported")

        body = await reader.readexactly(length) if length else b""
        return Request(method.upper(), path, headers, body, peer, query)

    async def _write_response(
        self, writer: asyncio.StreamWriter, status: int, payload: dict[str, object]
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        head = (
            f"HTTP/1.1 {status} {STATUS_TEXT.get(status, 'Unknown')}\r\n"
            f"Content-Type: application/json; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "Cache-Control: no-store\r\n"
            "\r\n"
        ).encode()
        writer.write(head + body)
        await writer.drain()
