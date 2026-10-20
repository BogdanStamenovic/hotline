"""IMAP credentials, borrowed from the msmtp config that already holds them.

The Gmail app password is in ~/.msmtprc for sending. An app password is good
for IMAP too, so the inbox reads the same account rather than a second copy of
the secret living in hotline's .env -- one place to rotate, and nothing new to
leak. The password is never logged, printed, or put in an exception message:
`Credentials.__repr__` hides it so a traceback cannot carry it either.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import HotlineError

DEFAULT_ACCOUNT = "gmail"

# msmtp names an SMTP host; IMAP lives elsewhere. Only Gmail is mapped because
# it is the only account this has been run against -- anything else must say
# its IMAP host explicitly rather than have one guessed for it.
IMAP_HOSTS = {"smtp.gmail.com": "imap.gmail.com"}


@dataclass(frozen=True)
class Credentials:
    user: str
    host: str
    port: int = 993
    password: str = field(default="", repr=False)

    def __repr__(self) -> str:
        return f"Credentials(user={self.user!r}, host={self.host!r}, port={self.port})"


def msmtprc_path() -> Path:
    return Path(os.environ.get("HOTLINE_MAIL_MSMTPRC", Path.home() / ".msmtprc"))


def _accounts(text: str) -> dict[str, dict[str, str]]:
    """msmtprc as {account: {key: value}}, with `defaults` folded into each."""
    defaults: dict[str, str] = {}
    accounts: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition(" ")
        value = value.strip()
        if key == "defaults":
            current = defaults
        elif key == "account":
            # `account default : gmail` is an alias, not a new account.
            name = value.split(":")[0].strip()
            if ":" in value:
                current = None
                continue
            current = accounts.setdefault(name, dict(defaults))
        elif current is not None:
            current[key] = value
    return accounts


def load(account: str | None = None, path: Path | None = None) -> Credentials:
    account = account or os.environ.get("HOTLINE_MAIL_ACCOUNT") or DEFAULT_ACCOUNT
    path = path or msmtprc_path()
    try:
        text = path.read_text()
    except OSError as exc:
        raise HotlineError(f"cannot read {path}: {exc.strerror}") from exc
    entry = _accounts(text).get(account)
    if entry is None:
        raise HotlineError(f"no account {account!r} in {path}")
    user = entry.get("user") or entry.get("from")
    if not user:
        raise HotlineError(f"account {account!r} in {path} has no user")
    password = entry.get("password", "")
    if not password and entry.get("passwordeval"):
        try:
            done = subprocess.run(
                shlex.split(entry["passwordeval"]),
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise HotlineError(f"passwordeval for {account!r} failed to run") from exc
        if done.returncode != 0:
            # stderr deliberately not included: a failing password command can
            # echo what it was given.
            raise HotlineError(f"passwordeval for {account!r} exited {done.returncode}")
        password = done.stdout.strip()
    if not password:
        raise HotlineError(f"account {account!r} in {path} has no password")
    host = os.environ.get("HOTLINE_MAIL_IMAP_HOST") or IMAP_HOSTS.get(entry.get("host", ""))
    if not host:
        raise HotlineError(
            f"no IMAP host known for {entry.get('host')!r}; set HOTLINE_MAIL_IMAP_HOST"
        )
    return Credentials(user=user, host=host, password=password)
