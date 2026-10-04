# TODO

Deferred items. None of these are being worked on; he moved them here on
2026-09-17 (Discord `1550083615906463785`, provenance-verified). Remove a line
when it is done or he drops it, instead of marking it done.

| # | item | added | blocked on |
|---|---|---|---|
| 1 | Copy `HOTLINE_API_KEY` to Pigion and arm `bsajt-verify-watch.timer` there. He said yes on 09-13 ("move it sure"). The only copy on Pigion today is the pre-existing `~/.config/hotline-frontdoor.env`. | 09-13 | bogdanstamenovic.com is not deployed (HTTP 000 on 09-17), so there is nothing to verify yet |
| 2 | `track-slot-0800` in `wake` ends with `then_do=poweroff`, and its presence guard exempts the operator, so a timer-booted operator gets about 5 minutes. Change `then_do` if boot operators should be able to finish work. | 09-16 | his call |
| 3 | **standin**: meeting stand-in (full-duplex voice + talking avatar). Archived 2026-10-04 at his request (Discord `1556266429051969669`: "not a priority now"). Bench numbers: 2.87 s median answer vs 10.65 s old pipeline, 0.59 s barge-in, face 25 fps. Repo and handoff: `~/data/standin/HANDOFF.md`. Also pending: cvoice's `/unload` doesn't release its ~3 GB Whisper scorer, and the int8_float16 measurement. | 10-04 | his three answers: ~60 s of video or a photo for the face; v4l2loopback yes/no (recommended no); first live test meeting and whether it presents as him or as his assistant |
| 4 | **KinReply billing rule (from W1 #29, 2026-10-04)**: an AI reply RESERVED just before month-end midnight UTC and RECORDED just after is charged to the NEW month (api internal/internalapi reply.go:524,722, usage.go:242, knowledge.go:262). Decide which period a reservation belongs to. Recommendation: the month of RESERVATION, consistent with the quota gate's refusal, which already uses reservation time. No test covers it; it's not a double clock read. | 10-04 | billing work (#1 Billing) starting; it's his rule to set |

## Context for #1

- `bsajt-verify.timer` on archserver IS armed (user unit, every boot + 6 h).
  Against a dead site it times out, exits 1 and leaves a `failed` unit. It does
  **not** ring him (journal-verified 09-17 08:03 and 11:55).
