"""A read-only IMAP view of one mailbox account.

READ-ONLY is the contract, not a habit. His unread state is his: a message an
agent looked at must still be bold in his Gmail afterwards. Three layers hold
that, so no single mistake breaks it:

1. Mailboxes are opened with EXAMINE (`select(readonly=True)`), which the
   server itself refuses to modify through.
2. Bodies are fetched with BODY.PEEK[...], never BODY[...] or RFC822, which set
   \\Seen as a side effect even on a read-only select on some servers.
3. `ReadOnlyIMAP` wraps the connection and exposes only the verbs used here.
   STORE, COPY, MOVE, EXPUNGE, APPEND and friends do not exist on it, and a
   FETCH naming a \\Seen-setting item raises before it reaches the wire.

There is no send, delete, move or flag anywhere in this package.
"""

from __future__ import annotations

import email
import email.policy
import imaplib
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from ..errors import HotlineError
from .creds import Credentials

TIMEOUT = 30.0
HEADER_FIELDS = "FROM TO SUBJECT DATE MESSAGE-ID"


class ReadOnlyIMAP:
    """The only IMAP surface the rest of the package can reach."""

    _UID_VERBS = frozenset({"SEARCH", "FETCH"})
    # BODY[ without .PEEK, and RFC822 / RFC822.TEXT, mark a message read.
    _SETS_SEEN = re.compile(r"\bBODY\[|\bRFC822(?!\.SIZE|\.HEADER)\b", re.IGNORECASE)

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def login(self, user: str, password: str) -> None:
        self._conn.login(user, password)

    def list_mailboxes(self) -> tuple[str, list[Any]]:
        return self._conn.list()

    def select(self, mailbox: str) -> int:
        typ, data = self._conn.select(_quote(mailbox), readonly=True)
        if typ != "OK":
            raise HotlineError(f"cannot open mailbox {mailbox!r}")
        try:
            return int(data[0])
        except (TypeError, ValueError, IndexError):
            return 0

    def uid(self, verb: str, *args: str) -> tuple[str, list[Any]]:
        if verb.upper() not in self._UID_VERBS:
            raise HotlineError(f"refusing IMAP UID {verb}: this inbox is read-only")
        if verb.upper() == "FETCH" and self._SETS_SEEN.search(" ".join(args[1:])):
            raise HotlineError(f"refusing FETCH {args[1:]}: it would mark mail as read")
        return self._conn.uid(verb, *args)

    def logout(self) -> None:
        try:
            self._conn.logout()
        except (OSError, imaplib.IMAP4.error):
            pass


