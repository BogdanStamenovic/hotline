"""Query strings reach the handler.

`hotline-ios` wanted a cursor on an event feed (`?since=41`), could not get one,
and moved the endpoint to POST-with-a-body rather than fork the server to carry
two integers. It was right to work around it and right to report it: the target
was split on "?" and the query half discarded, so a query parameter was not
merely unrouted, it was *unreachable* -- no handler could ever see one.

Routing stays on exact paths, which is deliberate and unchanged. The query is
simply no longer thrown away on the way past.
"""

from __future__ import annotations

import asyncio

import pytest

from hotline.httpd import Request, Server


def read(raw: bytes) -> Request:
    """Drive the real request parser over a real stream, not a stub of it."""

    async def go() -> Request:
        reader = asyncio.StreamReader()
        reader.feed_data(raw)
        reader.feed_eof()
        return await Server("127.0.0.1", 0)._read_request(reader, "test")

    return asyncio.run(go())


def get(target: str) -> Request:
    return read(f"GET {target} HTTP/1.1\r\nHost: x\r\n\r\n".encode())


def test_a_query_parameter_reaches_the_handler() -> None:
    request = get("/events?since=41")

    assert request.query == {"since": "41"}


def test_routing_still_happens_on_the_path_alone() -> None:
    """The exact-path routing was never the bug and must not change: a handler
    registered for /events has to keep matching when a cursor is appended."""
    assert get("/events?since=41&verbose=1").path == "/events"


def test_no_query_is_an_empty_mapping_not_a_none() -> None:
    """So a handler can say `request.query.get(...)` without guarding first."""
    assert get("/events").query == {}


def test_a_blank_valued_parameter_is_present_rather_than_absent() -> None:
    """ "Was this flag passed" and "what is its value" are different questions,
    and dropping a blank value conflates them."""
    assert get("/events?verbose=").query == {"verbose": ""}


def test_percent_encoding_is_decoded() -> None:
    assert get("/find?name=data%2Dd5%20now").query == {"name": "data-d5 now"}


def test_a_repeated_parameter_takes_the_last_value() -> None:
    """One value per key, last wins -- the common convention, and it keeps the
    type a plain str so handlers never have to branch on list-or-string."""
    assert get("/events?since=1&since=9").query == {"since": "9"}


@pytest.mark.parametrize("target", ["/events?", "/events?&", "/events?=x"])
def test_malformed_queries_do_not_raise(target: str) -> None:
    """A junk query must not turn a routable request into a 400 -- it is the
    handler's business whether it can use what arrived."""
    assert isinstance(get(target).query, dict)
