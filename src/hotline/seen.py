"""When each agent was last seen alive, for retention of the ones that just stopped.

Bogdan's rule (10-14): passing agents are deleted three days after they stop and
purged from Discord too; keepers stay. Before this only a `--done` started the
clock, so an agent that was killed, or died with a power-off, kept its channel
forever: six of those were sitting in the sidebar that night.

"Three days after it stopped" needs to know when it stopped, and nothing records
that -- a session that dies tells nobody. So hotlined's layout tick writes the
time it last saw each agent alive, and retention counts from there.

A sibling file rather than a field on the Agent record, for the reason
`teams.py` gives: a long-running daemon on older code skips registry rows with
fields it does not know, and would forget the agents entirely.

**An agent with no entry is first seen now.** The file starts empty, so every
agent that was already dead on the day this shipped gets three days' grace
rather than being purged on the first pass. That grace is the window in which he
can mark any of them a keeper.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path


class Seen:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> dict[str, float]:
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return {str(k): float(v) for k, v in raw.items() if isinstance(v, (int, float))}

    def update(
        self, alive: Iterable[str], known: Iterable[str], now: float | None = None
    ) -> dict[str, float]:
        """Stamp `alive` with now, give `known` agents with no entry a first sighting.

        Names no longer known are dropped, so the file does not grow forever.
        """
        moment = time.time() if now is None else now
        known_l = {n.lower() for n in known}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_suffix(".lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            seen = {k: v for k, v in self.read().items() if k in known_l}
            for name in known_l:
                seen.setdefault(name, moment)
            for name in alive:
                seen[name.lower()] = moment
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".seen.", suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(seen, f, indent=1)
            os.replace(tmp, self.path)
        return seen


def seen_beside(registry_path: Path) -> Seen:
    return Seen(registry_path.with_name("seen.json"))


def archive_channel(manager: object, channel_id: int, out_dir: Path) -> Path | None:
    """Save a channel's full history before it is deleted. None if it could not be.

    Same shape as the 10-04 prune archive (`{"channel": ..., "messages": [...]}`),
    so both read the same way.
    """
    with contextlib.suppress(Exception):
        meta, messages = manager.history(channel_id)  # type: ignore[attr-defined]
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{meta.get('name', 'channel')}-{channel_id}.json"
        path.write_text(json.dumps({"channel": meta, "messages": messages}, indent=1))
        return path
    return None
