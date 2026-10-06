# TODO

Deferred items. None of these are being worked on; he moved them here on
2026-09-17 (Discord `1550083615906463785`, provenance-verified). Remove a line
when it is done or he drops it, instead of marking it done.

| # | item | added | blocked on |
|---|---|---|---|
| 1 | Copy `HOTLINE_API_KEY` to Pigion and arm `bsajt-verify-watch.timer` there. He said yes on 09-13 ("move it sure"). The only copy on Pigion today is the pre-existing `~/.config/hotline-frontdoor.env`. | 09-13 | bogdanstamenovic.com is not deployed (HTTP 000 on 09-17), so there is nothing to verify yet |
| 2 | `track-slot-0800` in `wake` ends with `then_do=poweroff`, and its presence guard exempts the operator, so a timer-booted operator gets about 5 minutes. Change `then_do` if boot operators should be able to finish work. | 09-16 | his call |
| 3 | **standin**: meeting stand-in (full-duplex voice + talking avatar). Archived 2026-10-04 at his request (Discord `1556266429051969669`: "not a priority now"). Bench numbers: 2.87 s median answer vs 10.65 s old pipeline, 0.59 s barge-in, face 25 fps. Repo and handoff: `~/data/standin/HANDOFF.md`. Also pending: cvoice's `/unload` doesn't release its ~3 GB Whisper scorer, and the int8_float16 measurement. | 10-04 | his three answers: ~60 s of video or a photo for the face; v4l2loopback yes/no (recommended no); first live test meeting and whether it presents as him or as his assistant |
| 4 | **KinReply billing rule (from W1 #29, 2026-10-04)**: an AI reply RESERVED just before month-end midnight UTC and RECORDED just after is charged to the NEW month (api internal/internalapi reply.go:524,722, usage.go:242, knowledge.go:262). Decide which period a reservation belongs to. Recommendation: the month of RESERVATION, consistent with the quota gate's refusal, which already uses reservation time. No test covers it; it's not a double clock read. ALSO (W1 #30, 10-04): a double delivery caused by our ambiguous-send retry counts ONCE against the seller's monthly DM quota (default; a one-line flip if he wants both copies counted). | 10-04 | billing work (#1 Billing) starting; it's his rule to set |

## Context for #1

- `bsajt-verify.timer` on archserver IS armed (user unit, every boot + 6 h).
  Against a dead site it times out, exits 1 and leaves a `failed` unit. It does
  **not** ring him (journal-verified 09-17 08:03 and 11:55).
| 5 | **After db 00057 (follows_us DROP, #22) is DEPLOYED**: ping legal so the export README's SCHEMA VERSIONS sentence can widen from "no longer asks for it or exports it" to "asks for, keeps or exports" (legal 10-05). | 10-05 | trigger = 00057 on prod | |
| 6 | **odds attempt-semantics bug** (found 10-06, run 20261006-124727; root cause named by odds' own critic, round 4): for a question where EVERY attempt must go well ("10 rides without a bad outcome"), odds treats the N attempts as N tries at ONE success, so overall p50 = 1 - (1-p)^N ≈ 100% for every strategy and the verdict prints literal "~>99%". The correct form is p^N (all must succeed). The finalize pass fixed it by hand in the follow-up (98% vs 89%), but the tool's model has no "all attempts must succeed" mode, so it recurs on any risk-style question. Fix idea: a success_mode (any|all) in the framing, set by reframe from the question, used by the Monte Carlo and the verdict; plus a test that a risk question with N=10 compounds as p^N. Also: two researcher subagents timed out at 540s (Q14, Q15). Ask Bogdan before changing his tool. | 10-06 | his tool; not urgent | |
