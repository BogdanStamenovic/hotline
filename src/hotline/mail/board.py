"""The expectation board: mail somebody is waiting for, and whether it came.

An expectation is a small set of rules ("From matches /skyfall/", optionally
"Subject matches /waitlist/") plus why it exists, who asked, and an optional
deadline. `check` matches every open expectation against recent mail and marks
the first hit received, recording which message it was and WHICH rule matched
what, so a wrong match can be seen to be wrong instead of trusted.

Conservative on purpose. A missed match costs a "still waiting" line on the
board and a look by the operator; a false match tells someone their mail came
when it did not, and nobody re-checks a thing marked done. So:

* every rule given must match (AND, never OR);
* a pattern that matches the empty string (".*", "", "x?") is refused at
  `expect` time, because it would match any message at all;
* mail sent FROM the account itself is never a match -- Gmail's All Mail holds
  his sent mail, and his own reply quoting the subject is not the arrival;
* only mail the server received after `since` counts (default: a day before the
  expectation was posted, because the reason to post one is usually a sign-up
  that just happened, and the confirmation can beat the agent to the board).

State is one sqlite file under the hotline state dir. sqlite rather than a JSON
file like kb.json because the daemon's checker and an operator's `expect` run in
different processes and can write at the same moment.
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from email.utils import parseaddr
from pathlib import Path

from ..config import state_dir
from ..errors import HotlineError
from .inbox import Summary

OPEN, RECEIVED, CLOSED, CANCELLED = "open", "received", "closed", "cancelled"
STATUSES = (OPEN, RECEIVED, CLOSED, CANCELLED)
DEFAULT_LOOKBACK = 24 * 3600.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS expectations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    why TEXT NOT NULL,
    from_re TEXT,
    subject_re TEXT,
    agent TEXT,
    posted_by TEXT,
    created REAL NOT NULL,
    since REAL NOT NULL,
    due REAL,
    status TEXT NOT NULL DEFAULT 'open',
    overdue_flagged REAL,
    received_at REAL,
    message_id TEXT,
    msg_from TEXT,
    msg_subject TEXT,
    msg_date TEXT,
    msg_mailbox TEXT,
    msg_uid INTEGER,
    matched TEXT,
    notified REAL,
    closed_at REAL,
    note TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def db_path() -> Path:
    return state_dir() / "mail.db"


def check_pattern(pattern: str, field: str) -> re.Pattern[str]:
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise HotlineError(f"--{field} is not a valid regex: {exc}") from exc
    if compiled.search(""):
        raise HotlineError(
            f"--{field} {pattern!r} matches the empty string, so it would match every "
            "message; give something that has to actually appear"
        )
    return compiled


@dataclass
class Expectation:
    id: int
    why: str
    from_re: str | None
    subject_re: str | None
    agent: str | None
    posted_by: str | None
    created: float
    since: float
    due: float | None
    status: str
    overdue_flagged: float | None
    received_at: float | None
    message_id: str | None
    msg_from: str | None
    msg_subject: str | None
    msg_date: str | None
    msg_mailbox: str | None
    msg_uid: int | None
    matched: str | None
    notified: float | None
    closed_at: float | None
    note: str | None

    def overdue(self, now: float | None = None) -> bool:
        return self.status == OPEN and self.due is not None and (now or time.time()) > self.due

    def rules(self) -> str:
        parts = []
        if self.from_re:
            parts.append(f"from ~ /{self.from_re}/")
        if self.subject_re:
            parts.append(f"subject ~ /{self.subject_re}/")
        return " AND ".join(parts)

    def match(self, mail: Summary, own_address: str = "") -> str | None:
        """Why `mail` satisfies this expectation, or None if it does not."""
        if mail.received and mail.received < self.since:
            return None
        sender = parseaddr(mail.from_)[1].casefold()
        if own_address and sender == own_address.casefold():
            return None
        reasons = []
        for label, pattern, value in (
            ("from", self.from_re, mail.from_),
            ("subject", self.subject_re, mail.subject),
        ):
            if not pattern:
                continue
            hit = re.search(pattern, value or "", re.IGNORECASE)
            if hit is None:
                return None
            reasons.append(f"{label} ~ /{pattern}/ matched {hit.group(0)!r}")
        return "; ".join(reasons) if reasons else None


class Board:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(_SCHEMA)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    # ---- meta ------------------------------------------------------------

    def meta(self, key: str) -> str | None:
        with self._db() as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str | None) -> None:
        with self._db() as db:
            if value is None:
                db.execute("DELETE FROM meta WHERE key=?", (key,))
            else:
                db.execute(
                    "INSERT INTO meta(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )

    # ---- expectations ----------------------------------------------------

    def expect(
        self,
        why: str,
        *,
        from_re: str | None = None,
        subject_re: str | None = None,
        due: float | None = None,
        agent: str | None = None,
        posted_by: str | None = None,
        since: float | None = None,
        now: float | None = None,
    ) -> Expectation:
        if not why.strip():
            raise HotlineError("--why is required: the board is read by people")
        if not (from_re or subject_re):
            raise HotlineError("give at least one of --from or --subject")
        for pattern, name in ((from_re, "from"), (subject_re, "subject")):
            if pattern:
                check_pattern(pattern, name)
        now = now or time.time()
        with self._db() as db:
            cur = db.execute(
                "INSERT INTO expectations(why, from_re, subject_re, agent, posted_by,"
                " created, since, due) VALUES(?,?,?,?,?,?,?,?)",
                (
                    why.strip(),
                    from_re,
                    subject_re,
                    agent,
                    posted_by,
                    now,
                    since if since is not None else now - DEFAULT_LOOKBACK,
                    due,
                ),
            )
            new_id = int(cur.lastrowid or 0)
        found = self.get(new_id)
        assert found is not None
        return found

    def get(self, ident: int) -> Expectation | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM expectations WHERE id=?", (ident,)).fetchone()
        return Expectation(**dict(row)) if row else None

    def all(self, statuses: Iterable[str] = STATUSES) -> list[Expectation]:
        wanted = list(statuses)
        marks = ",".join("?" * len(wanted))
        with self._db() as db:
            rows = db.execute(
                f"SELECT * FROM expectations WHERE status IN ({marks}) ORDER BY id", wanted
            ).fetchall()
        return [Expectation(**dict(r)) for r in rows]

    def open(self) -> list[Expectation]:
        return self.all([OPEN])

    def _update(self, ident: int, **fields: object) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._db() as db:
            db.execute(f"UPDATE expectations SET {cols} WHERE id=?", (*fields.values(), ident))

    def finish(self, ident: int, status: str, note: str | None = None) -> Expectation:
        """Close (done with it) or cancel (no longer wanted) an expectation."""
        if status not in (CLOSED, CANCELLED):
            raise HotlineError(f"cannot finish with status {status!r}")
        found = self.get(ident)
        if found is None:
            raise HotlineError(f"no expectation {ident}")
        self._update(ident, status=status, closed_at=time.time(), note=note)
        updated = self.get(ident)
        assert updated is not None
        return updated

    def mark_received(self, exp: Expectation, mail: Summary, why: str) -> Expectation:
        self._update(
            exp.id,
            status=RECEIVED,
            received_at=mail.received or time.time(),
            message_id=mail.message_id,
            msg_from=mail.from_,
            msg_subject=mail.subject,
            msg_date=mail.date,
            msg_mailbox=mail.mailbox,
            msg_uid=mail.uid,
            matched=why,
        )
        updated = self.get(exp.id)
        assert updated is not None
        return updated

    def mark_notified(self, ident: int) -> None:
        self._update(ident, notified=time.time())

    def mark_overdue_flagged(self, ident: int) -> None:
        self._update(ident, overdue_flagged=time.time())

    # ---- matching --------------------------------------------------------

    def match(
        self, mail: Iterable[Summary], own_address: str = ""
    ) -> list[tuple[Expectation, Summary, str]]:
        """Resolve open expectations against `mail`; earliest arrival wins.

        One message may satisfy two expectations (two agents waiting on the
        same confirmation). Each expectation takes at most one message.
        """
        ordered = sorted(mail, key=lambda m: (m.received, m.uid))
        hits = []
        for exp in self.open():
            for candidate in ordered:
                why = exp.match(candidate, own_address)
                if why:
                    hits.append((self.mark_received(exp, candidate, why), candidate, why))
                    break
        return hits

    def newly_overdue(self, now: float | None = None) -> list[Expectation]:
        """Open, past due, and not yet flagged. Flagged once, never again."""
        now = now or time.time()
        return [e for e in self.open() if e.overdue(now) and not e.overdue_flagged]
