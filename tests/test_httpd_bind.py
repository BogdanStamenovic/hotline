"""`Server.bound` is what is listening, not what was asked for.

Given a *list* of hosts, `asyncio.start_server` binds whatever it can and raises
only when every address fails. Loopback effectively always binds, so a request
for `[tailnet, loopback]` where the tailnet address does not exist yet -- the
normal state at boot, before tailscaled is up -- succeeds while binding loopback
alone, and raises nothing.

`start()` used to log `self.hosts`, so in exactly that case the daemon announced
itself on an address it had never bound and no line anywhere said otherwise.
`hotline-ios` then serves a phone that cannot reach it while every local probe
passes. These tests pin the log and the attribute to the sockets.
"""

from __future__ import annotations

import asyncio

import pytest

from hotline.httpd import Server

# In 100.64.0.0/10 (the CGNAT range tailscale uses) so it reads as a plausible
# tailnet peer, and not routable off this box either way.
ABSENT = "100.72.99.99"


def free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_bound_omits_an_address_that_did_not_bind() -> None:
    port = free_port()
    lines: list[str] = []
    server = Server([ABSENT, "127.0.0.1"], port, log=lines.append)

    async def run() -> None:
        await server.start()
        try:
            assert server.bound == ["127.0.0.1"]
            # The request is preserved -- callers need it to notice the gap.
            assert server.hosts == [ABSENT, "127.0.0.1"]
        finally:
            await server.close()

    asyncio.run(run())

    assert lines, "start() logged nothing"
    assert ABSENT not in lines[0], f"log claims an unbound address: {lines[0]!r}"
    assert f"http://127.0.0.1:{port}" in lines[0]


def test_bound_lists_every_address_that_did_bind() -> None:
    port = free_port()
    server = Server(["127.0.0.1", "127.0.0.2"], port)

    async def run() -> None:
        await server.start()
        try:
            assert server.bound == ["127.0.0.1", "127.0.0.2"]
        finally:
            await server.close()

    asyncio.run(run())


def test_still_raises_when_nothing_binds() -> None:
    """The one case asyncio does report must keep reporting."""
    server = Server([ABSENT], free_port())

    async def run() -> None:
        with pytest.raises(OSError):
            await server.start()

    asyncio.run(run())