def _quote(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _connect(creds: Credentials) -> Any:
    return imaplib.IMAP4_SSL(creds.host, creds.port, timeout=TIMEOUT)


@dataclass
class Summary:
    """The envelope of one message. Every text field is untrusted."""

    mailbox: str
    uid: int
    message_id: str
    from_: str
    to: str
    subject: str
    date: str
    received: float  # INTERNALDATE: when the server got it, as epoch seconds
    size: int = 0

    @property
    def key(self) -> str:
        # Message-ID survives the same mail appearing in two Gmail folders; the
        # uid fallback covers mail that has none (it happens, mostly spam).
        return self.message_id or f"{self.mailbox}#{self.uid}"


@dataclass
class Attachment:
    filename: str
    content_type: str
    size: int


@dataclass
class Message:
    summary: Summary
    headers: list[tuple[str, str]]
    text: str
    attachments: list[Attachment] = field(default_factory=list)
    raw: EmailMessage | None = field(default=None, repr=False)


_META = re.compile(rb"UID (\d+)")
_IDATE = re.compile(rb'INTERNALDATE "([^"]+)"')
_SIZE = re.compile(rb"RFC822\.SIZE (\d+)")


def _internaldate(meta: bytes) -> float:
    found = _IDATE.search(meta)
    if not found:
        return 0.0
    try:
        return datetime.strptime(found.group(1).decode(), "%d-%b-%Y %H:%M:%S %z").timestamp()
    except ValueError:
        return 0.0


def _parts(data: list[Any]) -> Iterator[tuple[bytes, bytes]]:
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2:
            yield item[0], item[1]


def _header(msg: EmailMessage, name: str) -> str:
    try:
        value = msg.get(name)
    except (ValueError, IndexError, TypeError):
        # A malformed header the policy cannot decode is still not a crash.
        return ""
    return str(value) if value is not None else ""


def imap_date(epoch: float) -> str:
    return time.strftime("%d-%b-%Y", time.gmtime(epoch))


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.out: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3"):
            self.out.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.out.append(data)


def html_to_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    text = "".join(parser.out)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


class Inbox:
    def __init__(self, imap: ReadOnlyIMAP) -> None:
        self.imap = imap
        self._selected: str | None = None

    @classmethod
    @contextmanager
    def open(
        cls, creds: Credentials, connect: Callable[[Credentials], Any] | None = None
    ) -> Iterator[Inbox]:
        try:
            conn = (connect or _connect)(creds)
        except (OSError, imaplib.IMAP4.error) as exc:
            raise HotlineError(f"cannot reach {creds.host}: {exc}") from exc
        imap = ReadOnlyIMAP(conn)
        try:
            try:
                imap.login(creds.user, creds.password)
            except imaplib.IMAP4.error as exc:
                # The server's text, never ours with the password in it.
                raise HotlineError(f"IMAP login as {creds.user} refused: {exc}") from exc
            yield cls(imap)
        finally:
            imap.logout()

    # ---- mailboxes -------------------------------------------------------

    def mailboxes(self) -> list[tuple[set[str], str]]:
        _typ, data = self.imap.list_mailboxes()
        found = []
        for line in data or []:
            if not isinstance(line, bytes):
                continue
            m = re.match(rb'\(([^)]*)\) (?:"[^"]*"|NIL) (.+)$', line)
            if not m:
                continue
            flags = {f.decode().lower() for f in m.group(1).split()}
            name = m.group(2).decode(errors="replace").strip()
            if name.startswith('"') and name.endswith('"'):
                name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            found.append((flags, name))
        return found

    def special(self, flag: str) -> str | None:
        """The mailbox carrying a special-use flag like \\All or \\Junk.

        By flag, not by name: Gmail localises "[Gmail]/All Mail" to the
        account's language, and a hardcoded English name would silently match
        nothing on a Serbian-language account.
        """
        for flags, name in self.mailboxes():
            if flag.lower() in flags:
                return name
        return None

    def watch_mailboxes(self) -> list[str]:
        """Where expected mail can land: everything but trash, plus spam.

        All Mail rather than INBOX because a filter can skip the inbox, and spam
        because a waitlist confirmation from a new domain is precisely what
        Gmail files there. Falls back to INBOX on a server without special-use.
        """
        boxes = [b for b in (self.special("\\All"), self.special("\\Junk")) if b]
        return boxes or ["INBOX"]

    def _select(self, mailbox: str) -> int:
        count = self.imap.select(mailbox)
        self._selected = mailbox
        return count

    def count(self, mailbox: str = "INBOX") -> int:
        return self._select(mailbox)

    def unseen(self, mailbox: str = "INBOX") -> int:
        self._select(mailbox)
        return len(self._search(["UNSEEN"]))

    # ---- search ----------------------------------------------------------

    def _search(self, criteria: list[str]) -> list[int]:
        typ, data = self.imap.uid("SEARCH", *(criteria or ["ALL"]))
        if typ != "OK":
            raise HotlineError(f"IMAP search failed: {data!r}"[:200])
        uids: list[int] = []
        for chunk in data or []:
            if isinstance(chunk, bytes):
                uids.extend(int(u) for u in chunk.split())
        return sorted(uids)

    @staticmethod
    def criteria(
        *,
        from_: str | None = None,
        subject: str | None = None,
        since: float | None = None,
        text: str | None = None,
    ) -> list[str]:
        out: list[str] = []
        if since is not None:
            out += ["SINCE", imap_date(since)]
        for verb, value in (("FROM", from_), ("SUBJECT", subject), ("TEXT", text)):
            if value:
                if not value.isascii():
                    # imaplib can send one literal per command, and a CHARSET
                    # search with several is not worth a hand-rolled protocol
                    # layer. Search ASCII on the server, filter locally.
                    raise HotlineError(f"{verb.lower()} must be ASCII for a server search")
                out += [verb, _quote(value)]
        return out or ["ALL"]

    def search(self, mailbox: str = "INBOX", limit: int = 20, **criteria: Any) -> list[Summary]:
        self._select(mailbox)
        uids = self._search(self.criteria(**criteria))
        return self.summaries(uids[-limit:] if limit else uids, mailbox)

    def recent(self, mailbox: str = "INBOX", limit: int = 20) -> list[Summary]:
        count = self._select(mailbox)
        if not count:
            return []
        uids = self._search(["ALL"])
        return self.summaries(uids[-limit:], mailbox)

    def summaries(self, uids: list[int], mailbox: str) -> list[Summary]:
        if not uids:
            return []
        if self._selected != mailbox:
            self._select(mailbox)
        typ, data = self.imap.uid(
            "FETCH",
            ",".join(str(u) for u in uids),
            f"(UID INTERNALDATE RFC822.SIZE BODY.PEEK[HEADER.FIELDS ({HEADER_FIELDS})])",
        )
        if typ != "OK":
            raise HotlineError("IMAP fetch failed")
        out = []
        for meta, raw in _parts(data):
            uid = _META.search(meta)
            if not uid:
                continue
            msg = email.message_from_bytes(raw, policy=email.policy.default)
            size = _SIZE.search(meta)
            out.append(
                Summary(
                    mailbox=mailbox,
                    uid=int(uid.group(1)),
                    message_id=_header(msg, "Message-ID").strip(),
                    from_=_header(msg, "From"),
                    to=_header(msg, "To"),
                    subject=_header(msg, "Subject"),
                    date=_header(msg, "Date"),
                    received=_internaldate(meta),
                    size=int(size.group(1)) if size else 0,
                )
            )
        return sorted(out, key=lambda s: s.uid)

    # ---- one message -----------------------------------------------------

    def show(self, uid: int, mailbox: str = "INBOX") -> Message:
        self._select(mailbox)
        typ, data = self.imap.uid("FETCH", str(uid), "(UID INTERNALDATE RFC822.SIZE BODY.PEEK[])")
        parts = list(_parts(data)) if typ == "OK" else []
        if not parts:
            raise HotlineError(f"no message with uid {uid} in {mailbox}")
        meta, raw = parts[0]
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        assert isinstance(msg, EmailMessage)
        size = _SIZE.search(meta)
        summary = Summary(
            mailbox=mailbox,
            uid=uid,
            message_id=_header(msg, "Message-ID").strip(),
            from_=_header(msg, "From"),
            to=_header(msg, "To"),
            subject=_header(msg, "Subject"),
            date=_header(msg, "Date"),
            received=_internaldate(meta),
            size=int(size.group(1)) if size else len(raw),
        )
        headers = [
            (name, _header(msg, name))
            for name in ("From", "To", "Cc", "Reply-To", "Date", "Subject", "Message-ID")
            if msg.get(name) is not None
        ]
        return Message(summary, headers, body_text(msg), attachments(msg), raw=msg)


def body_text(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, ValueError):
        payload = part.get_payload(decode=True)
        content = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else ""
    if part.get_content_type() == "text/html":
        return html_to_text(str(content))
    return str(content)


def attachments(msg: EmailMessage) -> list[Attachment]:
    out = []
    for part in msg.iter_attachments():
        payload = part.get_payload(decode=True)
        out.append(
            Attachment(
                filename=part.get_filename() or "(unnamed)",
                content_type=part.get_content_type(),
                size=len(payload) if isinstance(payload, bytes) else 0,
            )
        )
    return out


def save_attachments(msg: EmailMessage, directory: Path) -> list[Path]:
    """Write attachments into `directory`. Only ever on an explicit request.

    Filenames are the sender's choice, so they are reduced to a basename with a
    conservative alphabet -- "../../.bashrc" must land as "bashrc" in the target
    directory and nowhere else -- and an existing file is never overwritten.
    """
    directory.mkdir(parents=True, exist_ok=True)
    saved = []
    for index, part in enumerate(msg.iter_attachments()):
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            continue
        name = Path(part.get_filename() or f"attachment-{index}").name
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).lstrip(".") or f"attachment-{index}"
        target = directory / name
        n = 1
        while target.exists():
            target = directory / f"{target.stem}-{n}{target.suffix}"
            n += 1
        target.write_bytes(payload)
        saved.append(target)
    return saved
