# #agent-wake-general

Archived 2026-09-05 before `hotline --done` deleted the channel.

### 2026-09-05T10:35:14.117000+00:00 — hotline

Read the whole repo. Confirming hotline-80's read: `wake add` takes `--at` only, every task is one-shot, and README's Limitations even says so in as many words ("there is no recurrence syntax"). So Bogdan's premise that wake already does recurring is wrong -- it does not, and that is exactly why the 08:00 run has been re-armed by hand nightly.

Plan, three pieces:
1. `--every` on `wake add` -> a `repeat` column on the task row; the firing path re-arms the next occurrence instead of leaving it `fired`.
2. install.sh grows MAC / broadcast / hotline-url prompts, same `[ -t 0 ]` + `WAKE_INSTALL_*` discipline as the existing four.
3. Scrub 100.72.2.62 / a8:a1:59:fd:4d:13 / archserver / pigion for RFC 7042 + RFC 5737 documentation values.

Plus the `--on <server-name>` unfireable-task bug. Starting with recurrence; baseline suite is green at 194.

### 2026-09-05T10:43:15.404000+00:00 — hotline

Recurrence works end to end, verified against a real `wake serve`.

```
wake add --at 2026-09-06T06:00:00Z --every 1d --task "..."
wake add --at +5m --every daily|hourly|weekly|12h|90m|3600
```

Shape: one `repeat_seconds REAL` column. On fire the row goes back to `pending` at its next occurrence instead of reaching `fired` -- so there is never a second row racing the first, and the id you wrote down keeps meaning the same schedule forever.

**Missed occurrences, decided and documented:** whole periods are added to the *anchor*, not to now, so a task anchored at 06:00 stays at 06:00. Machine off three days -> exactly one catch-up run, then back on schedule. Same choice systemd's `Persistent=true` makes. Proved it: anchored a 60s task 125s in the past, served for 4s, got exactly 1 tick and the row advanced 10:40:31 -> 10:43:31 (phase kept, two missed slots skipped).

Other decisions:
- A **failed** run still re-arms. A nightly job that errored once must still run tomorrow; stopping the schedule on the first bad night is the exact silent failure this whole task is about. The error is kept on the row, not just in the journal.
- Floor of 60s on `--every`, cron's own floor -- the loop polls every 5s and each fire runs a command with a 300s timeout.
- `--every` with `--backend rtcwake` is refused rather than accepted-and-ignored: rtcwake is armed at add time and no scheduler ever sees it again.
- `cancel` is terminal (verified), and the re-arm is compare-and-set on the revision read before the command ran -- so a cancel landing mid-run stays cancelled rather than being written back to `pending`.

### 2026-09-05T10:43:15.769000+00:00 — hotline

**The `--on` bug is fixed.** The server now answers to both `""` and its own ORIGIN, so `--on <the server>` fires instead of sitting pending forever. Verified: `--on hub` on a server named hub fires, `--on laptop` still correctly does not. `wake list` grew an `on` column and an `every` column, because both of these were invisible failure modes.

**Found a second bug while in there:** the server's `/api/v1/tasks` route rebuilds the Task by hand on the sync-merge path and never copied `then_do` or `timeout_seconds`. So a device pushing `--then poweroff` arrived at the server as a task that would never power anything off. Fixed in the same place.

194 tests still green, ruff and mypy clean. Writing the new tests now, then install.sh.

### 2026-09-05T10:48:00.314000+00:00 — hotline

Recurrence committed (2 commits, not pushed yet). **257 tests passing**, up from 194; ruff and mypy clean; all 17 mutations in `scripts/mutate.sh` still caught.

Verified track's contract by running its exact `_invoke_wake_add` shape against the new build rather than reasoning about it:
- `wake add --at <epoch> --task ... --backend shell --id track-abc --on hub` twice on the same id -> stdout is `track-abc` both times, stderr 0 bytes. Unchanged.
- Recurrence is opt-in: no `--every` means `repeat_seconds` is NULL and the row behaves exactly as before.
- One thing I checked because it could have bitten: `wake add --backend wol` with no target now errors at add time (it falls back to a configured MAC and complains if there is none) instead of arming a task that fails at fire time. track already guards that at `scheduler.py:242`, so it never reaches wake from track.

Added the re-arm-after-run argument to the README as you suggested, as its own subsection -- a run that dies before the re-arm ends the schedule permanently, and a machine that was off when the task was due never re-arms either, both because the re-arm lives inside a run that did not happen.

Noted on history: worktree scrub only. Starting Task B now -- install.sh prompts and the machine-info scrub.

### 2026-09-05T11:15:07.630000+00:00 — hotline

**Both tasks done, pushed to origin/main at 1b457da** (verified with `git ls-remote`, not the local ref). 264 tests (from 194), ruff/mypy/shellcheck clean, 27/27 mutations caught.

**Recurrence.** `wake add --at <anchor> --every <period>` — `1d`, `12h`, `daily`, `hourly`, `weekly`, bare seconds. One `repeat_seconds` column; on fire the row goes back to `pending` at its next occurrence instead of reaching `fired`, so there is never a second row racing the first. Missed occurrences: periods are added to the *anchor*, never to now, so three days off gives exactly one catch-up run and the 06:00 task is still at 06:00. A failed run still re-arms — stopping a schedule on the first bad night is the silent failure the whole feature exists to remove. `cancel` is the only thing that ends it, including mid-run.

**Generalization.** install.sh now asks seven things instead of four: it detects this machine's MAC off the default-route interface and offers it pre-filled, plus the WoL broadcast and the hotline-ios address. Every trace of his hardware is gone from the worktree — MAC, tailnet IP, both hostnames — replaced with RFC 5737 / RFC 7042 documentation values. I cloned the public repo fresh and ran the whole stranger path from it: the installer asks *them*, and `wake add --backend wol --every daily` afterwards needs no typed address.

**The `--on` bug is fixed.** The server now answers to both `""` and its own ORIGIN, so `--on <the server>` fires instead of sitting pending forever.

**Three more bugs found by using it rather than reading it:**
1. The sync-merge path rebuilt the task by hand and never copied `then_do`/`timeout_seconds` — a device pushing `--then poweroff` reached the server as a task that would never power anything off.

### 2026-09-05T11:15:08.099000+00:00 — hotline

2. A pre-`--every` server silently turns a server-owned recurring task into a one-shot *and* wipes the device's own copy. Ran the old code to confirm. Now warned about, free, off the row the server already echoes back.
3. `no such task` named no database, which read as a lost row. Nothing in wake deletes a row — no DELETE, DROP or VACUUM anywhere — so it always means the wrong file. It now says which.

The mutation audit earned its keep again: it caught that my own test for "firing early must not move the timer" used a one-day period, for which the general arithmetic returns the right answer anyway. Deleting the code under test passed the suite.

`stopgap-*` rows untouched. Reported the sequencing to hotline-80 — retire them before arming a recurring rule, or tomorrow 08:00 fires twice.

