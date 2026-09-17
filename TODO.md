# TODO

Deferred items. None of these are being worked on; he moved them here on
2026-09-17 (Discord `1550083615906463785`, provenance-verified). Remove a line
when it is done or he drops it, instead of marking it done.

| # | item | added | blocked on |
|---|---|---|---|
| 1 | Copy `HOTLINE_API_KEY` to Pigion and arm `bsajt-verify-watch.timer` there. He said yes on 09-13 ("move it sure"). The only copy on Pigion today is the pre-existing `~/.config/hotline-frontdoor.env`. | 09-13 | bogdanstamenovic.com is not deployed (HTTP 000 on 09-17), so there is nothing to verify yet |
| 2 | `track-slot-0800` in `wake` ends with `then_do=poweroff`, and its presence guard exempts the operator, so a timer-booted operator gets about 5 minutes. Change `then_do` if boot operators should be able to finish work. | 09-16 | his call |

## Context for #1

- `bsajt-verify.timer` on archserver IS armed (user unit, every boot + 6 h).
  Against a dead site it times out, exits 1 and leaves a `failed` unit. It does
  **not** ring him (journal-verified 09-17 08:03 and 11:55).
