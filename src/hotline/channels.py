"""Creating and deleting an agent's Discord channels.

REST-only and synchronous, for the same reason the pager is: this has to work
from any process on the machine, including `hotline --done` run by an agent in
its own shell, and including when `hotlined` is not running. Routing it through
the live py-cord connection would make an agent's ability to close its own
channel depend on the daemon being up.

**Measured limits**, rather than the ones I assumed. A channel create returns
`x-ratelimit-limit: 2000` against `x-ratelimit-reset-after: ~86400`, so the
budget is two thousand channel operations per guild per day -- generous enough
that a fleet spinning up will not notice it. Discord's hard ceiling of 500
channels per guild is the constraint that will bite first, and it is the one
worth watching.

Deleting a channel is irreversible and takes its history with it. That is the
behaviour Bogdan asked for -- channels are disposable, the handoff is the record
-- so the safety here is narrow and deliberate: this refuses to delete anything
it cannot confirm is an agent channel, so a bug in the caller cannot take out
`#general`.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

from .errors import HotlineError

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/BogdanStamenovic/hotline, 1.0)"

TEXT, VOICE, CATEGORY = 0, 2, 4

# Every channel hotline owns carries this prefix. It is the only thing standing
# between a caller bug and someone's real channels, so deletion checks it rather
# than trusting an id handed in from elsewhere.
PREFIX = "agent-"

_SLUG = re.compile(r"[^a-z0-9-]+")


def slug(name: str) -> str:
    """Discord lowercases and rewrites channel names anyway; do it ourselves.

    Doing it here means the name we store is the name Discord will show, so
    looking a channel up by name later actually finds it.
    """
    cleaned = _SLUG.sub("-", name.strip().lower()).strip("-")
    # Test the slug, not the concatenation -- `PREFIX + ""` is still truthy, so
    # checking the result would let a name of pure punctuation become a bare
    # "agent-", and every such agent would then collide on one channel.
    return (PREFIX + (cleaned or "unnamed"))[:100]


class Channels:
    def __init__(self, token: str, guild_id: int, timeout: float = 20.0) -> None:
        self.token = token
        self.guild_id = guild_id
        self.timeout = timeout

    # ---- transport -------------------------------------------------------

    def _call(self, path: str, method: str = "GET", body: dict | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"{API}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bot {self.token}",
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
            },
        )
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < 3:
                    # Respect Discord's own number rather than guessing a backoff.
                    try:
                        wait = float(json.loads(exc.read()).get("retry_after", 1.0))
                    except (ValueError, OSError):
                        wait = float(exc.headers.get("retry-after") or 1.0)
                    time.sleep(min(wait + 0.25, 30.0))
                    continue
                detail = exc.read().decode(errors="replace")[:200]
                raise HotlineError(f"discord {method} {path} failed ({exc.code}): {detail}") from exc
            except OSError as exc:
                raise HotlineError(f"discord {method} {path} unreachable: {exc}") from exc
        raise HotlineError(f"discord {method} {path} rate-limited repeatedly")

    # ---- queries ---------------------------------------------------------

    def all(self) -> list[dict]:
        result = self._call(f"/guilds/{self.guild_id}/channels")
        return result if isinstance(result, list) else []

    def find(self, name: str) -> dict | None:
        wanted = slug(name)
        for channel in self.all():
            if channel.get("name") == wanted:
                return channel
        return None

    def owned(self) -> list[dict]:
        """Every channel hotline created, by prefix."""
        return [c for c in self.all() if str(c.get("name", "")).startswith(PREFIX)]

    def category(self, name: str) -> int | None:
        for channel in self.all():
            if channel.get("type") == CATEGORY and channel.get("name") == name:
                return int(channel["id"])
        return None

    # ---- lifecycle -------------------------------------------------------

    def create_text(self, name: str, topic: str = "", parent_id: int | None = None) -> int:
        """Create an agent's text channel, or return the one that already exists.

        Idempotent on purpose: re-declaring is a retask, and an agent that
        reframes its work must not end up with two channels.
        """
        existing = self.find(name)
        if existing is not None and existing.get("type") == TEXT:
            return int(existing["id"])
        body: dict[str, Any] = {"name": slug(name), "type": TEXT}
        if topic:
            body["topic"] = topic[:1024]
        if parent_id:
            body["parent_id"] = str(parent_id)
        return int(self._call(f"/guilds/{self.guild_id}/channels", "POST", body)["id"])

    def create_voice(self, name: str, parent_id: int | None = None) -> int:
        """Voice is created lazily, only when an agent actually needs to speak.

        One RTX 4060 runs one Whisper and one Piper, and Bogdan can only be in one
        voice channel at a time, so a channel per agent standing open permanently
        would be a room full of doors nobody can walk through.
        """
        existing = self.find(name)
        if existing is not None and existing.get("type") == VOICE:
            return int(existing["id"])
        body: dict[str, Any] = {"name": slug(name), "type": VOICE}
        if parent_id:
            body["parent_id"] = str(parent_id)
        return int(self._call(f"/guilds/{self.guild_id}/channels", "POST", body)["id"])

    def retopic(self, channel_id: int, topic: str) -> None:
        """Keep the channel description in step with an edited task.

        The task is editable whenever, and a channel whose topic still describes
        the job the agent stopped doing an hour ago is worse than no topic.
        """
        self._call(f"/channels/{channel_id}", "PATCH", {"topic": topic[:1024]})

    def delete(self, channel_id: int, force: bool = False) -> bool:
        """Delete a channel hotline owns. Refuses anything else.

        A channel delete is irreversible and takes every message with it. Bogdan
        chose that -- channels are disposable and the handoff is the record -- so
        the guard is not about second-guessing him, it is about making sure a bug
        in a caller cannot reach `#general`.
        """
        if not force:
            for channel in self.all():
                if int(channel["id"]) == int(channel_id):
                    if not str(channel.get("name", "")).startswith(PREFIX):
                        raise HotlineError(
                            f"refusing to delete #{channel.get('name')}: not an agent channel"
                        )
                    break
            else:
                return False  # already gone
        self._call(f"/channels/{channel_id}", "DELETE")
        return True


def from_env(env: dict[str, str] | None = None) -> Channels | None:
    """Build a manager from the environment, or None if Discord is not configured."""
    import os

    source = env if env is not None else dict(os.environ)
    token = source.get("HOTLINE_BOT_TOKEN")
    guild = source.get("DISCORD_GUILD_ID")
    if not token or not guild:
        return None
    try:
        return Channels(token, int(guild))
    except ValueError:
        return None
