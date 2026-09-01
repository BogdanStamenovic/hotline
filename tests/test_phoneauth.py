"""Prove the phone-message verification path end to end, with a simulated phone.

There is no real device key here: a test Ed25519 keypair stands in for the phone,
which is exactly what the app will hold. Every store (keys, receipts, nonces) is
redirected into a tmp dir so this never reads or writes his real state.
"""

from __future__ import annotations

import time
from base64 import b64encode

import pytest
from nacl.signing import SigningKey

from hotline import phoneauth


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("HOTLINE_PHONE_KEYS", str(tmp_path / "keys.json"))
    monkeypatch.setenv("HOTLINE_PHONE_RECEIPTS", str(tmp_path / "receipts"))
    monkeypatch.setenv("HOTLINE_PHONE_NONCES", str(tmp_path / "nonces.db"))
    yield


def _phone(key_id: str = "iphone-1", label: str = "his iPhone"):
    """Enroll a keypair and return a signer that plays the phone's role."""
    priv = SigningKey.generate()
    pub_b64 = b64encode(bytes(priv.verify_key)).decode()
    phoneauth.enroll(key_id, pub_b64, label=label)

    def sign(body: str, *, timestamp=None, nonce="n1", key=key_id):
        timestamp = str(int(time.time())) if timestamp is None else str(timestamp)
        msg = phoneauth.canonical_bytes(
            key_id=key, timestamp=timestamp, nonce=nonce, body=body
        )
        sig = b64encode(priv.sign(msg).signature).decode()
        return {
            "body": body,
            "timestamp": timestamp,
            "nonce": nonce,
            "signature": sig,
            "key_id": key,
        }

    return sign


def test_valid_message_verifies_and_leaves_a_receipt():
    sign = _phone()
    payload = sign("shutdown now", nonce="a1")
    receipt = phoneauth.verify_message(**payload)
    assert receipt.body == "shutdown now"
    # And it can be re-verified off disk the way --provenance would.
    ok, summary = phoneauth.reverify(receipt.receipt_id)
    assert ok
    assert "VERIFIED" in summary
    assert "shutdown now" in summary


def test_tampered_body_fails():
    sign = _phone()
    payload = sign("do nothing", nonce="b1")
    payload["body"] = "shutdown now"  # attacker edits the message, keeps the sig
    with pytest.raises(phoneauth.PhoneAuthError, match="signature does not verify"):
        phoneauth.verify_message(**payload)


def test_unknown_key_fails():
    sign = _phone(key_id="iphone-1")
    payload = sign("hi", nonce="c1", key="iphone-1")
    payload["key_id"] = "not-enrolled"
    with pytest.raises(phoneauth.PhoneAuthError):
        phoneauth.verify_message(**payload)


def test_wrong_signer_fails():
    # A different keypair signs, but claims an enrolled key_id.
    _phone(key_id="iphone-1")
    attacker = SigningKey.generate()
    body, ts, nonce = "shutdown now", str(int(time.time())), "d1"
    msg = phoneauth.canonical_bytes(key_id="iphone-1", timestamp=ts, nonce=nonce, body=body)
    forged = b64encode(attacker.sign(msg).signature).decode()
    with pytest.raises(phoneauth.PhoneAuthError, match="signature does not verify"):
        phoneauth.verify_message(
            body=body, timestamp=ts, nonce=nonce, signature=forged, key_id="iphone-1"
        )


def test_stale_timestamp_rejected():
    sign = _phone()
    payload = sign("shutdown now", timestamp=int(time.time()) - 10_000, nonce="e1")
    with pytest.raises(phoneauth.PhoneAuthError, match="stale"):
        phoneauth.verify_message(**payload)


def test_replay_with_different_content_rejected():
    sign = _phone()
    first = sign("say hello", nonce="reused")
    phoneauth.verify_message(**first)
    # Same nonce, different body: a replay attempt.
    second = sign("shutdown now", nonce="reused")
    with pytest.raises(phoneauth.PhoneAuthError, match="replay"):
        phoneauth.verify_message(**second)


def test_identical_resend_is_idempotent_not_a_replay():
    # The phone polls a slow turn by re-POSTing the same signed payload.
    sign = _phone()
    payload = sign("what's the status?", nonce="poll-1")
    r1 = phoneauth.verify_message(**payload)
    r2 = phoneauth.verify_message(**payload)  # the poll
    assert r1.receipt_id == r2.receipt_id


def test_tampered_receipt_on_disk_fails_reverify():
    sign = _phone()
    receipt = phoneauth.verify_message(**sign("harmless", nonce="f1"))
    # Someone edits the stored receipt to change the message after the fact.
    stored = phoneauth.load_receipt(receipt.receipt_id)
    stored.body = "shutdown now"
    phoneauth.persist_receipt(stored)
    ok, summary = phoneauth.reverify(receipt.receipt_id)
    assert not ok
    assert "NOT VERIFIED" in summary


def test_newline_in_nonce_rejected():
    sign = _phone()
    with pytest.raises(phoneauth.PhoneAuthError, match="single line"):
        phoneauth.verify_message(**sign("hi", nonce="bad\nnonce"))


def test_enroll_rejects_non_ed25519_key():
    with pytest.raises(phoneauth.PhoneAuthError):
        phoneauth.enroll("iphone-x", b64encode(b"too-short").decode())


def test_reverify_missing_receipt():
    ok, summary = phoneauth.reverify("deadbeef")
    assert not ok
    assert "no phone receipt" in summary
