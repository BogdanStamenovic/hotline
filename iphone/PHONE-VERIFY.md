# Making phone messages verifiable

Today a phone message is authenticated by the shared `HOTLINE_API_KEY` plus the
Tailscale IP allowlist. That proves *a key-holder* sent it — and the key is
readable by anything running as your uid — not that *you* did, and it leaves no
receipt to re-check. So a phone "shutdown now" has to be re-confirmed over
Discord every time.

This is the phone half of the fix. The server half is built (`hotline/phoneauth.py`,
wired into `hotlined` and `hotline --provenance`). When the phone signs its
messages, each one becomes cryptographically attributable to you and leaves a
receipt a later session can re-verify — the phone analogue of a Discord relay.

## What the phone does

1. **Once, at first run:** generate an **Ed25519** keypair. The private key never
   leaves the device — iOS Keychain, ideally with `kSecAttrAccessibleWhenUnlocked`
   and Secure Enclave protection if you wrap it. Pick a stable `key_id` string for
   the device, e.g. `iphone-1`.

2. **Enroll the public key once** on the box (or laptop over the tailnet):

   ```
   python -m hotline.phoneauth enroll iphone-1 <base64-of-32-byte-public-key> --label "his iPhone"
   ```

   This writes `~/.config/hotline/phone_keys.json` (like `authorized_keys`).
   Only a holder of the matching private key can then sign as you. Re-enroll to
   rotate; list with `python -m hotline.phoneauth list`.

3. **On every message send:** build a fresh `timestamp` (unix seconds) and a fresh
   random `nonce` (any unique single-line string — a UUID is fine), sign the
   canonical bytes below, and add four fields to the JSON body you already POST to
   `/api/v1/claude`:

   ```
   text        (unchanged — the message)
   session_id  (unchanged)
   key_id      "iphone-1"
   timestamp   "1788260996"          decimal unix seconds, as a string
   nonce       "b3f1...".            unique per message, single line
   signature   base64 of the 64-byte Ed25519 signature
   ```

   Unsigned messages still work exactly as before (they just keep the old
   "key-holder only" standing) — so you can ship signing without a flag-day.

## The canonical bytes (the whole contract)

Sign **exactly** these UTF-8 bytes. The body is last so it may contain anything,
including newlines:

```
HOTLINE-PHONE-SIG-v1\n
<key_id>\n
<timestamp>\n
<nonce>\n
<body>
```

i.e. `("HOTLINE-PHONE-SIG-v1\n" + key_id + "\n" + timestamp + "\n" + nonce + "\n" + body)`
encoded UTF-8, where `body` == the `text` you send. `key_id`, `timestamp` and
`nonce` must not contain a newline. Reference implementation: `canonical_bytes()`
in `hotline/phoneauth.py`. Match it byte-for-byte and the two halves agree;
deviate and every signature fails closed.

### Swift (native app)

```swift
import CryptoKit   // Curve25519.Signing

let head = "HOTLINE-PHONE-SIG-v1\n\(keyID)\n\(timestamp)\n\(nonce)\n"
var message = Data(head.utf8)
message.append(Data(body.utf8))
let signature = try privateKey.signature(for: message)   // 64 bytes
let signatureB64 = signature.base64EncodedString()
let publicKeyB64 = privateKey.publicKey.rawRepresentation.base64EncodedString()  // 32 bytes, for enrollment
```

`CryptoKit`'s `Curve25519.Signing` is Ed25519. `rawRepresentation` of the public
key is the 32 bytes to enroll; the signature is the 64 bytes to base64 into
`signature`.

## What the server does with it

- Verifies the signature against your enrolled key, rejects a timestamp more than
  an hour from its clock (kills long-term replay), and rejects a nonce reused with
  *different* content (kills replay within the window). An identical re-POST — the
  Shortcut/app polling a slow turn — is idempotent, not a replay.
- Persists a **receipt** and stamps the relayed message's provenance header with
  `kind=phone`, `phone_verified=true`, and a `receipt` id.
- A later session verifies it off disk, no Discord round-trip:

  ```
  hotline --provenance phone:<receipt_id>
  # or pipe the whole relayed message (header + body) to:
  hotline --provenance -
  ```

  → `VERIFIED: phone message signed by enrolled key 'iphone-1' …` (exit 0), or a
  plain NOT VERIFIED on a tampered or unenrolled message.

The one-hour replay horizon is generous on purpose: the client re-sends the same
signed payload while polling for a slow turn's answer. If the app instead re-signs
each poll with a fresh timestamp+nonce, tighten `DEFAULT_SKEW_SECONDS`.

## Client feasibility — read this before wiring it

- **Native hotline iOS app:** clean path. `CryptoKit` + Keychain do all of the
  above. This is where signed phone messages should live.
- **The Shortcut** (`SHORTCUT.md`): iOS Shortcuts have **no native Ed25519 sign
  action**, so the Shortcut cannot sign on its own. Options if you want the
  Shortcut signed too: call a [Scriptable](https://scriptable.app) script action
  that holds the key and signs (JS Ed25519), or accept that Shortcut messages stay
  unsigned (key-holder standing) and reserve signing for the native app. The
  server treats both correctly — signed gets the receipt, unsigned keeps the old
  honest label — so this is a choice, not a blocker.
