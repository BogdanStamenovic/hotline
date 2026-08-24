"""Splitting a message so Discord will actually deliver all of it.

Discord's hard limit is 2000 characters and it does not negotiate: an oversized
message is rejected, and a client that truncates instead delivers a message that
*looks* complete and is not.

This was not theoretical. The pager truncated a long status message at 1900
characters, Bogdan read it, and had no way to know the end was missing -- he found
out only because the last sentence made no sense. Silent truncation is worse than
an error, because the reader believes they have the whole thing.

Lives in its own module because both the Discord bot and the pager need it, and
the pager must keep working without py-cord installed.
"""

from __future__ import annotations

MAX_MESSAGE = 1900  # Discord's limit is 2000; leave headroom for a part marker


def chunk(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    """Split a long answer on paragraph, then line, then hard boundaries.

    Discord silently rejects an oversized message, so an answer that is merely long
    would otherwise vanish entirely -- the worst possible failure for something
    whose only job is to deliver an answer.
    """
    text = text.strip() or "(no answer)"
    parts: list[str] = []
    while len(text) > limit:
        window = text[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 2:
            cut = window.rfind("\n")
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts
