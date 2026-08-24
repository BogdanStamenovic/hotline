"""Paging Bogdan, and waiting for him to answer.

On an iPhone only a real PSTN call rings unconditionally, and money was ruled out.
The honest free substitute is a Discord `@mention`: it pushes to the lock screen
through APNs, it can repeat, and it does not ring. This escalates one — quiet at
first, louder over minutes, and eventually the physical siren on the workstation
in case he is sitting right there with his phone face down.

Two design choices worth stating.

**It is REST-only and synchronous.** No gateway connection, no `py-cord`, no
running daemon. A blocked agent in any Claude session anywhere on this machine can
call `hotline-page` and have it work even if `hotlined` is dead — which is exactly
the situation in which you most need to reach a human.

**It blocks and returns his answer on stdout.** That is the whole point. The
alternative is fire-and-forget, which leaves the agent guessing, and CLAUDE.md is
explicit that the approval loop should be fast enough not to stall a run.
`answer = $(hotline-page "may I spend money on X")` is a question, not a
notification.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

from .config import page_claim
from .text import chunk

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/BogdanStamenovic/hotline, 0.1)"
SIREN = os.path.expanduser("~/.claude/bin/wake-bogdan.sh")

# (seconds since the page started, what to do). Deliberately slow at the start:
# most questions are not urgent, and a pager that shouts immediately gets muted,
# which is the same failure mode as a denylist that fires on ordinary work.
DEFAULT_LADDER: list[tuple[float, str]] = [
    (0, "post"),
    (120, "nudge"),
    (300, "nudge"),
    (600, "siren"),
    (900, "nudge"),
    (1500, "siren"),
]

DEFAULT_TIMEOUT = 1800.0
POLL_SECONDS = 8.0


class PagerError(Exception):
    """Raised when a page could not be delivered at all."""


@dataclass
class PageResult:
    answered: bool
    reply: str = ""
    waited_seconds: float = 0.0
    escalations: list[str] = field(default_factory=list)
    message_id: str | None = None
    channel_id: str | None = None


def _request(
    path: str,
    token: str,
    method: str = "GET",
    body: dict | None = None,
    timeout: float = 20.0,
) -> object:
    """One Discord REST call, honouring 429 the way Discord asks.

    Retrying a 429 by guessing is how a bot gets its token flagged; `retry_after`
    is in the response body and is not optional.
    """
    for attempt in range(5):
        request = urllib.request.Request(
            API + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 4:
                try:
                    wait = float(json.loads(exc.read()).get("retry_after", 1.0))
                except (ValueError, AttributeError):
                    wait = 1.0
                time.sleep(min(wait + 0.25, 30.0))
                continue
            detail = exc.read()[:300].decode(errors="replace")
            raise PagerError(f"discord {method} {path} -> {exc.code}: {detail}") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            if attempt < 4:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise PagerError(f"discord {method} {path} unreachable: {exc}") from exc
    raise PagerError(f"discord {method} {path}: gave up after retries")


def _fire_siren(repeats: int = 3) -> bool:
    """Last resort: he may be at the keyboard with his phone face down.

    Never allowed to break the page. If PipeWire is down or the script is missing,
    that is worth noting in the result, not worth aborting for.
    """
    if not os.path.exists(SIREN):
        return False
    try:
        done = subprocess.run(
            [SIREN, str(repeats), "hotline: an agent is blocked and needs you"],
            timeout=90,
            check=False,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    # Starting the process is not the same as making a noise. A remembered
    # per-application volume once pinned the siren at -76 dB: it played, exited 0
    # and woke nobody. wake-bogdan.sh now exits 3 when the sink never went
    # RUNNING, so anything but 0 is a siren that did not actually fire.
    return done.returncode == 0


class Pager:
    def __init__(
        self,
        token: str,
        user_id: str,
        channel_id: str,
        siren: Callable[[], bool] = _fire_siren,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.token = token
        self.user_id = user_id
        self.channel_id = channel_id
        self._siren = siren
        self._sleep = sleep
        self._now = now
        self._dm_channel: str | None = None

    # ---- primitives -----------------------------------------------------

    def send(self, channel_id: str, content: str) -> str:
        """Post a message, splitting it if Discord will not take it whole.

        Returns the id of the FIRST part, because that is the anchor replies are
        counted from. Truncating instead -- which this used to do -- delivers
        something that reads as complete and is not; Bogdan lost the end of a
        status message that way and only noticed because the last sentence stopped
        mid-thought.
        """
        parts = chunk(content)
        first_id: str | None = None
        for index, part in enumerate(parts):
            body = part if len(parts) == 1 else f"{part}\n\n*({index + 1}/{len(parts)})*"
            payload = _request(
                f"/channels/{channel_id}/messages",
                self.token,
                "POST",
                {
                    "content": body,
                    # Without this an @mention inside a code block or an edited
                    # message can silently fail to notify, defeating the purpose.
                    "allowed_mentions": {"users": [self.user_id]},
                },
            )
            assert isinstance(payload, dict)
            if first_id is None:
                first_id = str(payload["id"])
        assert first_id is not None
        return first_id

    def dm_channel(self) -> str | None:
        """A DM pushes even when the guild is muted. Best-effort: he may have DMs
        from server members closed, and that must not fail the page."""
        if self._dm_channel:
            return self._dm_channel
        try:
            payload = _request(
                "/users/@me/channels", self.token, "POST", {"recipient_id": self.user_id}
            )
        except PagerError:
            return None
        if isinstance(payload, dict):
            self._dm_channel = str(payload["id"])
        return self._dm_channel

    def replies_since(self, channel_id: str, after: str) -> list[str]:
        try:
            payload = _request(
                f"/channels/{channel_id}/messages?after={after}&limit=25", self.token
            )
        except PagerError:
            return []
        if not isinstance(payload, list):
            return []
        found = [
            str(m.get("content") or "")
            for m in payload
            if isinstance(m, dict)
            and isinstance(m.get("author"), dict)
            and str(m["author"].get("id")) == self.user_id
        ]
        return [text for text in reversed(found) if text.strip()]

    # ---- the ladder -----------------------------------------------------

    def page(
        self,
        reason: str,
        context: str = "",
        timeout: float = DEFAULT_TIMEOUT,
        ladder: list[tuple[float, str]] | None = None,
        source: str = "an agent",
    ) -> PageResult:
        steps = sorted(ladder if ladder is not None else DEFAULT_LADDER)
        started = self._now()
        result = PageResult(answered=False)

        head = f"<@{self.user_id}> **{source} needs you.**\n\n{reason}"
        if context:
            head += f"\n\n```\n{context[:1200]}\n```"
        result.channel_id = self.channel_id
        result.message_id = self.send(self.channel_id, head)
        result.escalations.append("post")

        dm = self.dm_channel()
        dm_anchor: str | None = None
        if dm:
            try:
                dm_anchor = self.send(dm, head)
                result.escalations.append("dm")
            except PagerError:
                dm = None

        pending = [step for step in steps if step[1] != "post"]
        self._claim(True)

        try:
            return self._wait(result, reason, timeout, pending, started, dm, dm_anchor)
        finally:
            self._claim(False)

    def _claim(self, active: bool) -> None:
        """Tell the text bridge to keep its hands off this channel.

        Written with an expiry rather than deleted-on-exit only: if this process is
        killed mid-page the bridge must not stay muted forever.
        """
        path = page_claim()
        try:
            if active:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(str(time.time()))
            else:
                path.unlink(missing_ok=True)
        except OSError:
            pass

    def _wait(
        self,
        result: PageResult,
        reason: str,
        timeout: float,
        pending: list[tuple[float, str]],
        started: float,
        dm: str | None,
        dm_anchor: str | None,
    ) -> PageResult:
        assert result.message_id is not None
        while True:
            elapsed = self._now() - started
            result.waited_seconds = round(elapsed, 1)

            for text in self.replies_since(self.channel_id, result.message_id):
                result.answered, result.reply = True, text
                self._acknowledge(self.channel_id)
                return result
            if dm and dm_anchor:
                for text in self.replies_since(dm, dm_anchor):
                    result.answered, result.reply = True, text
                    self._acknowledge(dm)
                    return result

            if elapsed >= timeout:
                self.send(
                    self.channel_id,
                    f"<@{self.user_id}> giving up after {timeout / 60:.0f} minutes with no "
                    f"answer. The agent will proceed without you or stop. Original ask:\n"
                    f"> {reason[:400]}",
                )
                return result

            while pending and pending[0][0] <= elapsed:
                _, action = pending.pop(0)
                if action == "nudge":
                    minutes = elapsed / 60
                    self.send(
                        self.channel_id,
                        f"<@{self.user_id}> still blocked, {minutes:.0f} min in: {reason[:300]}",
                    )
                    result.escalations.append("nudge")
                elif action == "siren":
                    result.escalations.append("siren" if self._siren() else "siren-failed")

            self._sleep(min(POLL_SECONDS, max(0.1, timeout - elapsed)))

    def _acknowledge(self, channel_id: str) -> None:
        try:
            self.send(channel_id, "Got it — thanks. Carrying on.")
        except PagerError:
            pass


def from_env() -> Pager:
    token = os.environ.get("HOTLINE_BOT_TOKEN")
    user_id = os.environ.get("DISCORD_USER_ID")
    channel_id = os.environ.get("DISCORD_TEXT_CHANNEL_ID")
    missing = [
        name
        for name, value in (
            ("HOTLINE_BOT_TOKEN", token),
            ("DISCORD_USER_ID", user_id),
            ("DISCORD_TEXT_CHANNEL_ID", channel_id),
        )
        if not value
    ]
    if missing:
        raise PagerError(f"missing from the environment / .env: {', '.join(missing)}")
    assert token and user_id and channel_id
    return Pager(token, user_id, channel_id)
