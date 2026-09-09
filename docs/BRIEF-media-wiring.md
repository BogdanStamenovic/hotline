# Task: give an answered hotline call audio

`~/data/hotline-ios/server`. Bogdan answered a real SIP call on 2026-09-09 at
15:44:57Z and heard silence. Ring works; there is no media leg. Two independent
causes, both of which must be fixed or the call is silent again.

## Cause 1 — `on_answer` is never passed

`SipTransport.__init__` (`src/hotline_ios/ring/sip.py:164`) takes
`on_answer: object | None = None`. `_finish_answered` (`sip.py:559`) calls it
with `(sdp_answer_text, media_socket, srtp_key, srtp_salt)` when he picks up,
and otherwise ACKs the 200 and hangs up.

**Nothing anywhere passes it.** `daemon.py:2633` constructs `SipTransport()`
bare. `VoiceCall` (`media/voicecall.py:231`) appears in its own module and
`tests/test_voicecall.py` and nowhere else. The media engine is complete —
`MediaPump` on its own thread, SRTP both directions, barge-in retaining frames
that arrive mid-utterance — and unreachable from the live path.

The seam is `build_transport` in `daemon.py:2601-2653`, the single construction
path for every doorbell.

## Cause 2 — the SDP advertises an unroutable media address

`SipTransport.media_host` reads `SIP_MEDIA_HOST` and **it is set in neither
`.env`** (`~/data/hotline-ios/.env`, `~/data/hotline/.env` — those are the only
files `load_env()` reads). Its own comment states the consequence:

> There is no ICE here, so when it is not, nothing tells us: the call connects
> and is silent.

Empty means the SDP offers whatever local address the SIP socket got — a
192.168.x address, unroutable from his phone or from linphone.org's media relay
(`176.31.149.179`). Fixing cause 1 alone would very likely reproduce the same
silence, and that would read as the wiring having failed.

Note for the record: `/proc/<pid>/environ` will NOT show `SIP_*` for the running
daemon. Those reach `os.environ` through `load_env()` after exec. Absence there
is not evidence.

## What to build

A conversation loop invoked from `on_answer`, using the existing API:
`send_audio` / `send_silence` / `receive_turn` / `receive_audio` / `enrol_voice`
/ `stats` / `close`. Speak a greeting, listen for a turn, transcribe, hand it to
the session, speak the answer, repeat until he hangs up. cvoiced is at
`100.72.2.62:8760` (`/speak`, needs the API key; it returns 401 without one) and
is already warm at 6452 MiB.

Do **not** wire `speculate.py` in this task. It is the next task and it depends
on this one landing first.

## Constraints

- Do not "clean up" `Restart=always` on the unit, or the lazy media-socket bind,
  or the TLS default. Each carries a comment explaining the constraint that
  forced it; `sip.py:10-95` is worth reading in full before touching anything.
- Keep the doorbell behaviour reachable. A `SipTransport` built without
  `on_answer` must still ACK-and-hang-up: `hotline-page` and the confirmed-ring
  path depend on a ring that does not hold a call it cannot talk on.
- `ConfirmedRing` cancels the INVITE at 8 s without a `180`. Under Do Not
  Disturb no `180` arrives even though the phone rings. Do not "fix" that here.

## Definition of done

Both suites green (`hotline` 501, `hotline-ios` 313/8s) is necessary and **proves
nothing** — the media suite was fully green while the engine was dead code. Done
means a real call where he hears a voice and it hears him. That call needs him
present, and the operator arranges it. Report back with the diff and the stats
from `VoiceCall.stats()`; do not claim it works without a call.
