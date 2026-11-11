"""Files Bogdan attaches to a Discord message: download them and tell the agent where they are.

Without this the bridge relayed `message.content` only, so a screenshot or a PDF
reached the agent as nothing at all (and a message that was only a file was
dropped as empty). Each attachment is saved under
`<state>/attachments/<channel>/<message>/<name>` and listed in a block appended
to the relayed text, so the agent can Read the path directly.

The block is part of the delivered body, so the provenance receipt covers it.
`provenance.verify` recognises a block whose file names match the Discord
message's real attachments and does not report it as text added in transit.
Trust: he said only trusted people have access to hotline (2026-11-11), so files
are not scanned; they are data the agent reads, never something it runs.
"""

from __future__ import annotations

import re
import shutil
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .config import state_dir

MARK = "[hotline-attachments]"
MAX_BYTES = 100 * 1024 * 1024
KEEP_DAYS = 30


def root() -> Path:
    return state_dir() / "attachments"


def safe_name(name: str) -> str:
    """A file name that cannot escape its folder or confuse a shell reader."""
    base = Path(name.replace("\\", "/")).name
    base = re.sub(r"[^\w.\-]+", "_", base).strip("._") or "file"
    return base[:120]


def _size(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB" if n >= 1024 * 1024 else f"{max(1, n // 1024)} KB"


def block(lines: Iterable[str]) -> str:
    items = list(lines)
    if not items:
        return ""
    return (
        f"\n\n{MARK} Bogdan attached these files; they are saved locally, Read them by path:\n"
        + "\n".join(items)
    )


def prune(base: Path | None = None, days: int = KEEP_DAYS, now: float | None = None) -> None:
    """Drop message folders older than `days`, so downloads don't pile up on a tight root."""
    base = base or root()
    cutoff = (now or time.time()) - days * 86400
    if not base.is_dir():
        return
    for channel in base.iterdir():
        for msg in channel.iterdir() if channel.is_dir() else ():
            if msg.is_dir() and msg.stat().st_mtime < cutoff:
                shutil.rmtree(msg, ignore_errors=True)


async def save_all(message: Any, base: Path | None = None) -> str:
    """Download every attachment of `message`; return the block to append ('' if none)."""
    attachments = list(getattr(message, "attachments", None) or [])
    if not attachments:
        return ""
    base = base or root()
    folder = base / str(message.channel.id) / str(message.id)
    folder.mkdir(parents=True, exist_ok=True)
    lines = []
    used: set[str] = set()
    for att in attachments:
        name = safe_name(att.filename)
        stem, dot, ext = name.rpartition(".")
        n = 2
        while name in used:
            name = f"{stem}-{n}.{ext}" if dot else f"{ext}-{n}"
            n += 1
        used.add(name)
        kind = att.content_type or "unknown type"
        if att.size > MAX_BYTES:
            lines.append(f"- NOT DOWNLOADED {name} ({kind}, {_size(att.size)}, over the {_size(MAX_BYTES)} cap): {att.url}")
            continue
        path = folder / name
        try:
            await att.save(path)
        except Exception as exc:  # noqa: BLE001 - one bad file must not lose the message
            lines.append(f"- FAILED {name} ({kind}): {type(exc).__name__}: {exc}; url {att.url}")
            continue
        lines.append(f"- {path} ({kind}, {_size(att.size)})")
    try:
        prune(base)
    except OSError:
        pass
    return block(lines)


_LINE = re.compile(r"^- (?:(?:NOT DOWNLOADED|FAILED) )?(?:\S*/)?([\w.\-]+)[ :(]")


def strip_matching(added: str, original_attachments: list[dict[str, Any]]) -> str:
    """Remove our attachments block from verify's 'added' text when it names exactly
    the files Discord says the message carries. Anything else is left in, so a forged
    block still shows up as an addition."""
    at = added.find(MARK)
    if at < 0:
        return added
    head, tail = added[:at], added[at:]
    lines = tail.splitlines()[1:]
    names = []
    for line in lines:
        m = _LINE.match(line)
        if not m:
            return added
        names.append(m.group(1))
    real = [safe_name(str(a.get("filename", ""))) for a in original_attachments]
    if not names or not _same_files(names, real):
        return added
    return head.strip()


def _same_files(listed: list[str], real: list[str]) -> bool:
    # duplicates get -2, -3 suffixes on disk; map them back before comparing
    def base(n: str) -> str:
        return re.sub(r"-\d+(?=\.[^.]+$|$)", "", n)

    return sorted(base(n) for n in listed) == sorted(base(n) for n in real) or sorted(listed) == sorted(real)
