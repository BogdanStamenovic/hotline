"""Prove a phone message was typed by Bogdan, not merely by a key-holder.

The daemon (`hotlined`) authenticates a phone request with a shared
`HOTLINE_API_KEY` and an IP allowlist. That proves *something holding the key*
sent it -- and the key is plaintext-readable by anything running as his uid, so
it proves nothing about *him*. There is no receipt either: the bytes replay
valid forever and nothing dates them. That is why a phone "shutdown now" has had
to be re-confirmed over Discord every time.

This module closes that gap for phone messages the way Discord provenance closes
it for relays. The phone signs each message with an **Ed25519** private key held
only on the device (iOS Keychain); the daemon verifies against the enrolled
**public** key. Asymmetry is the whole point: a shared symmetric secret can be
read and forged by anything on the box that holds it; a private key that never
leaves the phone cannot. The signature covers a **timestamp** and a **nonce**,
so stale bytes are rejected and a captured message cannot be replayed. And every
verified message leaves a **receipt** on disk, so a later session can re-verify
`kind=phone` off that receipt the way `--provenance` re-fetches a Discord message
-- checkable evidence, not a status field.

Deliberately self-contained and importable without the daemon: `provenance.py`
is Bogdan's own uncommitted work-in-progress and is left untouched. The server
half lives here; the app half (keypair in the Keychain, sign on send, one-time
public-key enrollment) is his, and the canonical byte format below is the
contract between them.

Uses PyNaCl, which is already in the daemon's venv (a transitive dep of
`py-cord[voice]`), so this adds no new dependency. The "PyNaCl has no 3.14 wheel"
note in `pyproject.toml` is about system Python 3.14; the daemon runs in the 3.12
venv, where PyNaCl 1.6 is installed and has a wheel.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from base64 import b64decode
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

# Bumped if the signed-bytes layout below ever changes, so an old app and a new
# daemon fail closed (signature mismatch) instead of silently disagreeing about
# what was signed.
SIG_VERSION = "HOTLINE-PHONE-SIG-v1"

# How far a message's timestamp may sit from the daemon's clock and still be
# accepted. This is the anti-replay horizon: captured bytes stop verifying once
# they age past it. Generous by default because the phone re-presents the *same*
# signed payload while polling for a slow turn's answer (see `verify_message`),
# and a turn can legitimately run for minutes. Tighten it if the app is changed
# to re-sign each poll with a fresh timestamp.
DEFAULT_SKEW_SECONDS = 3600

_STATE = Path(os.environ.get("HOTLINE_STATE_DIR") or (Path.home() / ".local/state/hotline"))
_CONFIG = Path(os.environ.get("HOTLINE_CONFIG_DIR") or (Path.home() / ".config/hotline"))


def _keys_path() -> Path:
    override = os.environ.get("HOTLINE_PHONE_KEYS")
    return Path(override) if override else _CONFIG / "phone_keys.json"


def _receipts_dir() -> Path:
    override = os.environ.get("HOTLINE_PHONE_RECEIPTS")
    return Path(override) if override else _STATE / "phone_receipts"


def _nonce_db_path() -> Path:
    override = os.environ.get("HOTLINE_PHONE_NONCES")
    return Path(override) if override else _STATE / "phone_nonces.db"


class PhoneAuthError(Exception):
    """A phone message did not verify. The message says why in plain words."""


# A nonce/timestamp/key_id must not contain a newline, because the canonical
# layout separates fields by newlines and the body -- which may contain anything
# -- comes last. A newline in an earlier field would let two different messages
# canonicalise to the same bytes.
def _clean_field(name: str, value: str) -> str:
    if not value or "\n" in value or "\r" in value:
        raise PhoneAuthError(f"{name} must be a non-empty single line")
    return value


def canonical_bytes(*, key_id: str, timestamp: str, nonce: str, body: str) -> bytes:
    """The exact bytes the phone signs and the daemon verifies.

    Layout (utf-8), body last so it may contain newlines without ambiguity:

        HOTLINE-PHONE-SIG-v1\\n
        <key_id>\\n
        <timestamp>\\n      (unix seconds, as decimal digits)
        <nonce>\\n
        <body>

    This function is the whole contract with the app. Reimplement it byte-for-byte
    in Swift and the two halves agree; deviate and every signature fails closed.
    """
    key_id = _clean_field("key_id", key_id)
    timestamp = _clean_field("timestamp", timestamp)
    nonce = _clean_field("nonce", nonce)
    head = f"{SIG_VERSION}\n{key_id}\n{timestamp}\n{nonce}\n"
    return head.encode("utf-8") + body.encode("utf-8")


@dataclass
class Receipt:
    """A verified phone message, persisted so it can be re-verified later.

    Everything needed to recompute the signed bytes is here, so re-verification
    reads only this file plus the enrolled public key -- it never trusts a stored
    "verified: true" flag, because a flag is exactly the status field this project
    keeps getting burned by.
    """

    receipt_id: str
    key_id: str
    timestamp: str
    nonce: str
    body: str
    signature: str  # base64
    label: str = ""
    received_at: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def body_sha256_16(self) -> str:
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()[:16]

    def to_json(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "sig_version": SIG_VERSION,
            "key_id": self.key_id,
            "timestamp": self.timestamp,
            "nonce": self.nonce,
            "body": self.body,
            "signature": self.signature,
            "label": self.label,
            "received_at": self.received_at,
            "extra": self.extra,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Receipt:
        return cls(
            receipt_id=str(data["receipt_id"]),
            key_id=str(data["key_id"]),
            timestamp=str(data["timestamp"]),
            nonce=str(data["nonce"]),
            body=str(data["body"]),
            signature=str(data["signature"]),
            label=str(data.get("label", "")),
            received_at=int(data.get("received_at", 0)),
            extra=dict(data.get("extra", {})),
        )


# --- enrolled public keys -------------------------------------------------


def load_keys() -> dict[str, dict[str, Any]]:
    """The phone public keys authorised to speak as him, by key_id.

    A JSON object `{key_id: {"pubkey": "<base64 32-byte ed25519>", "label": ...}}`.
    Analogous to `authorized_keys`: enrolling a key is a deliberate, one-time act,
    and only a holder of the matching private key (the phone) can then sign as him.
    A bare `{key_id: "<base64>"}` string value is also accepted for hand-editing.
    """
    path = _keys_path()
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    out: dict[str, dict[str, Any]] = {}
    for key_id, val in raw.items():
        if isinstance(val, str):
            out[key_id] = {"pubkey": val}
        elif isinstance(val, dict):
            out[key_id] = dict(val)
    return out


def enroll(key_id: str, pubkey_b64: str, label: str = "") -> None:
    """Authorise a phone public key. Validates it is a real Ed25519 key first."""
    _clean_field("key_id", key_id)
    _load_pubkey(pubkey_b64)  # raises if not a valid 32-byte Ed25519 key
    path = _keys_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = load_keys()
    keys[key_id] = {"pubkey": pubkey_b64, "label": label, "enrolled_at": int(time.time())}
    path.write_text(json.dumps(keys, indent=2, sort_keys=True))


def _load_pubkey(pubkey_b64: str) -> VerifyKey:
    try:
        raw = b64decode(pubkey_b64, validate=True)
    except Exception as exc:
        raise PhoneAuthError("public key is not valid base64") from exc
    if len(raw) != 32:
        raise PhoneAuthError(f"Ed25519 public key must be 32 bytes, got {len(raw)}")
    try:
        return VerifyKey(raw)
    except Exception as exc:
        raise PhoneAuthError("public key is not a valid Ed25519 key") from exc


# --- nonce store (anti-replay) --------------------------------------------


def _nonce_conn() -> sqlite3.Connection:
    path = _nonce_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS nonces "
        "(nonce TEXT PRIMARY KEY, seen_at INTEGER, receipt_id TEXT, sig TEXT)"
    )
    return conn


def _remember_nonce(nonce: str, receipt_id: str, sig: str, now: int, skew: int) -> None:
    conn = _nonce_conn()
    try:
        # Prune anything already past the replay horizon: once a nonce's message
        # would be rejected for staleness anyway, keeping the nonce buys nothing.
        conn.execute("DELETE FROM nonces WHERE seen_at < ?", (now - skew,))
        conn.execute(
            "INSERT OR IGNORE INTO nonces (nonce, seen_at, receipt_id, sig) VALUES (?, ?, ?, ?)",
            (nonce, now, receipt_id, sig),
        )
        conn.commit()
    finally:
        conn.close()


def _seen_nonce(nonce: str) -> tuple[str, str] | None:
    conn = _nonce_conn()
    try:
        row = conn.execute(
            "SELECT receipt_id, sig FROM nonces WHERE nonce = ?", (nonce,)
        ).fetchone()
    finally:
        conn.close()
    return (row[0], row[1]) if row else None


# --- receipts -------------------------------------------------------------


def _receipt_path(receipt_id: str) -> Path:
    return _receipts_dir() / f"{receipt_id}.json"


def persist_receipt(receipt: Receipt) -> None:
    d = _receipts_dir()
    d.mkdir(parents=True, exist_ok=True)
    _receipt_path(receipt.receipt_id).write_text(json.dumps(receipt.to_json(), indent=2))


def load_receipt(receipt_id: str) -> Receipt | None:
    path = _receipt_path(receipt_id)
    if not path.exists():
        return None
    return Receipt.from_json(json.loads(path.read_text()))


def _receipt_id_for(signature_b64: str) -> str:
    # Derived from the signature: unique per signed message, stable across polls,
    # and independent of anything the caller could collide on purpose.
    return hashlib.sha256(signature_b64.encode("utf-8")).hexdigest()[:32]


# --- verification ---------------------------------------------------------


def _verify_signature(receipt: Receipt) -> bool:
    """Recompute the signed bytes and check them against the enrolled key.

    The one function that decides authenticity. Trusts no stored verdict: it
    rebuilds the canonical bytes from the receipt's own fields and re-runs the
    Ed25519 check, so a tampered receipt file fails here.
    """
    keys = load_keys()
    entry = keys.get(receipt.key_id)
    if entry is None:
        return False
    pub = _load_pubkey(str(entry["pubkey"]))
    try:
        sig = b64decode(receipt.signature, validate=True)
    except Exception:  # noqa: BLE001
        return False
    message = canonical_bytes(
        key_id=receipt.key_id,
        timestamp=receipt.timestamp,
        nonce=receipt.nonce,
        body=receipt.body,
    )
    if len(sig) != 64:
        return False
    try:
        pub.verify(message, sig)
        return True
    except BadSignatureError:
        return False


def verify_message(
    *,
    body: str,
    timestamp: str,
    nonce: str,
    signature: str,
    key_id: str,
    label: str = "",
    now: int | None = None,
    skew: int = DEFAULT_SKEW_SECONDS,
) -> Receipt:
    """Verify a freshly arrived phone message. Returns its receipt or raises.

    Order matters. Freshness is checked before the nonce so a stale message is
    rejected as stale (the honest reason) rather than as a replay. The nonce is
    idempotent by design: the phone re-presents the *same* signed payload while
    polling for a slow turn, and re-presenting identical bytes is not an attack --
    reusing a nonce with *different* bytes is, and that is what gets rejected.
    """
    now = int(time.time()) if now is None else now
    _clean_field("nonce", nonce)
    _clean_field("key_id", key_id)

    # Freshness first.
    try:
        ts_int = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise PhoneAuthError("timestamp must be unix seconds (decimal digits)") from exc
    if abs(now - ts_int) > skew:
        raise PhoneAuthError(
            f"message timestamp is {abs(now - ts_int)}s from now, past the "
            f"{skew}s replay horizon -- refusing as stale"
        )

    receipt_id = _receipt_id_for(signature)
    receipt = Receipt(
        receipt_id=receipt_id,
        key_id=key_id,
        timestamp=str(timestamp),
        nonce=nonce,
        body=body,
        signature=signature,
        label=label,
        received_at=now,
    )

    # Nonce: idempotent re-presentation vs. genuine replay.
    seen = _seen_nonce(nonce)
    if seen is not None:
        seen_receipt_id, seen_sig = seen
        if seen_sig == signature and seen_receipt_id == receipt_id:
            # Same bytes again (a poll). Re-verify the signature and hand back the
            # stored receipt; do not treat it as an attack.
            stored = load_receipt(seen_receipt_id)
            if stored is not None and _verify_signature(stored):
                return stored
            # Fall through to a full re-verify if the receipt went missing.
        else:
            raise PhoneAuthError(
                "nonce reuse with different content -- this looks like a replay; "
                "refusing"
            )

    if not _verify_signature(receipt):
        raise PhoneAuthError(
            f"signature does not verify against enrolled key {key_id!r} "
            "(unknown key, tampered body, or wrong signer)"
        )

    persist_receipt(receipt)
    _remember_nonce(nonce, receipt_id, signature, now, skew)
    return receipt


def reverify(receipt_id: str) -> tuple[bool, str]:
    """Re-check a stored receipt. What `--provenance` calls for a phone message.

    Freshness is NOT re-checked: a receipt is a historical record, and the
    question here is "did he really sign this?", not "is it still current". A
    tampered receipt or an unknown/mismatched key fails.
    """
    receipt = load_receipt(receipt_id)
    if receipt is None:
        return False, f"no phone receipt {receipt_id!r} on this machine"
    if _verify_signature(receipt):
        label = f" ({receipt.label})" if receipt.label else ""
        return True, (
            f"VERIFIED: phone message signed by enrolled key {receipt.key_id!r}{label} "
            f"at unix {receipt.timestamp}. body_sha256_16={receipt.body_sha256_16}. "
            f"What was signed, verbatim:\n> {receipt.body}"
        )
    return False, (
        f"NOT VERIFIED: receipt {receipt_id!r} does not check out against enrolled "
        f"key {receipt.key_id!r} -- tampered, or the key is not enrolled"
    )


# --- a small CLI: `python -m hotline.phoneauth <cmd>` --------------------


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m hotline.phoneauth",
        description="Enroll a phone key and inspect phone-message receipts.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_enroll = sub.add_parser("enroll", help="authorise a phone public key")
    p_enroll.add_argument("key_id", help="a stable id for this device, e.g. 'iphone-1'")
    p_enroll.add_argument("pubkey_b64", help="base64 of the 32-byte Ed25519 public key")
    p_enroll.add_argument("--label", default="", help="human label for the key")

    sub.add_parser("list", help="list enrolled phone keys")

    p_verify = sub.add_parser("verify", help="re-verify a stored receipt by id")
    p_verify.add_argument("receipt_id")

    args = parser.parse_args(argv)

    if args.cmd == "enroll":
        try:
            enroll(args.key_id, args.pubkey_b64, label=args.label)
        except PhoneAuthError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"enrolled {args.key_id!r} -> {_keys_path()}")
        return 0

    if args.cmd == "list":
        keys = load_keys()
        if not keys:
            print(f"no phone keys enrolled ({_keys_path()})")
            return 0
        for key_id, entry in sorted(keys.items()):
            label = entry.get("label") or ""
            print(f"{key_id}\t{entry.get('pubkey', '')[:16]}...\t{label}")
        return 0

    if args.cmd == "verify":
        ok, summary = reverify(args.receipt_id)
        print(summary)
        return 0 if ok else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(_main())
