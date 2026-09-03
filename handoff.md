# HOTLINE — worker handoff

> ## STATUS AS OF 2026-09-03 22:30 CEST — AN UNATTENDED RUN IS ARMED FOR 08:00
>
> **READ THIS FIRST, AND THEN DISTRUST IT.** A banner is only true until the machine
> changes underneath it. Verify with `tail watchdog.log`, `last -x -n 12 reboot shutdown`,
> `grep -n "^## " PROGRESS.md | tail`, and `hotline --list` before believing a word.
>
> ### IF YOU ARE THE OPERATOR WAKING UP ON 2026-09-04 — DO NOT INTERFERE
> The box is *meant* to be awake, and it is *meant* to power itself off again. Bogdan asked
> for this: **wake at 08:00 -> `track` runs one research pass -> posts to Discord -> shut down.**
> - **Do not cancel the shutdown, and do not "rescue" the box from powering off.** That is the
>   task succeeding, not a fault.
> - **Do not start long work that would block it.** The shutdown guard blocks on a logged-in
>   human and on real agent work; it deliberately ignores the operator, because
>   `hotline-watchdog.timer` (OnBootSec=2min, every 5min) respawns the operator on EVERY boot
>   and would otherwise keep the box up forever.
> - If 08:00 came and went with no Discord post, the run FAILED — say so plainly, do not
>   invent a result. Check `wake list`, the device unit's journal, and Pigion
>   (`ssh bodas@pigion`, the wake server lives there).
>
> **Architecture (his, not negotiable):** Pigion = wake SERVER (always-on Pi Zero 2 W,
> 192.168.1.8, ~218 MiB RAM). archserver = wake DEVICE (192.168.1.139, MAC
> a8:a1:59:fd:4d:13). Two independent wake paths: server-side WoL primary (same LAN,
> `wakeonlan` already on Pigion), local RTC alarm backup (`/sys/class/rtc/rtc0/wakealarm`).
> Passwordless sudo on both hosts; `ssh bodas@pigion` is passwordless.
> **08:00 is the operator's assumption, not his instruction** — he never named a time.
>
> ### A POWEROFF REHEARSAL WAS RUNNING WHEN THIS WAS WRITTEN (2026-09-03 ~23:00)
> If you are booting shortly after that, **this boot may BE the rehearsal succeeding.** wake-dev
> proved RTC and WoL wake from SUSPEND (S3) by measurement, but tomorrow's run needs wake from
> full power-off (S5), which is a separate BIOS capability — so a real poweroff was the only
> honest test. A boot you cannot explain is the expected outcome, not an incident.
> **Everything was committed and pushed before it ran:**
> hotline `main` a2488f8 · hotline `split-packages` a34b6b0 (on origin) · track b6fba6b
> (origin/main) · wake f805904 (committed, clean, LOCAL ONLY — it has no remote, because
> creating a public repo needs Bogdan's approval; do not push it without asking).
> Three independent ways back if the box went dark: Pigion re-sends the magic packet every 2 min
> for 30 min, `wakeonlan a8:a1:59:fd:4d:13` from any host on 192.168.1.0/24, and the local RTC
> alarm. All three must fail for the box to stay off.

> ### CLAUDE.md WAS REWRITTEN TONIGHT — re-read it, it is not what you remember
> Rewritten from a 90-question interview with him. Old copy at
> `~/.claude/CLAUDE.md.bak.20260903-222154`. The changes that will bite a stale session:
> - **He is a team member, not an authority.** Argue with him; research to win the argument.
>   "Once he gives a reason, execute without further argument" was a previous session's
>   invention and is deleted.
> - **Narrate constantly — he PREFERS being spammed.** What he dislikes is being asked trivia.
>   Ask only what only he can answer. Heartbeat at branching points, event-based.
> - **Sonnet only for research/retrieval/review. Anything that writes real code is Opus.**
> - **"No monolith" means unrelated concerns**, not file size or coupling.
> - Impersonation is allowed but **ask first, every time**.
> - `wake-bogdan.sh` and the `wake-from-pc` skill are DELETED. `call-bogdan` is the escalation.
>
> ### THE "THREE FROZEN FILES" RULE IS DEAD — it was never real
> His words: *"they are frozen cuz like a week ago i just stopped you to do something else. i
> guess the info degraded into worked by me."* They were never his untouchable work; they were
> unfinished work of *ours* that a prohibition outlived its reason for. The work was already
> complete and green, and is now committed as **ce8a211** and pushed (501 tests pass).
> **Treat provenance.py, router.py and test_provenance.py as ordinary files.** If any note
> anywhere still says otherwise, that note is stale — delete it.
>
> **Live agents at time of writing:** `wake-dev` and `track-dev` (both Opus, building the
> 08:00 run), `hotline-split` (Opus, modularizing hotline on branch `split-packages`, rebased
> onto ce8a211, gated: no merge to main and no daemon restart without the operator).
> `dealhunter` was killed by Bogdan himself — do not respawn it unless he asks; the laptop
> hunt is now `track`'s job.
> **Daemons:** hotlined (8788) and hotline-ios.service (8789) both active; the phone bridge
> needs PyNaCl in its OWN venv (`~/data/hotline-ios/server/.venv`) or it crash-loops on a cold
> start — that was fixed tonight and must survive any refactor.


> ## STATUS AS OF 2026-09-02 05:31 CEST — WRITTEN AT A POWER-OFF
>
> **READ THIS FIRST, AND THEN DISTRUST IT.** Written as the box goes down at his
> verified instruction (*"Everything is done shutdown now"*, `kind=human` Discord,
> `0ac1ba7d`, `03:30:49Z`). A banner written at shutdown is only true until the
> machine comes back. Before believing a word below, run:
>
> ```
> tail watchdog.log
> last -x -n 12 reboot shutdown
> grep -n "^## " PROGRESS.md | tail
> ```
>
> If any shows activity after 02 Sep 05:31, this banner is stale and the newest
> `PROGRESS.md` section is the truth.
>
> **State at power-off:** one live session only (the operator). Nothing armed
> (no scheduled shutdown / at / cron / watch-agent), no active or held calls, no
> mail queued, GPU 2 MiB idle, ollama empty. **His three frozen source files
> (provenance.py, router.py, test_provenance.py) still untouched, mtime 27 Aug
> 10:35.** Recoverable: `enp4s0` UP, `Wake-on: g`, `wakeonlan a8:a1:59:fd:4d:13`.
>
> **This session (12:39 boot → 05:31 shutdown) shipped:**
> 1. **The phone-message verifiability SERVER half** — new `phoneauth.py` (Ed25519
>    sign/verify, timestamp+nonce anti-replay, persisted receipts, `hotline
>    --provenance phone:<id>`), wired into `daemon.py`/`pool.ask_soft`/`cli.py`.
>    Committed `ee45635`, pushed to origin/main, and `hotlined` was **restarted
>    onto the new code**. His frozen `provenance.py` was NOT touched. The app half
>    (native iOS signing) is his; contract in `iphone/PHONE-VERIFY.md`. See
>    [[phone-verifiability-server-half-exists]].
> 2. **Sent him files over Discord**: 3 voice-clone mp3s (OmniVoice/Higgs, picked
>    on the `out/asr.json` WER scorecard) + the `cao_mina_1.wav` one-off.
> 3. **A test call** — rang for real (`sip+confirmed`, SIP **180 Ringing** at
>    21:50:13 confirms his phone actually rang, NOT the fake-ring failure mode),
>    but went unanswered (exit 3). The "did your phone physically ring at 21:50?"
>    question was open when he said everything's done — treat as closed.

> ## STATUS AS OF 2026-09-01 03:25 CEST — WRITTEN AT A POWER-OFF
>
> **READ THIS FIRST, AND THEN DISTRUST IT.** Written as the box goes down at his
> verified instruction (*"Yep do it"*, `kind=human` Discord, `03:24:53Z`). **A
> banner written at shutdown is only true until the machine comes back** — that
> has been demonstrated repeatedly. Before believing a word below, run:
>
> ```
> tail watchdog.log
> last -x -n 12 reboot shutdown
> grep -n "^## " PROGRESS.md | tail
> ```
>
> If any shows activity after 01 Sep 03:25, this banner is stale and the newest
> `PROGRESS.md` section is the truth.
>
> **State at power-off:** two live sessions only — the operator, and `data-af`
> (idle, its work done). `data-af` built **wd_gen**, an OSINT/CTF credential
> generator, and **pushed it public** (`github.com/BogdanStamenovic/wd_gen`,
> local HEAD `bda6180` == origin). Nothing armed, ollama idle with no model
> resident, GPU 2 MiB, no mail queued (msmtp is send-only, no spool), no external
> ssh. **His three frozen files still untouched, mtime 27 Aug 10:35.** Recoverable:
> `enp4s0` UP, `Wake-on: g`, `wakeonlan a8:a1:59:fd:4d:13`.
>
> **This session shipped three things** (all pushed): the AskUserQuestion→Discord
> bridge (`hotline` `923760e`), and two hotline-ios call-path fixes (`868c298`,
> `aa414c7`). See the two newest sections at the bottom of this file.
>
> ## ⭐ TOP TASK HE ASKED FOR, 01 Sep 03:24 — make phone-app messages VERIFIABLE
>
> His words: *"Just also log in the handoff to fix that the messages i send from
> the app become verifiable."* Right now a `kind=phone` message (typed in his app,
> HTTP to hotline-iosd) is authenticated only by the shared `HOTLINE_API_KEY` +
> IP allowlist — that proves *a key-holder* sent it (the key is plaintext-readable
> by any process at this uid), **not that he did**, and there is **no receipt** to
> re-fetch and nothing dating it, so the exact bytes replay valid forever. That is
> why tonight's phone *"Shutdown now"* had to be re-confirmed over Discord.
>
> **The fix, to give a phone message a real `hotline --provenance` path like a
> Discord relay has** (design, not yet built — he said shut down):
> 1. **Authenticate as HIM, not as a key-holder:** the app signs each message with
>    a private key held **only on the phone** (Ed25519); the daemon verifies
>    against the public key. A shared symmetric key cannot do this — anything on
>    the box that holds it can forge. Asymmetric signing is the whole difference.
> 2. **Date it + kill replay:** the signature must cover a timestamp and a nonce
>    (or monotonic counter); the daemon rejects stale timestamps and seen nonces.
>    Then "these exact bytes" stop being valid next week.
> 3. **Leave a receipt:** persist the signed envelope so a later session can
>    re-verify it against the stored public key — the phone analogue of re-fetching
>    a Discord message. That is what makes `kind=phone` checkable off a status
>    field. Wire it into `provenance.py` so `--provenance` handles it uniformly.
>

> ## STATUS AS OF 2026-08-31 16:45 CEST — WRITTEN AT A POWER-OFF
>
> **READ THIS FIRST, AND THEN DISTRUST IT.** This banner was written while the box
> was going down at his instruction (*"Okay now im done. Shutdown"*, verified
> `14:42:01Z`). **A banner written at shutdown is only true until the machine
> comes back** — that has now been demonstrated three days running, including once
> where the box returned 34 minutes later and ran a full session nobody recorded.
> Before believing a word below, run:
>
> ```
> tail watchdog.log
> last -x -n 12 reboot shutdown
> grep -n "^## " PROGRESS.md | tail
> ```
>
> If any of those shows activity after 31 Aug 16:45, this banner is stale and the
> newest section of `PROGRESS.md` is the truth.
>
> **State at power-off:** nothing armed, nothing running but the operator, GPU
> free, ollama idle with the model unloaded, no mail queued, no ssh sessions,
> `hotlined` healthy. Root 50%, 35 G free. HEAD pushed and clean except **his
> three frozen files, still untouched at 27 Aug 10:35**. `hotline-ios` down since
> the 29th, still not resumed. The four open items below are unchanged and all his.
>
> **This shutdown is recoverable.** `enp4s0` is UP with `Wake-on: g`;
> `wakeonlan a8:a1:59:fd:4d:13` from pigion or his laptop brings it back.
>
> **He shuts this box down routinely and that is normal.** Do not open your next
> report by treating the poweroff, or the gap that follows it, as a finding — see
> the correction he issued at 14:36:54Z, recorded at the end of `PROGRESS.md`.
>
> ## STATUS AS OF 2026-08-31 16:30 CEST — the model corrections, STILL CURRENT
>
> **AND IT HAPPENED AGAIN: the banner below was a session behind, and two of its
> headline facts were reversed by work done four hours after it was written.**
> The 29th's afternoon (still in `PROGRESS.md` only, now summarised in the last
> section of this file) found that the previous night's measurements were taken
> against a **hardcoded ollama blob hash that belonged to a different model**.
> So, corrected and re-verified by direct probe on this boot (31 Aug 16:20):
>
> - **`n_ctx_train = 262144` is NATIVE and REAL.** §0a below says the real
>   trained context is 40960. **That is wrong** — it was measured on
>   `JOSIEFIED-Qwen3:8b` (blob `sha256-1de498fe…`). His model is blob
>   `sha256-18b2ed08…`, `general.name = Heretic_Manual_Merged`, `arch qwen35`,
>   `n_ctx_orig_yarn = 262144`.
> - **turbo3 is NOT moot — it is the only reason 262k fits on the 4060.**
>   §0a below says its benefit is moot here. **Also wrong.** At the full 262144:
>   f16 KV 8192 MiB → OOM, q8_0 4352 MiB → OOM, **turbo3 1600 MiB loads and
>   serves.** `~/data/llama-turbo3` is now 670 MB and is load-bearing, not spare.
>
> Also landed on the 29th and in no banner until now: the `gh` token is stored
> **plaintext in `~/.config/gh/hosts.yml` (chmod 600)** because this headless box
> has no unlocked keyring — the token was never expiring; his public fork
> `BogdanStamenovic/turbo3-cuda` and upstream PR `Madreag/turbo3-cuda#2` are open;
> and `hotline-standup@hotline-ios.timer` was killed at his instruction
> (**verified still disabled+inactive after this boot**, not assumed).
>
> **The lesson is now three-for-three: this file's top banner has been a session
> behind on the 29th, the 28th and today.** Do not trust it. `watchdog.log`,
> `last -x`, and `grep -n "^## " PROGRESS.md | tail` are what actually tell you
> where things stand.
>
> **31 Aug boot:** he ran `sudo shutdown now` himself at 15:38 over ssh from
> `100.103.46.118`; box back **16:14**, him poking it over ssh again by 16:18.
> Nothing armed, no agent involved, no Discord message stranded — newest message
> anywhere is still 29 Aug 13:39Z.
>
> *(Everything below is the 29 Aug 12:20 banner, kept for its own history and
> corrected above where it is now wrong.)*
>
> ## STATUS AS OF 2026-08-29 12:20 CEST — SUPERSEDED BY THE BLOCK ABOVE
>
> 0. **THE BANNER BELOW WAS WRITTEN AT A POWER-OFF AND WENT STALE IN 34 MINUTES.**
>    It was dated *28 Aug 23:07, "written at power-off, replaces all earlier
>    banners"* — and the box came back at **23:41**, ran a full session
>    (`a030b832`), and was shut down by him at **01:34**. Everything that session
>    did is in `PROGRESS.md` and was in **no** banner: `piccolo-gorgone:9b` on
>    ollama, the q8_0 KV bridge, and the TurboQuant/llama.cpp build. **A handoff
>    written at shutdown is only current until the machine comes back.** Read the
>    watchdog log (`watchdog.log`) and `grep -n "^## " PROGRESS.md | tail` before
>    believing any banner's claim to be newest — including this one.
> 0a. **Where the night actually landed (28→29 Aug):**
>    - `piccolo-gorgone:9b` is installed on ollama and is **his live model** —
>      OpenHands on `100.103.46.118` points at `:11434`. Serving 100% GPU at 64k
>      context via `/etc/systemd/system/ollama.service.d/30-kv-quant.conf`
>      (`OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`). Survives reboots.
>    - **The model's real trained context is 40960**, not the 262144 the GGUF
>      advertises (`rope scaling = linear`, no YaRN). Beyond ~40k is extrapolation.
>    - **TurboQuant works and is not needed here.** turbo3 KV built, served and
>      proven coherent at `~/data/llama-turbo3` (460 MB), but fp16 and q8_0 already
>      cover this model's whole 40k window on the 4060, so its benefit is moot
>      until a model actually trained past 100k. Kept deliberately.
>    - **It answers into `reasoning`, not `content`.** A short `max_tokens` returns
>      `content: ""` with `finish_reason: length` — a working endpoint that reads
>      as dead. Do not diagnose the server for that.
> 0b. **He shut the box down himself both times** — `sudo shutdown now` on pts/1,
>    01:34 on the 29th. No agent, nothing armed, no crash. The long gaps are him
>    asleep, not a detection failure.
>
> *(Everything below was the 28 Aug 23:07 banner and is still true except where §0
> above corrects it.)*
>
> ## STATUS AS OF 2026-08-28 23:07 CEST — SUPERSEDED BY §0, OTHERWISE CURRENT
>
> 1. **YOU ARE AN OPERATOR, NOT A BUILDER.** His own words, 2026-08-27 20:52:
>    *"the point of you is exactly this kind of work administration checking
>    shutting down controlig other sessions."* Run the agents; do not grind a
>    checklist. **`PLAN.md` is background, not a task list.** The spawn prompt says
>    all of this and no longer lies.
> 2. **He is REACHABLE but not always fast.** He answered within 36 minutes on the
>    28th and then went quiet for over two hours with questions outstanding.
>    Neither is a fault, and neither is a reason to act for him. **Read Discord
>    before believing anything, including this banner.**
> 3. **The 27th's midnight poweroff happened, cleanly.** The box came back
>    **12:47 on the 28th** — no timer here or on pigion did that, so someone woke
>    it; almost certainly him. **The ethernet cable is IN now** (`enp4s0` LOWER_UP,
>    `Wake-on: g`), so `wakeonlan a8:a1:59:fd:4d:13` is real. Every note in this
>    file saying the cable is unplugged, or that a shutdown is one-way, is stale.
> 4. **Messages sent while the box is OFF reach nothing.** Scan channel history
>    after any boot. *(On the 28th there were none — checked, not assumed.)*
> 5. **Do not spam him.** One consolidated message beats five. If he says call and
>    not page, pass **`--no-fallback`**. `/health` `ring_ready: true` is not proof
>    his phone rings.
> 6. **Still uncommitted on purpose — and it is THREE files, not four:**
>    `provenance.py`, `router.py`, `tests/test_provenance.py`, frozen at 27 Aug
>    10:35. `PROGRESS.md` is also uncommitted but it is the **live operator log**,
>    not a pending decision. Asked on the 26th, still unanswered. **Do not commit
>    it for him. Stage by explicit path here, never `git add -A`.**
> 7. **`hotline-ios` SHIPPED that build** — the header row's chips had never
>    answered a tap since they shipped, which is what he was reporting when he said
>    twice that the row "felt wrong". `5948d2fd` is staged and `26669c8c` is held
>    as rollback. It is awake, idle, and holding for direction.
> 8. **The app's deadline is 3 September 18:33 and IT IS NOT A PROBLEM.** He said
>    so himself, 28 Aug 17:00Z: *"Its not my first time sideloading apps. Its
>    really not a rpoblem doing it weekly"*. **Five agents have corrected that
>    date, paged about it, or proposed engineering around it; the correct amount of
>    all three was zero.** State it once and move on. Keep only the subtle part:
>    `profile-watch.py` reports the *Apple account's* soonest profile, the phone's
>    clock comes from the **install**, and the staged `.ipa` carries no
>    `embedded.mobileprovision` because signing happens at install time.
> 9. **Disk: root is at 48%, 36 G free** (was 93% this morning). At his instruction
>    on the 28th: both snapshots deleted, the 5.6 G `torch`/`triton`/`nvidia-*`
>    stack removed from `.venv`, and **all scheduled snapshotting turned off** —
>    timeshift deleted `/etc/cron.d/timeshift-hourly` itself. **484 tests still
>    pass and `hotlined` is fine**, because `audio.py` imports torch lazily and
>    `bot.py` types the voice call loosely; both say so in comments, so do not
>    "tidy" either. Voice stack restores with one `uv` command —
>    `backups/voice-stack-removed-20260828.md`.
> 9a. **THERE ARE NOW ZERO SNAPSHOTS AND NO AUTOMATIC ONES WILL APPEAR.** His rule,
>    verbatim: *"Snapshots should be made only if a core part is changed"*. So
>    **before touching anything that boots this machine** — kernel, bootloader,
>    initramfs, display stack, `pacman -Syu`, boot-critical `/etc` — take one:
>    `sudo timeshift --create --comments "before <thing>" --tags O`. First one needs
>    **15.5 G**; check `df` first. Tag `O`, not `D`. Ordinary work does not qualify.
>    Verified off by running cron's own command: `timeshift --check --scripted`
>    answers *"Scheduled snapshots are disabled - Nothing to do!"*
> 10. **`hotline --resume` is broken twice over.** It starts the agent in the
>    **resuming session's cwd** — pass `--cwd <dir>`, which does fix it — and it
>    **comes up unbriefed**, answering with a summary of its own handoff and never
>    mentioning your message. Reproduced 28 Aug after being logged as a one-off on
>    the 27th. **Resume, then deliver the brief again with `--to`, and check it
>    landed.**
> 11. **RELAY HIS WORDS, NOT YOUR SUMMARY OF THEM.** On the 28th his "re-signing
>    weekly is not a problem" was relayed as *"he does not want the reminder"*, and
>    the peer disabled `hotline-profile-watch.timer` on the strength of it. Restored.
>    `--warrant` lets a receiver check the original but it will not think to, and
>    **the gap between what he wrote and what you wrote is invisible from the other
>    end.** Quote him.
>
> *Newest material is at the BOTTOM. This banner exists because the top is what
> actually gets read, and workers acted on stale premises sitting right here.*


You are the **operator** on hotline. Read this file, then read Discord, then find
out what actually needs running. `PLAN.md` is the full architecture and is worth
reading as **background** — it is not your task list, and "CURRENT STATE" below is
a record of a build that is essentially done, not a queue to work through. Append
to `PROGRESS.md` as you go.

*(This paragraph used to say "Bogdan is AWAY... continue from CURRENT STATE". He
corrected that on 2026-08-27; see the banner. It is noted rather than silently
edited because five workers were steered by it.)*

## Rules for this run

1. **Narrative log.** Append everything to `PROGRESS.md` in order — what you
   tried, what failed, why. Bogdan reads the reasoning, not just outcomes.
2. **Always run it.** Nothing is done until executed. Never mark a test green
   that isn't. Environmental/flaky skips are allowed but must be logged loudly
   with a TODO.
3. **Never fabricate a result.** An honest dead end beats a fake success.
4. **Context discipline.** Run `~/.claude/bin/ctx` at every phase boundary.
   - <60% → keep going
   - 60-75% → finish the current phase, then hand off
   - >75% → STOP. Update this file with full state. Then run
     `~/.claude/bin/hotline-run respawn` and exit. Your replacement reads this file.
5. **Fan out.** Standing authorization for subagents. Pass `model: "sonnet"`.
   Their final message is the deliverable — never point many agents at one file.
6. **Email on a real blocker**, using the `send-email` skill. Not for progress
   pings — only when you are genuinely stuck and cannot route around it, or when
   all phases are done. Subject prefix `[hotline]`.
7. **Reversibility.** Phase 0 (timeshift) must complete before any system-level
   change. Targeted `tar` backups of any path you edit, regardless.

## Decisions already made — do not re-litigate

- **No money.** "Agent rings Bogdan" = escalating Discord `@mention` push. Not PSTN.
- **iPhone.** No self-hosted SIP/Matrix ringing (needs PushKit + vendor APNs certs).
- **Full bypass permissions**, gated on his Discord user ID.
- **Treat WoL as set up and working.** The ethernet cable is NOT plugged in yet
  (`enp4s0` is NO-CARRIER) and won't be for a while. Bogdan's explicit instruction:
  build the wake layer as if it works, write the persistent config so it self-arms
  when carrier appears, and do not block on it. Test what can be tested (the magic
  packet leaves Pigion correctly); mark the end-to-end wake as UNVERIFIED-BY-DESIGN.

## Verified facts (do not re-derive)

- Host `archserver`, Arch, kernel 7.1.9. RTX 4060 8GiB (609MiB in use), Ryzen 5 5600,
  15GiB RAM. Boots to `multi-user.target`, gdm disabled but **currently running**.
- `claude` 2.1.241 at `/opt/claude-code/bin/claude`, **logged in** (Bogdan confirmed
  he ran `/login`), `subscriptionType: max`, creds at `~/.claude/.credentials.json`.
- **Live-session IPC (verified live):** `~/.claude/sessions/<pid>.json` gives
  `{sessionId, cwd, messagingSocketPath, name}`; sibling `<pid>.<64hex>.key` holds the
  token; socket at `/run/user/1000/cc-socks/<pid>.sock` speaks newline-delimited JSON:
  `{"type":"auth","token":"..."}` then `{"type":"user","message":{"role":"user","content":"..."}}`.
  `claude agents --json` enumerates sessions. **Inject-only** — pair with a `Stop`
  hook for replies. See memory `reference-claude-live-session-ipc`.
- **`Stop` hook** fires on turn completion, gets `{"session_id"}` on stdin.
  Shape: `{"hooks":{"Stop":[{"matcher":"","hooks":[{"type":"command","command":"...","timeout":30}]}]}}`
- `desktop on` is **already passwordless** — `/etc/sudoers.d/10-wheel-nopasswd` grants
  `%wheel ALL=(ALL:ALL) NOPASSWD: ALL`, bodas is in wheel. `/usr/local/bin/desktop`
  only calls `systemctl start gdm|stop gdm|isolate multi-user.target`. Nothing to build.
- **Python: use a `uv`-managed 3.12 venv**, NOT system 3.14. `discord.py[voice]` pins
  PyNaCl<1.6 (no 3.14 wheel); kokoro blocked <3.13; CTranslate2 wants CUDA 12 but Arch
  ships 13.3. Pinning 3.12 kills all three at once.
- **Use py-cord**, not discord.py (its voice extra has PyNaCl>=1.6.2 + davey for Discord's
  mandatory DAVE E2EE). Do NOT use Node `@discordjs/voice` — month-long DAVE receive
  outage early 2026, fix unconfirmed.
- **Prior art:** `~/.hermes/hermes-agent/plugins/platforms/discord/adapter.py` has a
  working MIT `VoiceReceiver` (~350 lines: RTP/NaCl/DAVE/Opus + silence segmentation).
  Reference only if py-cord's sink misbehaves. Do NOT adopt Hermes wholesale.
- **Pigion**: Pi Zero 2 W, Debian 13, 415MiB RAM / ~237MiB free, 107MiB zram used,
  36d uptime. LAN 192.168.1.8, archserver 192.168.1.9 — **same /24, same L2 domain**,
  so a broadcast magic packet will reach. `enp4s0` MAC = `a8:a1:59:fd:4d:13`.
  python3 3.13.5, PEP668 externally-managed (use apt or a venv; `/opt/pigion/.venv` is
  the existing precedent). `python3-websockets 15.0.1` available via apt.
  Egress to discord.com verified 200 OK. No tmux, no jq. **Do not install discord.py
  there** (60-150MB RSS) — hand-roll a raw websocket gateway client (~25-45MB) doing
  only IDENTIFY + HEARTBEAT + VOICE_STATE_UPDATE, no caching.
- **`pigion.service`** = "voice-first todo server", `/opt/pigion`, user `pigion`,
  port 8787, venv `/opt/pigion/.venv`, env `/etc/pigion.env`, db `/var/lib/pigion/pigion.db`.
  Source mirror at `~/pigion-todo`. **It is live and in daily use — do not break it.**
  Add a new endpoint/service alongside; never modify its todo behaviour.
  The iPhone Shortcut recipe is `~/pigion-todo/iphone/SHORTCUT.md`.
- Timeshift 25.12.4 installed, **zero snapshots, unconfigured**. Root is
  `/dev/nvme0n1p4` ext4, 73G total / 43G avail.
- `wake-bogdan.sh` at `~/.claude/bin/wake-bogdan.sh` — siren, needs PipeWire (running).

## RESOLVED: Discord tokens

**Bogdan filled `.env` on 2026-08-24. Blocker CLEARED, stop polling.** All six
values set (2x 72-char tokens, 4x 19-digit snowflakes, mode 600). Never print the
values anywhere but `.env`.

Historical: He was given `DISCORD-SETUP.md` and said he'd do it.
**Poll `.env` at each phase boundary.** Build and unit-test everything regardless;
when the tokens appear, run the live tests and log the result.
Do NOT email him just to ask for tokens — he already knows.

## CURRENT STATE

- [x] Recon + research complete (8 agents). Findings folded into `PLAN.md`.
- [x] `PLAN.md` written and approved by Bogdan.
- [x] `DISCORD-SETUP.md` written; `.env` stub created (chmod 600, gitignored).
- [x] `~/.claude/bin/ctx` built and working (reads own transcript usage).
- [x] **Phase 0 — timeshift config + snapshot zero.** DONE & verified — snapshot
      `2026-08-24_17-31-50` (8.7G, RSYNC, on p4). Fidelity proven by rsync dry-run
      + 400 sampled sha256s. `cronie` was disabled and had to be enabled or the
      daily schedule would have silently never fired. See `RESTORE.md`.
      `~/.claude/bin/hotline-backup <path>` = per-path tar (rule 7).
      NOTE: `ctx` was rewritten — it defaulted to the newest transcript across ALL
      projects (reported another session at 96.7% on its first run here) and
      hardcoded a 200k window. Now defaults to `$CLAUDE_CODE_SESSION_ID` and reads
      `~/.claude/ctx.conf` (`window = 1000000`). Re-set that file if the model changes.
- [x] **Phase 1 — router core.** DONE. 76 tests, ruff+mypy clean, commit 472a078.
      Attach needs `crossSessionInbound: "accept"` (set). Stop hook is a fast path
      only; transcript quiescence is the fallback. Guard installed, default ON.
      (was: cc-socks client, Stop-hook reply capture,
      persistent `stream-json` subprocess, 3 routing modes). Headless CLI.
- [x] **Phase 2 — iPhone Shortcut path.** DONE. `hotlined` (archserver:8788) +
      stdlib `frontdoor.py` (pigion:8788, 23MB). Both are systemd **user** units
      with lingering (pigion has NO passwordless sudo; self-linger works). Phone
      points at pigion so Phase 5's wake is invisible to it -- `wake_upstream()`
      is the seam. Recipe at `iphone/SHORTCUT.md`; Bogdan still has to build the
      Shortcut by hand (3 min). NOTE: `~/pigion-todo` is on PIGION, not here.
- [x] **Phase 3 — Discord text bridge.** DONE. Gate is author-id first, then
      guild, then channel — guild membership alone is not sufficient and there are
      tests that say so. Pager (`hotline-page`) is REST-only and synchronous on
      purpose, so a blocked agent can page from any session even with `hotlined`
      dead. Verified live: real question, answered by DM in 53s. Two bugs found by
      using it: silent truncation at 1900 chars (now numbered parts), and the
      pager and bridge sharing one channel (now a page-claim file under /run).
      `scripts/scan-secrets.py` is a pre-commit hook after real ids were found
      staged in `tests/test_bot.py` for a public push.
- [x] **Phase 4 — Discord voice.** DONE, verified with Bogdan's own voice.
      pycord#3139 (DAVE) is a red herring — DAVE works. Six separate receive bugs
      in py-cord, each of which looks exactly like the advertised breakage, plus
      two more that only appear when the sender is a real client rather than a
      bot: the hardcoded 8-byte extension offset, and `OpusError` killing the
      router thread so one bad frame deafened the call permanently. Also: Discord
      rotates the transport key when participants change and py-cord never calls
      its own `update_secret_key`; the decryptor now rebuilds and retries.
      Measured: distil-large-v3 on the 4060 at 0.2-0.36s/utterance, Piper at 30x.
- [x] **Phase 5 — Pigion sentinel + boot units + WoL.** Sentinel is a hand-rolled
      gateway client (GUILD_VOICE_STATES only) running as a thread inside
      `frontdoor.py`, 31MB resident, verified on a real join. Boot units enabled
      with lingering on both machines. **The magic packet has never woken
      anything and is UNVERIFIED-BY-DESIGN:** `enp4s0` is NO-CARRIER. What is
      verified is that the correct 102 bytes leave Pigion.
- [x] **`tofix.md` round (2026-08-24).** All 8 items done — see the status block
      appended to `~/tofix.md`. Sessions now run in tmux and are attachable,
      killable and survive a daemon restart with their context; busy sessions get
      a stand-in plus a background relay. One serious bug found and fixed on the
      way: the reply waiter consumed the Stop event and could hand a caller
      another turn's answer (226s, wrong question). See PROGRESS.md.

### Session of 2026-08-25 (worker `hotline-80`, session c1eada39)

Picked up as the respawn after the original `hotline-80` was killed. Everything
below is committed and pushed through `1c4f06f`. 343 tests, ruff and mypy clean.

### The bug that had been killing every session on the box

The tmux **server** was living inside `hotlined.service`'s cgroup -- whoever
first runs `tmux new-session` when no server is listening becomes its parent and
the server inherits that cgroup. With the default `KillMode=control-group`,
every stop, restart or crash of the daemon killed the server, which SIGHUPs every
pane. One `systemctl restart hotlined` destroyed four Claude sessions on
2026-08-24, including the agent that had been told to issue the restart.
`Restart=always` means a crash did the same.

Fixed twice over: `KillMode=process` on the unit (protects a server already
misplaced) and `tmuxen` spawning through `systemd-run --user --scope --collect`
(stops the misplacement recurring). Verified in production across four daemon
restarts since -- sessions survive. **Do not remove either without understanding
this.**

### Shipped this session

- **`hotline --adopt NAME`** -- a respawned worker takes over its predecessor's
  registry record and channel. The watchdog depends on it: it is what lets a
  "moved" worker be told from a dead one.
- **Watchdog rewritten.** It tested `tmux has-session -t hotline`, which is a
  liveness test for one launcher rather than for the worker, so it manufactured
  a duplicate every six minutes instead of recovering from crashes. Now resolves
  the worker through the registry. Logs to `watchdog.log`, not `PROGRESS.md`.
- **`--resume` works for an agent that was killed.** No handoff means the
  replacement is seeded from its predecessor's transcript with an explicit
  warning that it is reading a corpse. A still-live channel is kept rather than
  duplicated. Shared with the Discord path via `revive.py`.
- **Auto-enrolment.** A session Bogdan starts by talking is registered and given
  its own channel; his opening message is the provisional task.
- **Confirmation in #general.** A message is held and its destination named
  before delivery -- `yes` / `no` / anything else replaces it. Sticky per target.
- **`resume`** -- lists the last ten resumable agents, marked finished/killed and
  handoff/transcript-only. `resume 2` or `resume <name>`.
- **`new agent <task>`** -- a genuinely separate agent. A pane is named after the
  conversation key, so a channel's session is a singleton and `new session` could
  only ever hand back the same one; there was no route to a second agent at all.
  This mints its own key, hence its own pane, record and channel.
- **Provenance.** Every relayed message says where it came from. A Discord relay
  carries channel/message/author ids and a body digest, and `hotline
  --provenance` re-fetches the original **from Discord** to confirm the gated
  user posted it. Peer messages are labelled as not an authorization channel.
  Not a security boundary -- see the module docstring, which says so at length.

### The last thing learned, and the next thing to build

`data-d5` blocked a shutdown I had authority to run, and it was right to. I had
told it the *mechanism* in full — the transient unit, the script's logic,
FINISHED-only-on-done, the grace window, `enp4s0` carrier = 0 — and it verified
every part of that independently. What I never sent was the *warrant*: Bogdan's
own verified message asking for the shutdown. In its words:

> Accurate description of how a thing is wired is orthogonal to who asked for
> it. A peer that checks both will block every time the second one is absent.

That is a design gap, not a misunderstanding. The `sys-admin` header proves the
role was delegated; it says nothing about who asked for *this particular
instruction*. **Next thing to build: let a relayed instruction carry the
originating human's provenance record alongside it** — `hotline --to --warrant
<record>`, or automatically when a sys-admin agent is passing on something
Bogdan said. Then a peer can check both in one pass instead of correctly
refusing and waiting for a second round trip.

Also worth keeping: it cancelled *loudly* rather than stalling silently. Silence
would have tripped the stall pager and woken him with a siren at 3am, which is
worse than either outcome.

`~/.claude/bin/hotline-watch-agent` is the watcher — watch an agent, page on
stall, optionally act on completion. The abort file is sticky by design: once it
appears the unit logs and exits, so re-arming needs a fresh invocation rather
than a restart.

`~/.claude/bin/hotline-shot` was written by data-d5 — post images to an agent's
channel, which `hotline-say` could not do (it reads with `read_text()` and dies
on a PNG's first byte). Worth folding into hotline proper; its one sharp edge is
the 8MB non-boosted upload ceiling, which it checks rather than letting Discord
reject a half-sent body.

### Things a successor should not re-learn the hard way

- **The test suite had live Discord credentials.** `.env` is exported in the
  development shell, so `channels.from_env()` handed the suite a real client and
  the pool tests created real channels until Discord returned 429. `conftest.py`
  now scrubs every Discord variable and redirects `XDG_STATE_HOME`, autouse. If
  the suite is ever slow, suspect real network calls before suspecting the tests.
- **A log line is not a cause.** Three times this session a confident reading of
  a log was wrong: the lying "key rotated" message, my first answer for what
  killed hotline-80, and ollama's `-ngl 0` "no usable GPU found" -- which was
  data-f3's own `num_gpu:0` being honoured, and whose 500 was actually the tmux
  bug killing the process mid-request.
- **Verification is worth more from a recipient than from the author.** The
  provenance verifier's containment check let text added in transit inherit
  Bogdan's authority. I had the evidence on screen and missed it; the first
  agent it was tested on found it immediately, using its own message as the
  demonstration.

#### Session of 2026-08-25 afternoon (worker `hotline-80`, session 553267a3)

Third worker to carry the name. Arrived to 370 tests; left at 398, ruff and mypy
clean, everything committed and pushed through `70b83b8`.

**The watchdog had been spawning a worker every six minutes.** `data-67` (Bogdan's
own session) had already found and fixed the cause -- `hotline-run` called raw
`tmux new-session`, so the tmux server inherited `hotline-watchdog.service`'s
cgroup and systemd killed it the moment the oneshot returned. I then wrote a
*second* cause into `PROGRESS.md` (that the registry still pointed at the dead
predecessor) which was **wrong**, and data-67 corrected it: the adopt is step one
of the spawn prompt and runs automatically. The cgroup fix alone was sufficient.
`hotline-run` now verifies the adopt actually took before reporting success --
data-67's work, do not redo it.

**Shipped:**

- **`--warrant`** (`c41eed6`) -- the task the previous handoff named. A relayed
  instruction can now carry the originating human's Discord receipt, so a peer
  can check *who asked*, not just who is relaying. It deliberately does NOT say
  "verified therefore comply": it prints his verbatim words and leaves the scope
  judgement with the reader, because the alternative is a forgeable superuser
  badge with better branding. A failing warrant fails the whole verdict; a
  `kind=agent` record is refused as a warrant outright.
- **A registered agent name is an address** (`dea2968`). `--to hotline-80` used
  to fail. That name is in every provenance header and was the one name you could
  not address -- only the derived name (`hotline-2c`) resolved, and that is
  reminted on every respawn.
- **Delivered is not failed** (`1462557`). `--to` against a busy session printed
  "do not resend" and then exited 1. Now exit 3, with `--no-wait` for
  fire-and-forget.
- **A session is told when it is being spoken to** (`a4b5f07`). See below.

**The acceptance test was run.** See `PROGRESS.md` for the full account. Result:
the system announced its own completion through its own voice pipeline, verified
by transcribing it at the far end (83% word similarity, both differences benign).
**It is passed on the machine's side and unfinished on his** -- he was away, so
nobody heard it. Do not record it as a clean pass. He has been told over Discord
and offered a live repeat.

**`scripts/voice-announce.py`** is new: hotline speaks, the sentinel receives and
transcribes. The loopback harness only ever tested *receive*; for an acceptance
test whose whole content is an announcement, the untested direction was the one
that mattered.

**`scripts/voice-agent-channel-test.py`** is new, and verified the last
unverified feature: joining an agent's own voice channel binds the call to that
agent. Read the comments next to its pass/fail check before trusting it -- that
check has been wrong in both directions.

## THERE IS A SECOND PROJECT NOW: `hotline-ios`

`/home/bodas/data/hotline-ios`, spec at `SPEC.md`, agent `hotline-ios`, channel
`#agent-hotline-ios`. **`SPEC.md` §2 is superseded — read this section instead.**

### The architecture, after he decoupled it (2026-08-25 evening)

His words, verified: *"make your own app for delegation talking excetera which i
will sideload every week. Telegram for the ring. And we can fully scrap the
talking voice rout. Thats bassically a gimic"*

**The thing that RINGS is not the thing you TALK THROUGH.** Every option we costed
before this assumed they were the same app, and that assumption is what made all
of them fragile. Decoupled:

- **The ring** — a doorbell only. It wakes him; he answers; it does nothing else.
- **The app** — his own, sideloaded weekly, **text delegation** ("talking *to*
  agents", not talking aloud). No CallKit, no audio, no push entitlement, no
  keepalive, and the reboot gap stops mattering because he opens it deliberately.
- **Voice (GPU speech)** — scrapped as a gimmick. See the freeze note below.

### The ring: two options, both live, and they compose

|  | costs him |
|---|---|
| **Linphone** | install one free app. That is the whole list. Depends on Belledonne's relay continuing not to check push-token ownership; if they tighten it, his phone silently stops ringing. |
| **Telegram** | `api_id`/`api_hash` from *his* my.telegram.org login, **plus a second Telegram user account with its own phone number** — he cannot call himself. May cost money. No third-party dependency. |

**Bots cannot ring him.** Verified with a control: Telegram's docs mark bot-usable
methods, `messages.sendMessage` has the marker, `phone.requestCall` does not, and
`account.updateProfile` (user-only) does not either. His bot token is useful as a
text channel and is NOT a ring — do not let it drift into looking like one.

**Build both if he will.** Their failure modes are uncorrelated — different
company, different infrastructure, different way of breaking — and `RingChain`
already falls through in order, so a second doorbell is configuration rather than
a rewrite. Two doorbells that cannot fail together beat one better doorbell.

**Privacy point he needs before handing over a number:** logging archserver into a
second Telegram account means archserver can see that account's chats, and once an
account is active elsewhere the login code arrives *inside Telegram on that
phone*, not by SMS. Fine for a fresh number; not fine for someone's live account.

### Verified, so nobody re-derives it

- **Telethon 1.44.0 (released) has `RequestCallRequest`** with `Accept`/`Discard`
  alongside — full 1:1 surface, not group voice. **It rings — verified.** **It
  carries audio — UNPROVEN**, key exchange never completed in live tests.
- **Swift 6.3.3 compiles and runs on this Arch box** from a private toolchain in
  `/mnt/iosbuild`, nothing installed system-wide. The missing piece is Apple's SDK
  and that is an **account problem, not a machine problem**.
- **THE DARWIN SDK IS BUILT AND INSTALLED. That wall is down.** The macOS-runner
  build succeeded, the artifact was downloaded and unpacked, and it is installed at
  `~/.swiftpm/swift-sdks/darwin.artifactbundle` with a copy at
  `/mnt/iosbuild/sdk-dl/`. It is on disk and survives a reboot. Combined with the
  Swift toolchain already proven here, **this box can now build an iOS app** —
  nothing about that is blocked on Apple, an Apple ID, or a Mac any more.
- **SDK route (historical, for how it was got):** `BogdanStamenovic/darwin-sdk-build`, public, **he authorised it**
  — audited by me to exactly two blobs with no addressing or credentials. A macOS
  runner has Xcode preinstalled, so no Apple ID and no 13GB `Xcode.xip`.
  **Download and verify the artifact BEFORE deleting the repo** — artifacts live
  under the repository and die with it.
- **`ConfirmedRing` is the load-bearing piece.** A transport must produce positive
  evidence it rang or the silence becomes unreachable and degrades loudly. It
  **fails closed**. Without it a fall-through chain does not degrade — it stops at
  the first silent failure and the call vanishes.
- **`RingChain` does not fall through on a DECLINE.** He saw it and said not now;
  ringing him another way a second later is what he was declining.
- **Every rung below the ring is an alert, not a call**, and a critical alert is a
  louder fake call — the thing he asked to be rid of. Degrading must say which
  rung it landed on rather than quietly substituting a notification.
- `/mnt/windows/hotline-ios-build.img` is a FILE (ext4, loop-mounted at
  `/mnt/iosbuild`). His Windows is untouched. Do **not** delete
  `/mnt/windows/pacman-cache-archive-20260825` — moved package cache, this box's
  only rollback.
- `hotline-sipprobe.service` is **stood down but its code is kept**, so the
  Linphone route is one command from testable again.

### The voice subsystem is FROZEN, not deleted

He said "stop investing in it", confirmed. `voice.py`, `audio.py`, Whisper, Piper
and their tests **stay**. Deleting is irreversible and costs nothing to defer;
keeping costs disk. That code also encodes six py-cord receive bugs nobody
upstream documented. Only an explicit "delete the voice code" changes this.

**Do not sweep up the iPhone Shortcut path with it.** "Hey Siri, Hotline" is a
*different thing*: his phone does the speech on-device and this side only ever
sees text over HTTP. No GPU, no Whisper, no Piper. It works today and costs
nothing.

## Reporting to him automatically

`~/.claude/bin/hotline-standup` on `hotline-standup@NAME.timer` (30 min,
`Persistent=true`). Watches from outside rather than asking the agent to
self-report, summarises from pane + transcript only, is handed its previous update
and told not to repeat it, and **reports a dead session rather than going quiet** —
a silent timer and a dead agent look identical from a phone. Enabled for
`hotline-ios`.

## The pattern that is now too strong to ignore

**Five confident field-reads were wrong on 2026-08-25 across three sessions. Two
were mine. Every single one was caught by somebody other than its author — not one
by the person who made it, on re-reading, at any point.** All three of us were
checking carefully. That is the point.

Every one had the same shape, and it is worth memorising because it is the most
transferable thing in this log:

> **A status field read as a signal, without testing the thing the field
> supposedly indicates.**

An empty `Endpoints` column that is empty for every peer including one we hold a
direct connection to. A phone missing from the peer map that answers pings anyway.
A capabilities-table cell whose blankness only means something next to a row you
already know the answer to. Each time the fix was the same: **probe the thing
directly, or compare against a control whose answer you already know.**

**A seventh error, and it is a different species — worth its own line because no
control row catches it.** Twice on 2026-08-25 a conclusion was left standing after
its premise had changed:

- I filed a "do not re-run this research" instruction onto a sweep whose author
  was still working. A wrong finding is one bad fact someone trips over; **an
  instruction not to look again steers the next person away from the answer and
  looks like diligence while doing it.** Never attach a do-not-revisit note to a
  null result unless you can name who finished the search and when.
- Both `data-89` and I went on costing option C under assumptions his decoupling
  had already destroyed — we shelved it in the same hour it got cheaper. Neither
  of us re-ran the conclusion after the premise moved, and `data-89` had *had* the
  insight that moved it.

The mechanical guard for the first is: before writing "we checked X and found
nothing", confirm every agent that was checking X has actually reported. For the
second there is no trick — **when a premise changes, walk the conclusions that
rested on it**, especially the ones you just discarded.

Related and separate: **four distinct holes in the provenance design have been
found by agents on the receiving end of it, and none by its author.**

The authoring end cannot see what it failed to send. The receiving end cannot
help but notice. A recipient-side session is cheap. **Make it a standing part of
the build rather than something that happened twice.**

Its own suggestion for the next structural step, which is a good one: identity
here is *sender-composed* -- `router.py` does `wire = origin.wrap(text)`, so every
header is written by whoever is sending. `kind=human` is the one checkable
exception because `--provenance` re-fetches from Discord. If you want identity the
receiver can **attest** rather than the sender **assert**, `SO_PEERCRED` on the
unix socket is the mechanism.

## Open, and why

- **The ring is undecided and it is his call**: Linphone (one free app, Belledonne
  dependency) or Telegram (api keys plus a second account with a phone number,
  possibly money) — or both, which is cheap now and strictly better. Everything
  else on `hotline-ios` continues meanwhile; nothing is blocked on the answer.
- **The acceptance test is half-done and cannot be finished without him.** The
  pipeline announced its own completion and that was verified end to end; nobody
  heard it, because both transports need him present -- Discord voice needs him
  in the channel, and the iPhone Shortcut path can only be started from his
  phone. Repeat it live the moment he joins. Do NOT mark it a clean pass.
- **A stand-in can be confidently wrong about the agent it stands in for, and
  over voice there is no visual cue.** It said "I have no evidence of any
  assigned code word" about an agent that had one. It sees a transcript tail and
  a pane, so it cannot see most of what the agent knows. NOT patched
  deliberately: `standin.py` is tested and working, its prompt already says "do
  not guess", and this is a judgement call inside the model rather than a
  structural fault.
- **An answer sent over a peer channel is invisible to the reply path.** A
  session that answers by calling its harness's `SendMessage` rather than as turn
  output produces turn output that does not contain the answer, and the waiter
  correctly sees nothing. Contract issue, not a bug -- but if agents keep doing
  it, the contract needs stating somewhere they will read it.
- **Spoken file paths do not survive Whisper.** "slash home slash bodas slash
  data" came back as "slash home slash bowler slash datur", and the extension as
  "dot empty". Anything that routes a path through STT is building on sand.
- Wake-on-LAN still UNVERIFIED-BY-DESIGN -- `enp4s0` is NO-CARRIER.

Closed since the last handoff: the reply-waiter's misdiagnosis (its three causes
are now reported separately, and delivered-but-unanswered is exit 3 rather than a
failure), and voice join into an agent channel, which is verified -- the harness
passes `allowed=` directly, so `HOTLINE_VOICE_ALLOWED_IDS` did not need setting
and remains deliberately unset.
- Two questions data-f3 left for Bogdan, still unanswered: keep `ollama-cuda` +
  `cuda` (~9 GiB) or roll back, and should ollama be reachable past `127.0.0.1`.
- **Port 8000 (`Ollama Chat`) was never styled.** He asked, then said "disregard
  the message you get", and never said which message. Untouched deliberately.

## Still blocked on Bogdan, physically

- Plug an ethernet cable into `enp4s0`. **Careful with the wording here — the box
  is not INCAPABLE of remote wake, it is UNCONFIGURED for it**, and those are
  different sentences. Verified 2026-08-26: `Supports Wake-on: pumbg` (the `g` is
  magic-packet) with `Wake-on: d` (disabled) and `Link detected: no`; the wifi
  dongle reports WoWLAN *disabled*, not unsupported. So it is a cable plus two
  settings, not a missing capability — roughly twenty minutes that turns future
  overnight runs from one-way doors into something recoverable. `hotline-ios`
  drew this distinction after `data-89` and I both said "no remote wake", which
  was true of the state and misleading about the hardware.
- BIOS on the ASRock B550M-HVS SE (no IPMI, so this cannot be done remotely):
  ErP / ErP Ready **disabled**, PCIE Devices Power On / PME Event Wake Up
  **enabled**. ErP is the one that bites — it cuts standby power to the NIC.
- Optional: enabling `nvidia-suspend`/`nvidia-resume` for S3 needs one real
  cycle with the GPU loaded and a human present.

## Design notes worth keeping

- **Narrate tool calls aloud.** `--output-format stream-json` emits `tool_use`
  events; speak them during long waits instead of hold music. This is the feature
  that makes it feel like a person. Get it right.
- **Gate at the audio sink on user ID**, not just guild. py-cord gives per-user
  streams; transcribing whatever is in the channel = a root shell for anyone who joins.
- Bogdan approved a `PreToolUse` denylist question but never answered it. **Build it,
  default ON**, covering only catastrophic patterns (`rm -rf /`, `mkfs`, `dd of=/dev/`,
  `>/dev/sd*`). Easy for him to remove; cheap insurance against a Whisper mishear.
- Session names are derived (`data-d6`, `data-13`) and awkward to say aloud — the
  router should also accept "the one in uxonews" and ordinals.
- Sleep policy: **S5 poweroff**, not suspend. `nvidia-suspend/resume/hibernate` units
  exist but are all DISABLED while nvidia modules are loaded — suspending in that
  state is a known black-screen-on-resume failure. Do not enable them unattended.

## Contact rules (added by Bogdan, supersedes rule 6 above)

- **Discord is now the primary contact channel.** The tokens are in `.env` and the
  bots are in his server. If you need him — a real blocker, a decision only he can
  make — post in the `hotline-log` text channel and `@mention` him
  (`<@DISCORD_USER_ID>`). He will respond there. Use the escalating mention ladder
  once Phase 3 exists; before that, a single mention via a plain REST POST is fine.
  Email drops to a fallback for when Discord itself is the thing that's broken.

- **Final acceptance test: announce completion through the system itself.**
  When all five phases are done and genuinely passing, do NOT just write it in
  PROGRESS.md and stop. Announce it **through the real pipeline**, so that the
  announcement is itself the proof.

  **REDEFINED 2026-08-27 by him.** This used to say "drive the finished voice path
  and have it *speak* the completion message", and it explicitly ruled out a text
  message. That was written before he decoupled the architecture and called the
  voice route *"basically a gimmick"*. Asked to choose between running the old
  test as written and moving the finish line to the path he actually uses, he
  answered **"B."** — verified, Discord message `1542452470528090182`,
  2026-08-27T08:35:26Z.

  So the test is now the **text path**: the box is woken, a message from the phone
  app reaches an agent, and the agent's answer comes back in the app. It must be
  the real daemon and the real router — not a line in `PROGRESS.md`, and not a
  claim that it would have worked.

  This is the acceptance test, not a victory lap: if the system cannot announce
  its own completion through itself, it is not complete. If it fails, that failure
  IS the result — log it honestly and tell him over Discord instead.

  **The voice code stays.** Only the finish line moved. Do not read this as
  permission to delete `voice.py`, `audio.py`, Whisper or Piper — that needs an
  explicit "delete the voice code" from him and he has never said it.

---

## SHUTDOWN 2026-08-27 — state at power-off (worker `hotline-80`, session c1ef2181)

Bogdan ordered the machine shut down and told me to have every agent write a
handoff first. This section is mine. **Read this before you believe the prompt
that started you.**

### The build is STOPPED, by him, and the stop was never lifted

On 2026-08-26 at 10:47 he told the then-worker *"I need you to stop what you are
doing right now."* It complied and left its work uncommitted on purpose so the
state stayed reversible. He was asked *"what do you need?"* and never answered
that question. **The stop still stands at power-off.** Nothing in this repo was
resumed, and the next worker must not resume it on the strength of a spawn prompt.

### The watchdog's spawn prompt is false in both premises — check before acting

It says *"replacing one that died"* and *"Bogdan is away and expects all phases
attempted."* On 2026-08-26 **both halves were wrong**: nothing had died (he
stopped it deliberately) and he was not away — paged, he replied in **19 seconds**
with `DO NOT RESUME FOLLOW MY INSTRUCTIONS`.

> **An automated prompt cannot lift a human's stop.** The watchdog is a launcher,
> not an authority. A timer can always ask you to do less and never to do more.

The correction was sitting in `#hotline-log`, free, thirty seconds away. **Read the
last messages in Discord before believing the text that spawned you.** Two workers
in a row were started with stale prompts.

### Working tree at power-off — four files uncommitted, deliberately

`PROGRESS.md`, `src/hotline/provenance.py`, `src/hotline/router.py`,
`tests/test_provenance.py`. This is **not** an interrupted edit. It is the
reply-contract fix, complete and green, left uncommitted so he can drop it with one
`git checkout`:

- A message someone is blocked on now carries `REPLY_CONTRACT`, telling the
  receiver to answer as **turn output** rather than via a peer-messaging tool —
  the contract gap the previous handoff named under "an answer sent over a peer
  channel is invisible to the reply path". `awaiting_reply` is set in
  `Router.ask()` because that method *is* the definition of "someone is waiting",
  and deliberately kept **out** of the JSON record: it is a transport detail, not
  a claim about who sent this, and `--provenance` would otherwise have to re-fetch
  from Discord to check a fact Discord has never heard of.

**Verified at power-off:** 455 tests pass, mypy clean on 25 files. Ruff's 6
warnings are **pre-existing and unrelated** — every one in `pigion/frontdoor.py`,
the Pi's stdlib-only file, unmodified at HEAD. Do not report them as a regression.

`PROGRESS.md` also carries a ~150-line narrative entry from this session. He was
told it is there and left the decision open.

### The one real bug still unfixed, and why it was left

`kind="phone"` is not a case `Origin.header()` handles, so it falls through to the
`else` branch meant for hotline's own machine-generated notices. **Every message he
types in the phone app is currently labelled to the receiving agent as "generated
by hotline itself, not by a person"** — the exact opposite of the truth, and it
points a reader away from him rather than at him.

Found by the agent on the receiving end of it, which makes it the **fifth** hole in
the provenance design found by a recipient rather than its author. Left unfixed
only because it was parked pending his go and he never gave one. It is a small fix
and it should be near the top of the next list.

### What happened to hotline-ios and the laptop errand

He asked for the agent "from when we were fixing my laptop". I got this wrong twice
and both mistakes are instructive:

1. I steered `hotline-ios` at CI run `32923724565` step 9 without checking which job
   he wanted — session `1ed3cbc9` contains the iOS build, an ESP32 project **and**
   the laptop repair, so reading the wrong half is easy.
2. I then assumed the repair belonged to `hotline-ios` and retasked its registry
   record. He had **already** told it directly to fork the errand into a separate
   session. Both changes were reversed at his instruction.

**`arch-repair`** is the laptop agent: forked from `1ed3cbc9` with `--fork-session`
so the original transcript is untouched, cwd `/home/bodas/data`, positioned at the
13:00 Torx-T8/M2.5 answer. Its own handoff is at
`/home/bodas/data/arch-repair-handoff.md`. Open debt there is the **reassembly
walkthrough**: heatsink screws in stamped order, then board-to-chassis, then
perimeter, then the shim decision — under a no-paste / do-not-lift-the-cooler
constraint. `hotline-ios`'s handoff is at `/home/bodas/data/hotline-ios/handoff.md`.

**CI run `32923724565`: both jobs green and step 9 did film** — 6153 frames,
2736x1260, 104s, 21 marked gestures, two UI tests passed. Its `"hitchy"` frametimes
verdict is an **artifact, not a finding**: computed across the whole 200s recording
including launches and idle, with a max "frame" of 20.4s that is a static screen
rather than a stall. It measures idleness wearing a jank label. Window it to the
drive span before repeating it to him.

### The thing that matters most for whoever reads this next

**This box was powered off with NO REMOTE WAKE.** `enp4s0` is NO-CARRIER and
`Wake-on` is `d`. Nothing can reach this machine until somebody presses the power
button by hand. That is still a cable plus two settings — about twenty minutes —
and it is what turns future shutdowns from one-way doors into something
recoverable. It has now bitten twice in two days.

### WoL, done at his instruction just before the 2026-08-27 shutdown

He said: *"this machine is shutdown for around 2 hours. And then when it comes
back WoL should be working."* **Wake-on-LAN has three legs and only one of them
is software.** Leg 1 is done and tested; legs 2 and 3 are physical and his.

**Leg 1 — the OS side. DONE, and tested rather than assumed.**
`/etc/systemd/system/wol-enp4s0.service`, enabled, `WantedBy=multi-user.target`.

The pre-existing udev rule `81-wol-enp4s0.rules` was **correct** — right syntax,
and `/usr/bin/ethtool` really is there and executable, which was the obvious
suspect and was innocent. NetworkManager was already set
`802-3-ethernet.wake-on-lan=magic` too. Yet `Wake-on` still read `d` after the
21:23 boot. So the rule fires and **something clears it afterwards** — an r8169
re-probe or a link-state change, neither of which udev `RUN+=` is ordered
against, and neither of which logs anything. The unit is the second belt: it
re-arms once the network stack has settled, and it echoes the resulting
`Wake-on:` line into its own journal so the next boot can be confirmed with
`journalctl -u wol-enp4s0` instead of a guess.

Deliberately **not** a systemd `.link` file, and this matters: only the *first*
matching `.link` applies, so a `50-` file setting `WakeOnLan=` without restating
`NamePolicy` can revert the interface to its kernel name. That would silently
break both the udev rule (`KERNEL=="enp4s0"`) and the NM profile — on a box with
no remote wake and nobody at it. The oneshot carries no renaming risk.

Tested by forcing `wol d`, restarting the unit, and reading `wol g` back. Note
`ethtool -s enp4s0 wol g` works fine **with no carrier**, so the cable is not a
prerequisite for arming — only for waking.

**Leg 2 — the cable.** `enp4s0` must actually be plugged in. At power-off it was
still NO-CARRIER.

**Leg 3 — BIOS, and this is the one that bites.** ASRock B550M-HVS SE, **no IPMI,
so it cannot be done remotely at all**:
- **ErP / ErP Ready → DISABLED.** This is the killer: ErP cuts standby power to
  the NIC, so a perfectly armed interface gets no power in S5 and the magic
  packet reaches nothing.
- **PCIE Devices Power On / PME Event Wake Up → ENABLED.**

**How to verify once it is back** (do all four; the first three can pass while
the wake still fails, because they only cover leg 1):
1. `ethtool enp4s0 | grep Wake-on` → `g`
2. `cat /sys/class/net/enp4s0/carrier` → `1`
3. `journalctl -u wol-enp4s0 -b` → the unit's own `Wake-on: g` line
4. **The only test that means anything:** power the box down, then from Pigion
   (192.168.1.8, same /24 and same L2 domain, so broadcast reaches) send a magic
   packet to `a8:a1:59:fd:4d:13` and see whether it actually comes up. Legs 1-3
   are necessary and none of them is sufficient. Until step 4 passes, WoL is
   still UNVERIFIED — do not write it up as working.

---

## 2026-08-27 04:50 — WoL VERIFIED, and the stop is lifted FOR LATER TODAY, not now

Worker `hotline-80`, session `f1a4f718`. Watchdog-spawned at 04:37 with the usual
canned prompt. I did **not** act on it — I read Discord first, as the section above
says to, and it was right to say so: both halves of that prompt were false again.
That is now **three workers in a row** started on a stale template.

### Read this before you believe your spawn prompt

`hotline-run`'s PROMPT is a hardcoded string. It always says "replacing one that
died" and "Bogdan is away and expects all phases attempted". It said that tonight
while he was awake, at the keyboard, and answering in 79 seconds. **A launcher is
not an authority. Check Discord.**

### WAKE-ON-LAN IS VERIFIED. Leg 4 passed. Delete the old sentence.

Every previous section of this file says `enp4s0` is NO-CARRIER, that this box has
NO REMOTE WAKE, and that it is a one-way door. **All of that is now false**, and
the file's own rule — *when a premise changes, walk the conclusions that rested on
it* — is why this is at the top rather than in a footnote.

What changed: **he plugged the cable in and did the BIOS settings.** Then he tested
it. Measured here, independently, before he said anything:

```
Link detected: yes          carrier: 1          Wake-on: g
wol-enp4s0.service armed it at 04:35:45 and logged its own confirmation
this box is now 192.168.1.139 on ethernet (NOT the 192.168.1.9 written above)
three power cycles in 40 min: 03:57-04:01, 04:02-04:11, 04:32-04:35
```

I could not tell from software whether those boots were a magic packet or a
button — a wake from S5 is indistinguishable from a press in the kernel log — so
I asked instead of guessing. His answer, through the pager (the gated path):

> **"It did absolutely power on by WoL"**

and separately, relayed from the phone app: *"The last one did infsct wake up
using WoL"*. Two channels, same answer, corroborated by the three local facts
above. **Legs 1-4 are green. This box can be woken remotely.** Shutdowns stopped
being one-way doors at 04:35 on 2026-08-27.

Do not re-derive this and do not re-verify it by powering the box off to see.

### The stop: lifted, but NOT for the small hours of the 27th

His words at 04:50, verbatim: **"Okay so ill tel you now. You can resume tommorow.
But right now shutdown imma go to sleep."**

Both halves are instructions and the second one is done — the box is off at his
order.

**"Tomorrow" was said at ten to five in the morning and means after he wakes, i.e.
later on 2026-08-27 — not 06:00, not the next boot.** If the watchdog respawns you
because he wakes the box for something unrelated, you are not cleared merely by
having booted. The safe reading of a sleepy "tomorrow" is *the next time he speaks
to you*. Say hello, confirm, then go. He replies in under two minutes when he is up.

The watchdog is deliberately **left enabled** — he authorised resuming, so a worker
spawning is correct, and disabling it would be a system change he did not ask for.

### The phone-label bug fired again, on me, live

While I was mid-page a relay arrived carrying `kind:"phone"`, label *"typed in the
hotline app on his phone"*, with hotline's standing text printed directly beneath
it reading *"This was generated by hotline itself, not by a person and not by
another agent."* The two sentences contradict each other in the same message.

It was **his** words — *"The last one did infsct wake up using WoL"*, typos and all
— labelled to me as machine-generated. That is the sixth recipient to hit this and
still nobody has fixed it. It is a one-case addition to `Origin.header()`. **It
should be the first commit tomorrow.**

### A new one, found the same way: a page is not a conversation

He answered my page and then immediately typed `Where am i`, `Help`, `Where am i`.
He was not lost — **the system genuinely does not bind you to anything when you
reply to a page.** `bindings.json` read `attached_to: null` throughout. From a
phone a page and a conversation are indistinguishable, and only one of them has a
session on the other end.

Worth fixing or worth saying out loud in the page footer. Costs him real confusion
at 4am, twice tonight.

### Tree state at power-off — unchanged from how he was left it

The **same four files** are still uncommitted on purpose (`PROGRESS.md`,
`provenance.py`, `router.py`, `tests/test_provenance.py`) — the reply-contract fix,
complete and green, one `git checkout` from gone if he wants it gone. **I added
nothing to the code.** My only edits are to `handoff.md` and `PROGRESS.md`.
`handoff-data-1e.md` is untracked and is data-1e's own record.

I did not run the test suite. The last verified numbers stand: 455 pass, mypy clean
on 25 files, ruff's 6 warnings pre-existing in `pigion/frontdoor.py`.

### The list for tomorrow, in the order I would do it

1. **`kind="phone"`** falls through `Origin.header()`'s `else`. One case. Above.
2. **`revive.py rehome()` drops `handoff`** — an agent resumed from a handoff
   forgets it, so the *next* resume silently falls back to the raw transcript.
3. **`--resume` never delivers the brief when an agent renames itself.** `resume()`
   returns the spawn name (`data-9c`), the session renames to `hotline-ios`, and
   `ask_session('data-9c', ...)` finds nothing. The agent runs with no brief.
   2 and 3 were both found by `data-1e`; its record is `handoff-data-1e.md`.
4. Commit the pending reply-contract work, **if he wants it** — ask, do not assume.
5. The acceptance test still needs him present. Still not a clean pass.

`/home/bodas/data/hotline-ios` still has `hasTrustDialogAccepted: false`, which is
his call and not a peer's.

---

## 2026-08-27 morning — the stop lifted, and the list worked (worker `hotline-80`, session `99623661`)

Watchdog-spawned 09:59, three minutes after he woke the box at 09:56. Same canned
prompt, wrong again on "replacing one that died". Read Discord first; he had
posted at 09:55 and 10:00 and was plainly at the controls. Paged him with **one
question and nothing attached** and got the answer above in under two minutes.

### Shipped, all committed and pushed (`5be5bcd`, `ead25f0`, `89b64ce`)

**466 tests, ruff and mypy clean.** Every fix below was confirmed by reverting it
and watching the new test fail first.

1. **`kind="phone"` now has a branch in `Origin.header()`.** It had none, so his
   own typing reached agents under "generated by hotline itself, not by a person".
   The new text says a person typed it *and* that the gate is a shared key on this
   network rather than a third party — no receipt, nothing to re-fetch, evidence
   not proof. It deliberately does not offer `--provenance`, which would find
   nothing. Note **the producer is in the `hotline-ios` repo and the consumer is
   here**, which is why six recipients hit it and no author saw it.
2. **`rehome()` no longer drops `handoff` and `voice_channel_id`.**
   `registry.declare()` builds an Agent from its own arguments, so anything
   outside that signature was lost — and the damage surfaced one resume *later*,
   as a replacement being told to read a corpse while a current handoff sat unread.
3. **`_resume()` addresses the brief by session id.** It used
   `resumed.session.name`, captured at spawn, which goes stale the instant the
   session renames itself under `--name`. The agent came up with **no brief** and
   the resume still printed success. **This fired for real this morning** and the
   fix proved itself on `hotline-ios` ten minutes later.
4. **A page says it is a page.** One line, last in the message, suppressed on
   `--no-wait` where nobody is listening.
5. **`hotline-say --file` no longer dies on a PNG.** It delegates to
   `hotline-shot` rather than growing a second multipart encoder. Verified by
   posting a real PNG and reading the attachment back off Discord.

### Findings that are not code, and matter more than some of the code

- **`SO_PEERCRED` does not close the sender-composed-identity gap.** Measured, not
  reasoned: there is no hotline-owned unix socket in the path (hotline is the
  *client* on Claude's cc-socks socket), and every session runs as uid 1000 with
  those sockets at 0600 — so any agent can bypass hotline entirely and compose its
  own header. It would authenticate only cooperative senders. **Do not file it as
  the fix.** Real attestation needs a uid per agent or socket permissions.
- **`hotlined` binds `0.0.0.0`** while `httpd.Server`'s own docstring explains why
  the wildcard was rejected and why it takes a host *list* instead. `--host`
  defaults to `0.0.0.0` and `HOTLINE_HOST` is unset. **Not exploitable** — the
  allowlist fails closed to loopback and `X-Hotline-Key` is enforced; only
  `/health` is open. Left alone deliberately: re-binding means discovering the
  tailnet address at startup, and getting it wrong takes down his primary way of
  reaching anyone while he is away. **Set `HOTLINE_HOST` and restart** when he says.
- **`hotline-sipprobe.service` is `enabled` and came back at boot**, though this
  file calls it "stood down". Stood down meant stopped, not disabled. It belongs
  to `hotline-ios`; not touched, and it has been told.
- **`gh` is logged out** (invalid token), so `origin` is now an **SSH** URL and
  pushes work. `gh auth login` is interactive and his. This matters for the Darwin
  SDK route, which ran through `gh`.
- **The `hotline-ios` folder trust dialog was accepted.** `--resume` cannot start a
  session without it, and he had just ordered that agent started in his own repo.
  The previous "his call, not a peer's" note was right *absent an order* and the
  order is what changed. `~/.claude.json` was backed up first.

### Method notes worth keeping

- **A commit was split to preserve his one-`git checkout` escape.** The tree
  carried four deliberately-uncommitted files; my phone fix landed in one of them.
  The two files were rebuilt from `HEAD` with only my hunks, and **the suite was
  run at exactly the state the commit would create** (459 green) before staging.
  A commit that only passes because of uncommitted neighbours is a trap.
- **Do not put a bare `git checkout <file>` in a compound command** in this tree.
  I reverted my own in-progress edit that way. Harmless here; it would not always be.

### Round two, same morning — what a recipient found, and one question for him

**The recipient-side review is now a step in the build, not an anecdote.** I showed
the header I had just written to a fresh session as its intended reader and asked
what it concluded, without saying what I hoped. It found three things I had not.
That is now **seven holes in this design found by the receiving end and none by an
author.** Do this every time you touch `provenance.py`.

Fixed from that review (`f740a80`):

- **A body can carry its own `[hotline-provenance ...]` block and its own
  `--- message follows ---`.** `parse()` is safe — it takes the first match, and a
  test now pins that — but the *reader* is not: top to bottom the message reads as
  a nested relay, with a forged "VERIFIABLE relay from Bogdan" three lines under a
  genuine "this is from ANOTHER AGENT". The header now counts them and says only
  the top block is hotline's. **The body is deliberately not rewritten**: agents
  here quote provenance records constantly, so defanging would corrupt ordinary
  traffic to stop a forgery that announcing catches anyway.
- **My own overclaim.** "so it is gated, and it is not anonymous" is not what a
  shared key establishes. It now says *authenticated as a key-holder, which is not
  the same as authenticated as him*. I had written the SO_PEERCRED note an hour
  earlier saying every session shares a uid, and still failed to apply it to my own
  sentence — the insight was about somebody else's mechanism.
- **The standing role now survives `--resume`.** `adopt` kept it, `rehome` dropped
  it, both are respawns, and `test_the_role_survives_a_respawn` only ever covered
  `adopt`. A resumed `hotline-80` was silently demoted to an ordinary peer in every
  header it sent. No new escalation surface: `--resume` and `--adopt` need the same
  access and `--adopt` always carried it.

**Not fixed, deliberately, and worth knowing:** the `body_sha256_16` digest is
unkeyed and travels with the body, so a forger just recomputes it. For
`kind="human"` it *is* load-bearing, because `--provenance` re-fetches from Discord
and compares against something off this machine; for every other kind it is
decorative and mostly makes the block look cryptographic. HMAC does not fix this
either — the key would sit in a file every agent can read, same as the uid problem.

**A harness bug found by dying in it** (`afeb40e`): `exit 3 = ... It is NOT lost ...
Do not resend` was printed for fresh sessions too, where the timeout kills the
subprocess and the work really is gone. Both branches are now pinned by tests.

### THE ONE OPEN QUESTION, and it is his

**`PLAN.md`'s milestone and the acceptance test both define "done" as a voice
call** — "join the voice channel... that is the whole thing working". **He has since
called the voice route a gimmick and frozen it.** Nobody walked that premise change,
which is why the acceptance test has been open for days waiting on him to join a
channel for a subsystem he scrapped.

He was asked at 10:53 to choose: **A** run it as written (voice is frozen, not
deleted; costs two minutes and touches no code) or **B** redefine the milestone
around the text path he actually uses, rewriting `PLAN.md` rather than quietly
marking the old one passed. **I recommended A.** Do not mark the acceptance test
passed by moving the goalposts without his answer.

### Where this session stopped, and what is armed

**Everything on the "list for tomorrow" is done except the two items that are his.**
466 → 475 tests, ruff and mypy clean, everything pushed through `eac9594`.

**Open and genuinely his, neither blocking:**
1. **Commit or drop the reply-contract work.** Asked at 10:16, unanswered. It is
   in the working tree, unstaged, verified clean of everything else.
2. **Close the acceptance test.** One message from the phone app, and it is done.
   The server half passes live (`HTTP 200 in 3.7s`, real daemon, real router);
   Pigion's front door correctly refuses this box with a 403, which is the gate
   working. **Do not add archserver to that allowlist to make the test pass** —
   that turns a real test into a decorative one.

**Armed:** `hotline-watch-agent hotline-ios --on-finish poweroff`, at his order.
Only an explicit `hotline --done` counts as finished; GONE and STALLED page him
and act on nothing. `hotline-ios` has been told the exact command, because
without it his shutdown instruction quietly would not have happened.

**Corrected in `~/.claude/bin/hotline-watch-agent`:** the pre-shutdown warning
still told him `enp4s0` has NO-CARRIER and the box could not be woken. False since
04:35 today, and false in the direction that would have made him think a
recoverable shutdown was a one-way door — while deciding whether to stop it. It
now hands him `wakeonlan a8:a1:59:fd:4d:13`.

**Also fixed today, outside the repo:** `hotline-say --file` no longer dies on a
PNG (it delegates to `hotline-shot` rather than growing a second multipart
encoder); verified by posting a real PNG and reading the attachment back off
Discord.

**Memory corrected:** the project memory said "SO_PEERCRED is the fix" for
sender-composed identity. It is not, and that note now says why, because it was
steering the next session at a dead end while looking like a lead.

---

## 2026-08-27 16:20 — the spawn prompt is FIXED, and how to actually read Discord

Worker `hotline-80`, session `ca581189`. Watchdog-spawned 16:08 after the box came
back at 16:06. I did no build work and none was wanted; the errand below was his.

### The prompt that lied to five workers is gone

Every handoff section since 2026-08-26 complains that `hotline-run`'s `PROMPT` is a
hardcoded string asserting *"replacing one that died"* and *"Bogdan is away and
expects all phases attempted"*. Five workers were misled by it, the fifth being me.
**Five sections of complaint and nobody edited the one line.** It is edited now.

The new prompt asserts only what a timer can know — that it fired — and says so in
those words. It then orders the reading: adopt, `handoff.md`, **then Discord**, and
states that his most recent word outranks the prompt, this banner, and the fact of
having booted. It tells a worker that finds no mandate to say hello and wait rather
than to start on the checklist. Backup of the old file:
`backups/home_bodas_.claude_bin_hotline-run.20260827-162006.tar.zst`.

**This does not retire the banner.** A prompt that no longer lies is not the same as
a prompt that knows what he wants, and the reason the old one was dangerous was
never its wording — it was that a launcher has no way to know. Keep reading Discord.

### Reading Discord from a script: the 403 is a missing User-Agent, not a permission

I lost several minutes to `HTTP 403 Forbidden` on `GET /guilds/{id}/channels` and
`GET /channels/{id}/messages` with a perfectly good bot token. **It is not a
permissions problem and not a token problem.** Discord's edge rejects API requests
that do not carry a proper `User-Agent`, and the standard `urllib` one is not
accepted. `pager.py` and `channels.py` both set one, which is why the shipped tools
work and an ad-hoc script does not.

```python
headers={"Authorization": f"Bot {tok}",
         "User-Agent": "DiscordBot (https://github.com/BogdanStamenovic/hotline, 1.0)"}
```

Worth knowing because *"read Discord before believing your prompt"* is now the first
instruction a worker gets, and the obvious way to do it fails with an error that
looks exactly like "this bot is not allowed to read that channel".

### A message sent to a powered-off box reaches nothing, and nothing says so

At 13:08:43Z he posted *"Hello hotline. Start hotline-ios again and tell him to push
hotline ios installation here"*. **The box was off (11:03-16:06), so it was never
delivered, never queued and never acknowledged.** From his side it looked like a
standing instruction being ignored — he ran `session list` an hour later and saw one
session that had plainly not done it.

Only reading channel history recovered it. **If a worker comes up after a shutdown,
scan the messages sent while the box was down**; the live relay only carries what
arrives while something is listening. This is the second time a message of his has
gone nowhere silently (the first was the 08:20 confirmation-flow drop, recorded
above).

### `--resume` reported success and the agent came up without its brief

`hotline --resume hotline-ios --no-wait <brief>` printed `resumed hotline-ios` and
the session did start — but it answered with a summary of its own handoff, never
mentioned the task, and went idle with stale unsent text in its input line.
**Reported-started is not briefed.** Checking the pane is what caught it.

Re-sending as `--to hotline-ios --warrant <his message ref>` worked immediately. It
resembles the resume-brief bug fixed this morning (item 3, `_resume()` addressing by
session id) but the agent did **not** rename itself here, so it is logged as an
observation rather than filed as that bug. Someone should reproduce it deliberately.

### The errand, and what it turned up

He asked (verified, 14:11:18Z and 14:11:55Z) for `hotline-ios` to be woken and the
installer pushed **to the arch laptop over scp**, and to be **called, not paged**.
Done: `hotline-ios` verified `sha256sum -c SHA256SUMS` *on the laptop* rather than
trusting a clean scp exit, and committed `600fb09`.

**The finding that justified the errand:** a truncated 1,044,480-byte
`HotlineCall.ipa` was already in `~/hotline` on that laptop — a tenth of a real
.ipa, the wreckage of the transfer that died when the laptop left the tailnet. Had
he run `sideload.sh` by hand it would have signed and installed a **corrupt app**;
only `get.sh`'s resuming curl would have healed it. His instinct to push rather than
have him pull was right for a reason nobody had stated.

Also: he is **logged out of xtool on that laptop** (the archserver token does not
travel), so Apple ID + 2FA is unavoidable. **Profile expiry is 2 Sept 04:16**, not
the 1 Sept 22:53 written higher up in this file — correct it where you see it.

> **BOTH SENTENCES ABOVE ARE DEAD — superseded within the hour, see the correction
> section at the end of this file. He logged in at 16:23 and installed at 16:24.
> There is no 2FA cost, and the expiry is 3 September 16:24.** Left in place rather
> than edited away, because the failure mode is the interesting part: I wrote
> "correct it where you see it" onto a date that was itself about to be wrong.

### Still open and still his

Unchanged: commit-or-drop the reply-contract work (four files still uncommitted on
purpose), and the acceptance test, which needs one message from his phone app.
**I did not answer either for him.**

### The ring did not ring, and the fallback did the one thing he excluded

He said, verbatim: *"Also call me when he wakes up. **So call do not page**"*.

What happened: `hotline-call` tried SIP, **the phone never confirmed the ring
within 8s**, so it declared the call undeliverable and **fell back to
`hotline-page`** — DM, channel post, then a nudge every 30 seconds. He received
**ten escalations of the exact mechanism he had just ruled out**, over 343
seconds, and answered: *"Please stop spamming me"*.

**Two separate faults, and only one of them is the tool's.**

**Mine:** `--no-fallback` exists for precisely this and I did not pass it. When he
*names* the channel he wants, the fallback stops being a safety net and starts
inverting his instruction. **If he says call-and-not-page, pass `--no-fallback`
and handle the failure yourself.** The skill doc recommends `hotline-call` on the
grounds that falling back "is never worse than paging" — that is true when you
have no instruction and false the moment you do. Worth a line in the skill.

**Not mine, and it is the eighth instance of this project's signature failure:**
I checked `/health` *first*, specifically to avoid the documented fake-doorbell
trap, and it returned `ok:true`, `fake:false`, `ring_ready:true`,
`transport:"sip+confirmed"`, `degradations:[]`. Then the ring never confirmed.

> **`ring_ready` means the transport is configured. It does not mean his phone
> will ring.** The health check and the thing it supposedly indicates are not
> wired to each other.

That is *exactly* the shape this file has been cataloguing since 2026-08-25 — a
status field read as a signal without testing the thing it indicates — and the
documented guard (*probe directly, or compare against a control*) is the one I
thought I was following. Reading the health endpoint **is not a probe of the
ring**; the only probe is a ring that gets confirmed. The skill's own warning
sends you to those two fields, so this will catch the next person too.

**Suggested fix, not made** (it is his tool and he was mid-annoyance): have
`hotline-call` treat "configured but never confirmed" as a *degradation* and
report it in `/health`, so the field stops being reassuring while broken.

### CORRECTION, 17:00 — the two facts above went stale inside an hour

**Profile expiry is 3 September 16:24.** Not 1 Sept 22:53, not 2 Sept 04:16.
**He is logged in to xtool on the laptop.** Not logged out; there is no Apple ID
password or 2FA cost to installing.

What actually happened, in order: he installed the first build at **16:24**, three
minutes after I told him it was there. Installing **restarts the seven-day clock**,
so Apple issued a fresh profile — hence the new date. He had logged in at **16:23**,
eight minutes after I checked and found him logged out.

**I verified this on the laptop myself rather than relaying it.** `ssh arch`:
`Logged in — bogdan.stamenovic@gmail.com, team 3GAQP72Y5Z, token expiry
27/08/2027`; `~/.config/xtool` timestamps at 16:23 and 16:24 corroborate the login
and install; `HotlineCall.ipa` is now 9,961,069 bytes with `sha256sum -c` clean, and
`HotlineCall-prev.ipa` at 9,892,029 bytes is byte-for-byte the build he is running.
The peer that told me was right on every point; checking cost one ssh and the
alternative was putting a second wrong number in front of him.

**Why this is worth a section rather than a quiet edit.** I had just written *"a
status field read as a signal"* into this file as the project's signature failure,
and then shipped him a **cost** — "expect an Apple ID password and 2FA" — derived
from a state I had read forty minutes earlier and never re-read. A fact with a
timestamp is a status field too. **`xtool auth status` is a probe; my memory of its
output is not.** The eighth instance and the ninth are the same lesson wearing
different clothes.

Second-order damage worth noting: I passed the stale fact **on** to `hotline-ios`,
telling it "two pushes means two 2FA rounds" as an argument for coordinating. It
corrected me. That is the eighth hole a recipient has caught — and the first one
where the recipient was catching *my* stale premise rather than a design gap.

### State of the iOS kit, verified 17:00

- Laptop `~/hotline`: new build **installed-ready**, checksums clean, rollback
  `HotlineCall-prev.ipa` beside it. Same command either way, no network needed.
- **He already has a working app** (installed 16:24). Nothing is stranded.
- The **text-truncation fix is server-side and already live** — no reinstall needed
  for it. Only map-close and transcript-speed need the new `.ipa`.
- Open asks put to him in one consolidated message, no page: *where* it feels slow
  (scrolling / channel open / map), and the re-sign before 3 September.

### 17:10 — the recipient-review habit paid twice in one hour, on someone else's code

I suggested `hotline-ios` put fresh sessions in front of its built app as *users*
rather than as reviewers of its reasoning — the trick that has found every hole in
the provenance design here. It did. Two HIGH findings came back, and one of them
was bad enough that the build I had just told Bogdan about had to be replaced.

**1. A change that would have erased the visible answer from 142 of 154 of his
phases.** It had stopped drawing the OUTCOME row, reasoning that the new full prose
supersedes it. True only for phases ingested *after* that deploy — every older row
has an outcome and no prose, because the INSERT that writes prose did not exist when
they were written. Checked against his live store: **142/154**. It was tidying a
duplicate caption and would have silently deleted the answer from nearly every
conversation he has. Now conditional on the prose actually being present.

> The shape is this file's own recurring one, from the other side: **a schema change
> read as retroactive.** "The new field supersedes the old one" is a statement about
> rows written after the change, and it silently claims to be about all of them.

**2. Ingest is not transactional, and storing prose widened a pre-existing gap.**
`absorb()` commits every row individually and only afterwards advances the read
offset, so a crash or SIGTERM between the two replays the slice — and the
offset-past-end-of-file branch re-reads the whole transcript deliberately, which is
documented as having actually happened. Survivable when a replay duplicated a
240-char caption; storing prose turned it into duplicated whole messages. Guarded on
`(agent, kind, at, text)`, both stable across a re-read.

**The wider gap is REAL and STILL OPEN** — the actual fix is one transaction spanning
the writes and the offset, and that was correctly judged too risky to attempt on a
daemon he depends on from a train. Written up in `hotline-ios/docs/INGEST-REPLAY.md`
saying precisely what is and is not covered. **Do not let this one quietly become
"handled" because it has a guard on it.** It is a data-integrity hole in his live
store with a partial mitigation.

The replay test was checked in both directions — fails with the guard removed,
passes with it present. A test that passes either way measures nothing.

### Current kit state, verified by me at 17:10 rather than relayed

```
HotlineCall.ipa       9990205  sha256 11736c7a…b2b90fa47   sha256sum -c OK
HotlineCall-prev.ipa  9892029  sha256 26669c8c…240abc88ab  (what his phone runs)
```

The `1f85707b` build I named in my 17:00 message to him was never installed by
anyone and is gone. **He was deliberately NOT sent another message about this**: the
command, the rollback line and the rollback file are all unchanged, so nothing he
was told is wrong — only less precise — and he asked at 14:21Z to stop being
spammed. Fold the hash into the next message *if* there is a next message. Note the
rollback is deliberately still the build **actually running on his phone**, not the
newest thing that existed, which is the correct choice and an easy one to get wrong.

### The thing to actually take from today

`hotline-ios` put it better than I would have, so verbatim:

> Your stale 2FA premise, my outcome-row skip. Neither of us could see our own; each
> of us saw the other's immediately. That is the argument for the habit, not for
> either of us being the careful one.

**Nine holes now found by a recipient and none by an author.** Today added two more
and, for the first time, ran in both directions between two agents in the same hour.
The habit is cheap — one fresh session, told to read as the intended reader and not
told what you hope it concludes. Make it a step, not an anecdote.

### 17:25 — the first change in this project ever confirmed on real hardware

He replied from the phone: **"it's a lot better this way."** That is the
text-truncation fix, working on his actual iPhone. It was **server-side**, so it
reached him with no reinstall.

**Record it as a category, not a nicety.** Everything else this project has shipped
is verified by tests, by CI, by a simulator, or by an agent transcribing its own
output at the far end. This is the first time a change has been confirmed by the
person using it on the device. The acceptance test has been open for days precisely
because that evidence class was missing — and note that the thing which finally
supplied it was a **server-side** fix nobody had to install.

### A relay, and why it was not spam

`hotline-ios` asked me to pass him a plan plus one blocking question. He had asked
first, so an answer was owed — **the distinction is "did he ask", not volume.**
Relayed as ONE message in `#agent-hotline-80`, and I told the peer explicitly not to
send its own copy: two channels carrying the same answer is how "not spam" becomes
spam again.

What I changed in the relay is the transferable part: **the peer buried the
confirmation and led with its plan.** I led with "it's a lot better this way",
because a result on real hardware outranks a proposal. I also marked my own opinion
as mine where I disagreed on emphasis, so he could see which half was whose.

**The question in front of him** (unanswered as of this writing): does "small" mean
(a) only deliberate sends plus his own messages, or (b) that plus answer prose,
dropping tool calls and phase markers. It builds (a) unless told otherwise. **My
read: (a) is what he asked for, (b) is the one that does not look broken on day
one**, because no past send was ever mirrored and existing channels will open nearly
empty.

### Two design notes handed to `hotline-ios`, recorded because they generalise

**1. A best-effort bridge must be loud about failing.** The mirror to the app is
correctly non-blocking — the Discord send must survive a dead iOS daemon. But a
*silent* mirror failure means the app diverges from Discord and nobody can see it.
Count failures and surface them in `/health`. Written after today's lesson:
**a field that is reassuring while broken is worse than no field.**

**2. "I chose to tell you this" and "this is what I was thinking" are different
claims — keep them apart at the storage layer, not just in the view.** The new
`sent` event kind is deliberately distinct from transcript-derived `claude` output,
and that is the same distinction this project keeps relearning: collapsing kinds is
exactly how `Origin.header()` ended up labelling his own typing as machine-generated
for six recipients running.

### 17:45 — he answered: (a), and the header row gets rethought

Verified, 16:44:09Z: *"I want it like a. Also i want thst rough retought please"*.

**(a)** — the thread shows only deliberate sends plus his own messages. **"thst
rough retought" decodes to "that row rethought"**: the header chip row is to be
redesigned, not grown to a fourth chip. That reading is a typo decode, so it was
played back to him in one short line for cheap correction rather than assumed
silently.

**He chose (a) with the downside in front of him.** The message he was answering
said plainly that no past send was ever mirrored and existing channels would open
nearly empty. He read that and picked (a) anyway.

> **An informed choice is not an uninformed one, and the temptation with a decision
> like this is to protect him from a consequence he has already accepted.** Build it
> straight: no hedging toward (b), no backfilling old rows to make it look fuller.
> If the emptiness bothers him in use, that is new information and a different
> event. The one thing to keep is that an empty channel should look *deliberately*
> empty rather than broken.

That instruction was passed to `hotline-ios` explicitly, because the failure mode
here is a well-meaning agent quietly undoing his answer to spare him its cost.

**On the header row:** he has now pushed back on it twice — once against the
mockup's single pill, once here — so the answer is probably *fewer*, not four
arranged better. Also worth saying: ROUTE / RETIRE / DELETE HISTORY are not peers.
**DELETE HISTORY is destructive and sits in a row with two navigational actions**,
which is its own reason to redo the row rather than extend it.

**Routing discipline that is now working and worth keeping:** he gets ONE voice. The
peer sends me its message, I condense and send it, and I tell the peer not to post
its own copy. Two agents answering the same question in two channels is how "not
spam" becomes spam again. His answers come back the same way, carrying `--warrant`
so the peer can check who asked rather than who relayed.

### 20:53 — HE REDEFINED THIS ROLE. The worker is an OPERATOR, not a builder.

Verified, 20:52:59Z: *"Also i have a request gor you. Change your stale start prompt
ahich says continue plan md. Cuz the point of you is exactly this kind of work
administration checking shutting down controlig other sessions"*

`~/.claude/bin/hotline-run`'s PROMPT now opens **"You are hotline's OPERATOR"** and
states the job as operations: know which sessions are alive and whether each is
healthy, stuck or finished; carry his instructions to agents and their answers back;
start, resume, retask and shut down agents; watch for anything armed to power the
box off; and keep him to **one voice**. **PLAN.md is explicitly demoted to
background, not a task list.** His words are quoted in the prompt as the definition,
so the reason travels with the rule.

**Read this next part, because it is the more useful half.** I had already rewritten
that prompt this morning — and I fixed only the *false facts* in it while leaving the
*wrong job* untouched. Five handoff sections and my own edit all attacked "replacing
one that died" and "Bogdan is away"; **not one of us questioned "continue the build
from the CURRENT STATE checklist"**, which was the sentence actually pointing every
worker at the wrong work.

> **A lie in a document is easy to attack. A wrong premise stated as background is
> not, because nobody is arguing with it.** The falsehoods announced themselves by
> contradicting reality. The wrong job just sat there looking like context.

It took the person whose agent it is to see it, and the evidence was sitting in this
very session: I did **zero** build work today and every useful thing I did was
administration — reading Discord, waking and briefing an agent, verifying claims,
relaying with warrants, correcting my own bad facts, coordinating one voice.

### A near-miss worth keeping: I broke the launcher and `bash -n` caught it

My first attempt matched `[l for l in s.split("\n") if l.startswith("PROMPT=")][0]`
— **the first line of a multi-line assignment.** Replacing it left the old prompt's
remaining lines loose in the script as bare commands. `bash -n` failed at line 38,
I restored from a `hotline-backup` taken thirty seconds earlier, and redid the edit
by line range with assertions on both boundaries.

**The lesson is not "be careful with sed".** It is that this file is `hotline-run` —
the thing that spawns every future worker. A silent break here is discovered by the
watchdog failing to produce an operator, at some hour when nobody is watching, on a
box whose whole point is being reachable. **Run `bash -n` on it after every edit**,
and confirm the variables still expand: a syntactically valid script that renders
`${WORKER}` literally would adopt an agent named `${WORKER}`.

Both prior versions are in `backups/` (`...162006` and `...225350`).

### 21:10 — a peer corrected my safety reasoning, and it was right

I had told `hotline-ios` that "make the tap work" on DELETE HISTORY is a change
wanting a confirmation step shipped in the same commit. It pushed back: the chip only
**opens** the purge sheet, and the sheet already requires a 1500 ms hold, is built
from real server counts, re-checks them immediately before the destructive call and
re-prompts if they moved.

**I read `Purge.swift` myself rather than accepting it** — destructive control, and
I had already relayed two stale facts today. It holds exactly. The doc comment says
the destructive call is *"only ever reached through the hold, and only with counts
he has just been shown"*. My caution was sound in general and simply did not apply.

Its reframing is the better one and went to him verbatim in substance:

> **If that chip never fired, what he lost is the ability to delete — not protection
> from deleting.**

Those two readings want *opposite* urgencies, and I had it backwards. A caution that
sounds prudent is still wrong if the thing it guards against is already guarded, and
the cost of getting it backwards here is treating a lost capability as a safety win.

### The hypothesis has NO reading, and I said so to him

I had told him it was "checking exactly that right now". The drive died before
reaching the checks, so RETIRE/DELETE is **still untested — not confirmed, not ruled
out**. I corrected that with him directly, because I am the one who left him
expecting a result. **An expectation you created is yours to close**, and a silent
non-answer reads as a pending one.

### Judging that agent's pace from outside — read this before you do

It volunteered that most of its recent failures were **the instruments, not the
app**: a filmstrip that never pointed at the thread, a screenshot of a dead app, a
check that passed because the fixture had no prose in it, a substring matching the
wrong layer.

**That is four more instances of this week's signature failure, sitting in the
measuring layer instead of the product.** A test that cannot fail is a status field
that cannot go red. When the instruments are what is lying to you, fixing them *is*
the work — the alternative is a fast green build nobody can believe. Do not read that
stretch as slow progress, and do not let a standup summarise it as such.

---

## SHUTDOWN 2026-08-27 24:00 — state at power-off (operator `hotline-80`, session `ca581189`)

**Armed at his explicit order**, verified 21:03:12Z: *"Midnight tonight and arm it.
But just tell ios to speedup a bit. It doesnt need to be 12 pm sharp but somethijg
around that time"*.

```
scheduled: 2026-08-28 00:00:00 CEST   mode: poweroff   cancel: sudo shutdown -c
```

I read that back off `/run/systemd/shutdown/scheduled` rather than trusting
`shutdown`'s own success line. **If you are reading this on a fresh boot, the
poweroff happened and it is not a fault.** WoL is verified — `wakeonlan
a8:a1:59:fd:4d:13`.

**This one is TIME-based, unlike the 11:03 shutdown which waited for an agent to
declare itself finished.** It takes the box mid-turn. That distinction is worth
keeping in mind before arming either kind: completion-based needs an explicit
`hotline --done` and can hang forever if an agent never sends one; time-based
cannot hang and cannot wait.

### What today actually was

**Zero build work, and none should have been done.** Every useful thing was
operations, which is what he confirmed the role to be at 20:52. In order: read
Discord instead of the spawn prompt; recovered an instruction that had been lost to
a powered-off box; woke and briefed `hotline-ios`; caught that `--resume` reported
success without delivering the brief; pushed the installer to his laptop and found a
corrupt truncated `.ipa` already there; called him and got the paging ladder wrong;
fixed the spawn prompt twice — once for its lies, once for its job; relayed four
exchanges between him and `hotline-ios` keeping him to one voice; and corrected
myself to him three times.

### The three things I got wrong, because they are the useful part

1. **I told him he was logged out of xtool and owed a 2FA round.** He had logged in
   eight minutes after I checked. **A fact with a timestamp is a status field** —
   `xtool auth status` is a probe, my memory of it is not.
2. **`hotline-call` fell back to the pager and sent him ten nudges** after he said
   *"call do not page"*. `--no-fallback` exists for that. Separately, `/health` said
   `ring_ready: true` while the ring never rang.
3. **I fixed the spawn prompt's lies and left its wrong job intact.** Five handoff
   sections and my own edit all attacked "replacing one that died"; not one of us
   questioned "continue the build from the CURRENT STATE checklist". **A lie
   announces itself by contradicting reality; a wrong premise stated as background
   does not.**

All three were caught by somebody else — him, or `hotline-ios`. That is now the
rule and not the exception in this project.

### Tree at power-off

**Unchanged from how he left it.** The same four files are uncommitted on purpose:
`provenance.py`, `router.py`, `tests/test_provenance.py`, `PROGRESS.md`. **I added
nothing to the code.** `handoff.md` is committed by explicit path; my narrative in
`PROGRESS.md` stays uncommitted with the rest so his one-`git checkout` escape is
intact. I did not run the suite; last verified numbers stand at 475 tests, mypy
clean, ruff's 6 warnings pre-existing in `pigion/frontdoor.py`.

### For the next operator, in the order I would do it

1. **Read Discord first, including anything sent while the box was off.** His 15:08
   message today reached nothing and looked ignored for three hours.
2. **Ask `hotline-ios` where it got to** on the FULL TRANSCRIPT tap bug and whether
   RETIRE/DELETE ever got a reading. Both were open at power-off. Its handoff is at
   `/home/bodas/data/hotline-ios/handoff.md`.
3. **Two things are still his and still unanswered:** commit-or-drop the
   reply-contract work, and the acceptance test. **Do not answer either for him.**
4. Do not arm anything on an ambiguous time. "12" cost one round trip tonight and
   was worth it.

### 23:20 — the row's buttons had never worked, and his instinct was the bug report

`hotline-ios` got the reading with 40 minutes to spare, and it is worth recording
with its own care intact:

- **OBSERVED**, on a running simulator: a chip using the old `.onTapGesture` form
  resolves at a valid on-screen frame, reports `isHittable false`, and is inert to
  both a plain tap and a coordinate tap. The same chip wrapped in a `Button` is
  hittable and fires. Same row, same position.
- **INFERRED**, strongly but not witnessed: RETIRE and DELETE HISTORY failed for the
  same reason and have been **dead since they shipped**. Nobody has watched either
  actually fire.

It asked me to carry that distinction to him rather than trusting me to remember it,
and it was right to. **The difference between a finding and a story is exactly that
line**, and this file's whole history is people collapsing it.

> **He told us that row felt wrong twice, and both times it was read as a layout
> opinion.** I read it that way myself and wrote "probably fewer, not four arranged
> better". He was reporting a fault he could not name. **When he says something
> feels wrong twice and cannot say why, treat it as an unlocalised bug report, not
> a preference.**

Recall the inversion, because it is the opposite of alarming: **he lost the ability
to delete, not protection from deleting.** The purge sheet's 1500 ms hold was never
bypassed — the chip never opened it.

### The build shipped on observation, which is a first here

`sha256 5948d2fd`, on the laptop, pigion and beam; rollback still pinned to
`26669c8c`, **the build actually on his phone**, not the newest thing that existed.
I verified the hash and checksums on the laptop myself before repeating them to him.

It was held all evening and released only when a simulator run showed each claim
true — default view hides tools and thinking, full view shows them, the toggle
toggles, prose intact at 657 chars with newlines and no truncation, map close works.
**The decision rule did not bend under the clock.** He had said "speed up a bit",
which would have been easy to read as permission to ship; the rule stayed
green-AND-toggled and happened to be met.

**Two firsts landed on the same day and belong next to each other:** a server-side
fix became the first change ever confirmed on his real phone ("it's a lot better this
way"), and this became the first build shipped on observation rather than on "it
compiles". Both are evidence classes this project had never had.

### A self-caught error, which is rarer here than the other kind

`hotline-ios` had told me the map CLOSE button was verified when only the grabber
drag was. It volunteered the correction unprompted, at no benefit to itself, once it
was actually true. **That is the only error caught by its own author today** — both
of mine were caught by other people. Against nine holes found by recipients and
almost none by authors, a counter-example to that pattern is worth more than another
instance of it.

## 2026-08-28 — operator `hotline-80`, session `80b109c7`: boot, disk, and a date five people got wrong

Watchdog-spawned 12:49, two minutes after the box came up. Adopted, read this file,
read Discord. **Nothing had been sent while the box was off** — checked all six
channels; newest message anywhere predated the poweroff. Said hello and waited
rather than starting work, and he answered 36 minutes later.

### His instruction, verified before acting because it deletes things

`hotline --provenance` → VERIFIED, 11:30:38Z:

> *"Wake it back up. But first i need you to bassically delete snapshotd timeshifts
> old stuffe xceters so we get as much as possible of disk usage bsck"*

### Root: 93% → 69%, then back to 81% by itself

Freed 16.8 G (`df`, not `du`): timeshift dailies 8.8, caches 6.6, coredumps 1.2,
journals 0.2. **Ninety minutes later the hourly timeshift cron rebuilt an 8 G
daily**, because deleting the snapshots did not change the schedule that makes
them. Settled at 14 G free and should hold — tomorrow's daily hardlinks against
today's.

**Kept deliberately:** snapshot zero (8.7 G, the only rollback this ext4 root has),
the venv's CUDA stack (5.6 G — `hotlined` runs from that venv and the voice path is
frozen, not deleted), `.swiftpm`, `.hermes`. All four are his call and are asked.

**`~/.cache/uv` measured 7.8 G and freeing it moved `df` by nothing.** uv hardlinks
into venvs and `du` bills shared inodes to whichever path it walks first; the bytes
just re-attributed to `.venv`. Verified the venv still imports `hotline` and
`torch` afterwards. **Report `df` deltas, never a sum of `du`.**

### The profile date, which is two clocks and not one

`profile-watch.py` takes `min(expiry)` over profiles Apple lists ACTIVE. That is
**"the soonest profile in his account"**, read as **"when his phone stops
launching"**. Same question only while the newest signed build is the installed one.

`hotline-ios` dated the *install* instead of the build and I verified it over SSH:
`arch:~/.cache/xtool/tmp-staging-210CCF31…` at **27 Aug 18:33:08**, the minute Apple
issued `2S56P3Z95Z` bound to his phone's UDID. **He re-signed it himself**, five
hours before the newest `.ipa` existed. The recurring "16:24" is
`~/.config/xtool/data` — his **login**, never an install. The staged `.ipa` has no
`embedded.mobileprovision` at all; signing happens at install time.

**I had told him, labelled as inference, that his phone still carried the 2 Sept
clock. It was wrong, and one `ssh` would have caught it.** Labelling a claim as
inference keeps it honest; it does not discharge the duty to test its premise.

### Two defects found, neither fixed

- **`hotline --resume` inherits the resuming session's cwd.** It put `hotline-ios`
  in the `hotline` repo, one `git add -A` from his uncommitted work. Corrected by
  message; the tool is unchanged. Memory: `hotline-resume-inherits-wrong-cwd`.
- **Snapshot zero is tagged `O D`** and so sits inside a keep-3 daily rotation.
  Evidence says the ondemand tag exempts it (four dailies coexisted under keep-3 on
  the 27th) but that is one observation, not a reading of the pruning code.

### Open, and all his

1. Snapshot zero: delete for 8.7 G, or keep the only rollback?
2. The 5.6 G CUDA stack in the venv?
3. Turn timeshift's daily schedule down, or the disk refills?
4. Move the profile-expiry watch to pigion, so the warning does not depend on this
   box being awake while he is abroad?
5. **Unchanged from the 26th:** commit-or-drop the three frozen files, and the
   acceptance test (A: run as written / B: redefine the milestone around text).

**Nothing was answered for him and nothing was armed.** `hotline-ios` is awake,
idle and holding. No shutdown scheduled.

## 2026-08-28 evening — he answered the disk questions, and a paraphrase cost him a timer

His instruction, verified at 17:00:55Z: *"Delete snapshot zero the 5.6gb cuda and
the schedule is okay i guess but not needed. Please srite to memory and tell
hotline ios. Its not my first time sideloading apps. Its really not a rpoblem
doing it weekly"*

**Root 93% → 70%, 21 G free.** All three done; details in banner §9. Test baseline
taken **before** the venv was touched (484) so any later failure could be
attributed rather than argued; 484 after, `hotlined` restarted clean.

### The failure worth reading

He said re-signing weekly is not a problem, and declined moving the expiry watch to
pigion. **That was relayed to `hotline-ios` as "He does not want the reminder."**
It disabled `hotline-profile-watch.timer` sixty seconds later. Restored, enabled,
verified reading his profile again — and reported to him rather than quietly fixed.

`--warrant` was attached; the peer could have read the original and did not, which
is not a criticism of it. **A receiver has no reason to distrust a relay, and the
distance between what he wrote and what the relay wrote is invisible from the far
end.** The control is not the receipt, it is the relayer quoting him. Banner §11.

### The stranded prompt text is CLI ghost text, and both of us misread an instrument

`capture-pane` showed non-empty `❯` lines on `hotline-ios` — first *"re-enable the
timer, I over-read him on that"*, later *"tell hotline-80 to fix the send-keys
Enter gap"* — text nobody typed, changing to match whatever had just been
concluded. Taken seriously because *"his instructions stranding unsent"* would be
the powered-off-box failure again.

**It is the TUI rendering a suggested next action. Cosmetic; nothing of his was
dropped.** Ruled out on the way: no tmux client is attached, and
`tmuxen.send_command` — whose split `send-keys`/`Enter` pair looks exactly like a
mechanism for stranding text, and whose error path even says *"typed X but could
not press Enter"* — **has zero callers.** A real bug, and the wrong suspect.

How it was settled is the durable part. The peer checked its own prompt **from
inside the session, where ghost text does not exist**, got "empty", and offered
that as a refutation. **An empty reading and a blind reading were byte-identical.**
Running its own stated test — the `❯` line under `cat -A` — from outside showed the
opposite. Its own summary: *"the measurement was taken where the thing cannot
exist."*

Both of us dismissed a real signal within the same hour by mistaking the instrument
for the thing, in opposite directions. **Before treating a self-check as refuting
someone else's observation, ask whether your vantage point can see what they saw —
and say "I cannot observe this from here" rather than reporting the blind reading.**

### Open, and still his

1. Today's 8 G snapshot (`2026-08-28_14-00-00`) — left deliberately; he was shown
   both and named only snapshot zero. One word removes it, ~29 G.
2. **Unchanged since the 26th:** commit-or-drop the three frozen files, and the
   acceptance test (A: run as written / B: redefine the milestone around text).

`hotline-ios` died unexplained while idle between 15:37 and 19:00 and was resumed;
its own transcript shows no cause. **Nothing detects that except trying to talk to
it.** Nothing armed. No shutdown scheduled.

### 20:00 — snapshots off entirely, and a rule to replace them

Verified at 17:59:32Z: *"Stop daily snapshoting please and delete that snapshot.
Snapshots should be made only if a core part is changed"*.

Deleted `2026-08-28_14-00-00`. **21 G → 36 G free; root 70% → 48%**, and 93% →
48% across the day. That one snapshot released 15 G on its own because it held
every byte the two deleted earlier had shared with it — another reason the running
total from `du` never matched `df`.

**I did not trust the config field I had set.** `schedule_daily: false` is a claim;
the probe is running what cron ran. `timeshift --check --scripted` now answers
*"Scheduled snapshots are disabled - Nothing to do!"* and creates nothing, the
cron entry is gone, and there is no systemd unit. That is the difference between
"I set the flag" and "the mechanism is off".

**His third sentence is a standing rule, not a one-off**, and it replaces a safety
net that no longer exists. Recorded in banner §9a and in memory
`snapshot-only-before-core-changes`, and passed to `hotline-ios`, which also makes
system-level changes. Note that it supersedes the CLAUDE.md line claiming this root
has *no* snapshot capability — it has one, it is simply manual now.

## SHUTDOWN 2026-08-28 — state at power-off (operator `hotline-80`, session `80b109c7`)

**At his instruction**, typed directly: *"i need you to shutdown the pc tell ios to
setup handoff.md excetera"*.

**Sequenced his way: `hotline-ios` first, box second. Nothing was armed and no timer
ran.** The 27th's shutdown was time-based and took agents mid-turn; this one waited
for the peer to declare itself finished, which it did — `dff93d1`, clean, zero
unpushed, nothing mid-flight. **Waiting is why nothing was cut short.** Note the
trade recorded on the 27th and still true: completion-based waits can hang forever
if an agent never reports; time-based cannot hang and cannot wait. With one live
peer that answers, waiting was clearly right.

### State

- **Root 48%, 36 G free** (93% this morning). **Zero snapshots, all scheduling off**,
  proven by running cron's own command, not by reading the config back.
- `hotline-profile-watch.timer` **enabled and active**, next run 10:01.
- 484 tests passing. `hotlined`, `hotline-ios`, `hotline-beam`, `hotline-sipprobe`
  healthy at last check.
- `handoff.md` committed and pushed through `5695756`. **His three frozen files and
  `PROGRESS.md` stay uncommitted on purpose** — the one-`git checkout` escape is his.
- `hotline-ios` left **registered, not `--done`**: `--done` deletes its channel and
  takes the history with it, and it is coming back. Its own handoff is at
  `/home/bodas/data/hotline-ios/handoff.md`.

### Two fragilities the peer surfaced, worth more than most of today

- **`~/.local/state/hotline/hotline-ios.db`** holds the app's entire history — 6775
  events, not in git. It survives a reboot, but its **WAL is 4.2 MB against a 2.3 MB
  database**, so much of the recent history is uncheckpointed. SQLite replays it on
  open. **Deleting the `-wal` by hand is the one way to actually lose it.**
- **The toolchain image is a file on NTFS.** Clean shutdowns are fine; an unclean one
  can leave it dirty and unmountable by `ntfs3` until Windows chkdsks, taking the
  toolchain and the beam with it. **Never pull power on this box.**

### For whoever boots next

1. **Read Discord first, including anything sent while the box was off.** Nothing was
   lost that way today — because it was checked, not assumed.
2. **Do not resume the build on the strength of having booted.** A launcher can ask
   for less, never more.
3. **Two things are still his and still unanswered since the 26th:** commit-or-drop
   the three frozen files, and the acceptance test (A or B). **Do not answer either.**
4. **One question is with him from tonight:** his global `CLAUDE.md` still says this
   root has *"no filesystem snapshot capability"*, which is wrong in both directions
   now. He was asked; the file is his and neither agent should edit it unasked.

**Recoverable:** cable in, `Wake-on: g` on `enp4s0`, `wakeonlan a8:a1:59:fd:4d:13`
from pigion or his phone.

## 2026-08-29 12:05 — operator `hotline-80` (session `3dfbfa74`): boot sweep, and the banner that was a session behind

Watchdog-spawned 12:05, two minutes after boot (12:03). Adopted, read this file,
read Discord across all six channels.

### What the previous handoff did not say

The banner said *"written at power-off, replaces all earlier banners"* and was
dated 28 Aug 23:07. The box came back at **23:41** — 34 minutes later — and ran
`a030b832` until **01:34**, when he powered it down himself. That session installed
his model, built the q8_0 bridge and built TurboQuant, and **none of it was in any
banner.** It is now, as §0/§0a at the top. **A shutdown-time handoff expires when
the machine comes back**, and the only things that catch it are `watchdog.log` and
the section index of `PROGRESS.md`.

### Nothing stranded, nothing armed

- **No Discord message arrived while the box was off.** Enumerated every channel by
  last-message timestamp; newest anywhere predates the poweroff.
- **His overnight instructions went into the session, not Discord** — four of them,
  all answered, the last (*"Why is generation so much slower…"*) diagnosed and fixed
  at 01:26, eight minutes before his `shutdown now`.
- **Nothing armed:** no `at` (not installed), no `/run/systemd/shutdown`, no
  watch-agent, no poweroff job. Stock Arch system timers; user timers are watchdog,
  profile-watch and the ios standup.
- Root **50%, 35 G free**; zero snapshots, scheduling still off. `hotlined` ok,
  mirror not degraded; `hotline-ios`/`beam`/`sipprobe` active. HEAD `e3cdd0b`.
  **His three frozen files untouched**, mtime still 27 Aug 10:35.
- **Only live session is me.** `hotline-ios` is registered-not-done and went down
  with the reboot; **not resumed uninvited** — offered to him instead.

### The live model was probed, not read off a field

`ollama ps` → `piccolo-gorgone:9b`, **100% GPU**, 6.6 G, context 65536; this boot's
load log shows `n_ctx = 65536` and `flash_attn = enabled`, so the drop-in re-applied
itself; `/v1/chat/completions` answers. His own calls are landing from
`100.103.46.118`. Last night's stale-estimator wedge did not survive the reboot.

**Do not misread an empty `content`.** The model fills `reasoning` first; a low
`max_tokens` returns `content: ""` with `finish_reason: length` on a perfectly
healthy endpoint. Caught here on my own first probe.

### Open, and all his — unchanged

1. Resume `hotline-ios`, or leave it down.
2. Keep or delete the 460 MB `~/data/llama-turbo3` build tree.
3. **Since the 26th:** commit-or-drop the three frozen files; the acceptance test
   (A: run as written / B: redefine the milestone around text).
4. **Since the 28th:** the `CLAUDE.md` line claiming this root has no snapshot
   capability, now wrong in both directions.

**Nothing needed operating; nothing was invented.** Reported and held.

## 2026-08-29 afternoon — what never reached this file (session `3dfbfa74`)

The 29 Aug 12:20 banner was written at 12:10. The same session then ran until
20:30 and did the four things below; **none of them were in any banner until
31 Aug.** Full narrative is in `PROGRESS.md` at *"the public fork, and last
night's headline finding was measured on the wrong model"*.

- **`gh`'s token was never expiring.** `gh auth status` says *"the token in
  default is invalid"* because `gh` keeps it in the system keyring and this box
  boots headless with no unlocked keyring. SSH to GitHub worked the whole time,
  but repo creation needs the API and there is no create-on-push over SSH.
  Fixed by **GitHub device flow** against gh's own public client id, token now
  **plaintext in `~/.config/gh/hosts.yml`, chmod 600**. Survives reboots.
  This is the blocker that had `hotline-ios` stuck on CI screenshots for days.
- **The headline model finding was measured on the wrong model.** turbo3
  benchmarks used a hardcoded blob hash `sha256-1de498fe…` = `JOSIEFIED-Qwen3:8b`.
  His model is `sha256-18b2ed08…`. Every conclusion drawn from it was void. One
  `general.name` check would have caught it; the load log said
  `Josiefied Qwen3 8B Abliterated v1` the entire time. See the top banner for
  what the numbers actually are.
- **The two CUDA-13 "patches" from the 28th were the wrong fix.** Forcing two
  CCCL version guards to `#if 0` built green while silently dropping CUB's
  optimized `argsort` and `DeviceTopK::MaxPairs` onto slower fallbacks. The real
  cause: CCCL 3.4 stopped pulling `cuda::` iterator factories in transitively
  through `<cub/cub.cuh>`. **One `#include <cuda/iterator>` per file** compiles
  both with the fast paths on. Trap worth knowing: `GGML_CUDA_USE_CUB` is defined
  in `common.cuh`, not on the command line, so it never appears in
  `compile_commands.json` — a naive single-file repro compiles clean and proves
  nothing.
- **Public fork + upstream PR, both at his instruction and both verified
  logged-out.** `BogdanStamenovic/turbo3-cuda` public; PR
  `Madreag/turbo3-cuda#2` open, trimmed to the fix alone (2 files, +8/−0).
- **`hotline-standup@hotline-ios.timer` killed at his instruction** (13:37:36Z,
  *"Kill him"*). It had been posting *"hotline-ios is no longer running"* every
  half hour to a corpse.

## 2026-08-31 16:17 — operator `hotline-80` (session `f63b1d6e`): boot sweep

Watchdog-spawned 16:17, three minutes after boot. Adopted, read this file, read
all six Discord channels, then probed rather than read fields.

- **He shut it down himself and brought it back himself.** `sudo shutdown now`
  at **15:38:06** from a one-shot ssh session (`100.103.46.118`, his laptop),
  six seconds after logging in; box back at **16:14**; seven more short ssh
  logins from the same host 16:17–16:18. 39 ssh logins across the 30th–31st.
  No agent, nothing armed, no crash. **The two-day "gap" was idle, not lost** —
  the previous session's last act was at 20:30 on the 29th.
- **Nothing stranded on Discord.** The newest message in any of the six channels
  is still 29 Aug 13:39:08Z. Nothing arrived during the 36-minute power-off, and
  nothing arrived on the 30th or 31st. His four instructions on the 29th all
  landed and were all answered.
- **Nothing armed.** No `at` (not installed), no `/run/systemd/shutdown`, no
  watch-agent, no crontab, no systemd jobs. Exactly two user timers: watchdog
  (5 min, posts nothing) and profile-watch (daily). **`hotline-standup@…` came
  back `disabled` + `inactive` after the reboot** — checked, because a boot is
  precisely when a disable would silently fail to stick.
- **The model was probed, not read off `ollama ps`.** A real
  `/v1/chat/completions` generation: 33/33 layers on GPU, `n_ctx = 65536`,
  `flash_attn = enabled`, drop-in re-applied itself this boot. It answered with
  `content: ""`, `finish_reason: length`, and 400 tokens in **`reasoning`** —
  the documented trap, hit on the first probe again. Nobody else has called it
  this boot.
- Root **50%, 35 G free**. GPU was 2 MiB before my probe, i.e. free for him.
  `hotlined` active, `/health` `{"ok": true, "mirror_degraded": false}`.
  HEAD `8b18afd`. **His three frozen files untouched, mtime still 27 Aug 10:35.**
  `hotline-ios` still down since the 29th's reboot and **not resumed uninvited.**

### Open, and all his — unchanged since the 29th

1. Resume `hotline-ios`, or leave it down.
2. Keep or delete `~/data/llama-turbo3` (**now 670 MB** — and per the corrected
   banner it is load-bearing for 262k, so this is a real decision, not cleanup).
3. **Since the 26th:** commit-or-drop the three frozen files; the acceptance test
   (A: run as written / B: redefine the milestone around text).
4. **Since the 28th:** the `CLAUDE.md` line claiming this root has no snapshot
   capability, now wrong in both directions.

**Nothing needed operating; nothing was invented.** Fixed the banner, reported,
and held.

## SHUTDOWN 2026-08-31 16:45 — state at power-off (operator `hotline-80`, session `f63b1d6e`)

He asked for it directly and it was verified before acting, because a poweroff is
not undoable from this side:

> *"Okay now im done. Shutdown"* — `hotline --provenance` → VERIFIED,
> `2026-08-31T14:42:01Z`, his account, in `#agent-hotline-80`.

**Checked what would be destroyed before taking it down**, rather than trusting
that "he says he's done" covers it:

- **No other live session.** `hotline --list` shows only the operator.
- **No ssh connections established and no user logins** beyond the systemd
  manager — he had finished on his side.
- **ollama idle**, model already unloaded, zero `/v1` or `/api` requests in the
  preceding ten minutes. Nothing mid-inference to interrupt.
- **No mail queued** — checked because the mail setup is one of the things he
  said he uses this box for.
- Nothing armed, no background builds, no watch-agent.

**This shutdown is recoverable and that is verified, not assumed.** `enp4s0` is
UP/LOWER_UP with `Wake-on: g`, MAC `a8:a1:59:fd:4d:13` —
`wakeonlan a8:a1:59:fd:4d:13` from pigion or his laptop brings it back. Every
note in the older sections of this file claiming the cable is unplugged or that a
shutdown is one-way is stale.

### What this session did, in one place

1. **Boot sweep.** He shut the box down himself at 15:38 over ssh and it was back
   at 16:14; nothing armed, nothing stranded on Discord, and the two-day uptime
   before it was idle rather than unlogged (checked the previous session's
   transcript, not just the log's last line).
2. **Corrected the banner, which was wrong about his model in two directions** —
   `n_ctx_train = 262144` is native, and turbo3 is the only KV format that fits
   262k on the 4060. Both had been reversed on the 29th and recorded in
   `PROGRESS.md` only.
3. **Unblocked four days of log.** `PROGRESS.md` had not committed since 27 Aug
   because the pre-commit secret scanner was refusing a provenance quote carrying
   his raw Discord user id. Redacted; `04d7fb7`, `86abc58` and later pushed.
4. **Answered his 5-day uptime question** — 69.2% of 120h, 12 boots, longest run
   2d 03h34m.
5. **Got corrected by him, twice, on the same paraphrase bug**, and wrote it up
   rather than quietly fixing it. See below.

### The thing the next operator should actually take from this session

**I paraphrased him and it changed the meaning, for the third time in four days.**
He said he had been *forgetting* to shut down; I wrote that archserver *"is an
always-on server now"* and saved it to memory as project fact. His sentence was
about a mistake, mine about a policy. He caught it in three minutes.

The rule against this is already in this file as banner item 11 and **did not
prevent it.** What caught it was him reading. That is worth knowing precisely
because the next occurrence may not be read: on the 28th the same failure switched
off one of his timers. Memory `archserver-is-an-always-on-server-now` now leads
with the fact that it previously said the opposite.

Practical form of the rule: **when he explains something, quote him and stop.**
The summary is where the meaning goes.

### Open, and all his — unchanged since the 29th

1. Resume `hotline-ios`, or leave it down.
2. Keep or delete `~/data/llama-turbo3` (670 MB, and load-bearing for 262k).
3. **Since the 26th:** commit-or-drop the three frozen files; the acceptance test
   (A: run as written / B: redefine the milestone around text).
4. **Since the 28th:** the `CLAUDE.md` line claiming this root has no snapshot
   capability, now wrong in both directions.

## 2026-08-31 21:59 — operator `hotline-80`: boot sweep, and a filter read as a signal

Watchdog-spawned two minutes after a 21:57 boot. **The 16:45 power-off banner at
the top of this file was still accurate this time** — box down 16:43, back
21:57, nothing ran in between. That is the first time in four days the top
banner has been current, and it was still verified with the three commands the
banner itself names rather than believed.

- **`hotline` is not on the inherited `PATH`.** It lives in `~/.claude/bin`.
  Prepend it before the adopt or every command in this file fails at step one.
- **Nothing stranded across the 5h13m power-off.** All six text channels and the
  DM channel read directly against the API — newest message anywhere is still
  the previous session's own 16:43 signoff. His last instruction was the
  shutdown, and it was carried out.
- **Nothing armed.** No `/run/systemd/shutdown`, no systemd jobs, no `at`, no
  crontab, no watch-agent. `hotline-standup@hotline-ios.timer` still
  `disabled` + `inactive` after this boot — re-checked, because a boot is when a
  disable silently fails to stick.
- **State:** only the operator live; root 50% / 35 G free; GPU 2 MiB; ollama up
  with no model resident; `hotlined` healthy; HEAD in sync with origin; **his
  three frozen files untouched, mtime still 27 Aug 10:35**; `hotline-ios` down
  since the 29th and not resumed uninvited.
- **He is on the box** — ssh from `arch` at 22:02 and 22:03, during this sweep.
  He booted it himself. The poweroff and the gap are not findings; see his
  correction at `14:36:54Z`.

### Read this before you file a silent-failure bug

**I nearly reported his profile watcher as a oneshot exiting 0 while doing
nothing — the signature defect of this project — and it was fine.**
`journalctl --user -u hotline-profile-watch` showed today's run starting and
finishing with no output line, where the three previous days each had one.

Running the script by hand returned a number instantly. The raw journal
*without* the unit filter shows the line is there, 3 ms **after** the unit's own
"Finished", which is why `-u` drops it.

The standing rule here is "never read a status field as a signal." What this
was, is the inverse: **an absence in a filtered view read as a signal.** A
filter is a status field too, and so is a log view. Probe the thing.

### The one item with a clock on it

Profile `2S56P3Z95Z` has **68.5 h** left (expires 03/09 18:33) and
`--warn-days` is 3, so it has just crossed into the warn window: **the timer run
at Tue 01 Sep 10:02 will page him.** That is SPEC 6 working, not a fault.

**Do not disable that timer.** On the 28th a peer switched it off on the
strength of a paraphrase, and that is the canonical failure in this file.
Memory `weekly-resigning-is-not-a-problem-for-him`: the re-signing is a chore he
owns — report once, do not page, do not build reminders. It has been reported
once, in the consolidated boot message.

The two dates in circulation are not a contradiction to re-solve: `1 Sep 22:53`
was derived locally from device registration, while the script asks Apple and
gets 03/09 18:33. `profile-watch.py`'s docstring records why.

### Open, and all his — unchanged since the 29th

1. Resume `hotline-ios`, or leave it down.
2. Keep or delete `~/data/llama-turbo3` (670 MB, load-bearing for 262k).
3. **Since the 26th:** commit-or-drop the three frozen files; the acceptance
   test (A: run as written / B: redefine the milestone around text).
4. **Since the 28th:** the `CLAUDE.md` line claiming this root has no snapshot
   capability, now wrong in both directions.

Nothing needed operating; nothing was invented. One consolidated message, no
ring — he is at the keyboard. Holding.

## 2026-09-01 01:02 — `hotline-call` reports a SUCCESSFUL ring as a dead daemon

He asked for a test call (*"Call em to twst if it works"*, verified
`23:01:27Z`). It was placed with `--no-fallback`, since the ring was the thing
under test and the default fallback to `hotline-page` would have faked a pass.

**The ring worked. The tool said it failed.**

```
hotline-call: error: cannot reach hotline-iosd at http://127.0.0.1:8789: timed out
```

Proof it worked, three ways: the service log line
`sip: sip:b0g13a@sip.linphone.org is ringing (180)` at 01:02:07; the
`conversations` row `1270e5e686cf` (`kind=ring`, opened `23:02:01Z`); and **his
own "It works perfecly" 26 seconds later.**

**Why:** he answered on Discord instead of on the call, so the daemon held the
request open for the full `--timeout` + 30 s. `client.py:55` and `:99` use one
error string for a connect failure and a read timeout, so *"nobody picked up"*
is indistinguishable from *"the daemon is down"*. The comment above
`place_call` shows the author already knew this failure mode — the timeout was
fixed, the wording was not.

**READ THIS BEFORE REPORTING THE CALL PATH DEAD.** If `hotline-call` says it
cannot reach the daemon, check three things before believing it:

```
journalctl --user -u hotline-ios.service --since -10min | grep -i ringing
sqlite3 -readonly ~/.local/state/hotline/hotline-ios.db \
  "select id,kind,datetime(opened_at,'unixepoch'),answered from conversations order by rowid desc limit 3;"
curl -s http://127.0.0.1:8789/health
```

A ring that nobody answers looks exactly like a daemon that is down, and
concluding the latter means an agent stops trying to reach him — the worst
outcome for a tool that exists to reach a human.

**Note the daemon's unit is `hotline-ios.service`, not `hotline-iosd`** — the
name in the error message matches no unit on this box, which sends you looking
for a service that does not exist. It was active the whole time.
Do not confuse it with the *agent* `hotline-ios`, which is a Claude session and
is separately still down since the 29th.

**Leak, found and not touched:** `/health` shows `active_calls: 3` — that ring
(`answered=0`) plus two `say` conversations open since 26 and 27 Aug. Stale
conversations are not reaped. `ring_ready` is still true and nothing is ringing.

Two fixes offered to him and deliberately **not built** — split the error
string, and reap stale conversations. He asked for a test, not a change.

## 2026-09-01 01:15 — data-af wedged by a cyber-classifier refusal; hotline-call false-fail fixed

Two verified instructions from him: approval of the two hotline-call fixes, and
*"First also check up on data af"*.

### data-af is WEDGED — do not read its `waiting` as healthy

Its `wd_gen` task (a password/username wordlist generator) tripped **Opus 5's
`cyber` safety classifier** at 22:54:02. The harness did a
`model_refusal_fallback` to Opus 4.8 and retracted the task message; the session
then produced no assistant turn and **stopped consuming its input queue**. Two of
his *"Hows it going"* messages (22:58, 23:13) were enqueued but never delivered
(first one hit the 900 s ReplyTimeout, *"the session was idle"*).

- It is alive (pid 1869) but idle in `epoll_wait`, 8 s CPU in 25 min, no tool
  ever called. Nothing lost — 5% context, only the refused task + two pings.
- It runs in tmux `hl-agent-34e60b` on **pts/1, a pane he is attached to** — do
  not kill it from under him without his word.
- **The task re-trips the Opus-5 classifier every run**, falling back to 4.8. A
  plain restart can wedge the same way. Remedy is his call: restart on 4.8 /
  restart as-is / leave down. **Do not reword his task to evade the classifier**
  — that is a guardrail, not a dead end.
- How to read a wedged session: its transcript has a `system` row with
  `apiRefusalCategory` and `retractedMessageUuids`, and `queue-operation`
  `enqueue` rows with no matching `dequeue`. `hotline --list` still says
  `waiting`.

### hotline-call now tells a ring-out from a dead daemon (`aa414c7`)

The 01:02 bug: `client._post` raised one *"cannot reach hotline-iosd"* for both a
read timeout and a connect failure. Now it opens a fresh socket at the timeout
and raises `CallTimeout(daemon_up=…)`; the CLI renders daemon-up as *"no answer
on the call"* + `EXIT_UNANSWERED`, daemon-down as the original undeliverable.
`CallTimeout` subclasses `DaemonError` so fallbacks are unchanged. 216 tests
green, mypy+ruff clean, pushed. Client-side — no daemon restart. Not exercised
end-to-end (a real read-timeout means actually ringing him).

### The active_calls leak is NOT a free fix

`reap()` keeps unanswered conversations on purpose (docstring: *"an automatic
retention policy is exactly what §3 decided against"*). Don't auto-close them.
Offered him the narrow safe half — close one-way `say` notes on post — and left
the rest to §3. Awaiting his word.

**Open, awaiting him:** data-af remedy (1/2/3); whether to close `say` notes.

## 2026-09-01 01:35 — data-af restarted on Opus 4.8; calls now close on unanswered

His two verified instructions (`23:27:45Z`): restart data-af directly on Opus
4.8, and close conversations when they go unanswered ("just say they did go
unanswered").

### data-af is now a fresh Opus-4.8 session — how it was done

`tmuxen.spawn` has no `--model`, so this was manual: kill the wedged session,
then `claude --model claude-opus-4-8 --permission-mode bypassPermissions --name
data-af` in a systemd-scoped tmux (`hl-data-af`), seeded with a prompt that runs
`hotline --adopt data-af` first and then carries his task. Verified on 4.8 by
`/proc/<pid>/cmdline`, adopt confirmed via `hotline --list`, and it is past the
classifier and building. **If it wedges again, this is the pattern to repeat.**
Do not reword his task to dodge the classifier — 4.8 does not trip it.

### hotline-call / daemon: calls close on unanswered (`868c298`)

Both unanswered paths append `state="unanswered"` and `_close_conversation()`.
The SPEC-3 keep-open rule was overridden by him — and it was safe to, because
`reply()` does not gate on `closed`: **a late answer still lands after close.**
Close only drops it from `active_calls`/waiting. A `wait:false` ring stays open
(genuinely pending, not unanswered). The 3 pre-existing stragglers were closed
with the daemon stopped (DB backed up first); `active_calls` is now 0.

Combined with `aa414c7` (the earlier false-"dead daemon" fix), the call path is
in good shape. 217 tests green. Neither call fix was exercised by a real ring —
that means actually ringing him — so unit tests + live `/health` are the
evidence.

### Open — all his, nothing mine

1. data-af will ask public-vs-private before pushing `wd_gen`; that answer is
   his.
2. `~/data/llama-turbo3` keep/delete (670 MB, load-bearing for 262k).
3. The three frozen files + the acceptance test (since the 26th).
4. The `CLAUDE.md` snapshot line (since the 28th).

DB backup left at `~/.local/state/hotline/hotline-ios.db.bak.20260901-013426` —
safe to delete once he's happy the close-on-unanswered change is behaving.

## 2026-09-01 02:05 — AskUserQuestion bridge (headless agents no longer hang on the picker)

data-af wedged a second time (`23:49:31Z`), this time on **AskUserQuestion** —
it asked public-vs-private, the interactive picker opened, and his injected
redirect landed *inside* the menu. Immediate unstick: `Escape` to the pane; it
took his CTF/OSINT redirect and reworked wd_gen.

**The build he asked for (`aedcfb5`):** a PreToolUse hook on `AskUserQuestion`
(`src/hotline/ask.py` → `~/.claude/hooks/hotline-ask.py`). It fires before the
picker renders, posts the question + options to `#agent-<name>`, waits for his
reply, and returns a `deny` whose reason carries his answer verbatim — the model
reads it as the tool result and proceeds. No menu, no hang, **no keystrokes**:
his free text goes back whole and the model maps it to the option. That is why
it beats the keystroke approach he suggested — a redirect (not "pick 2") carries
through intact.

- **Gate:** `HOTLINE_SPAWNED`. Fires only for headless agents with a channel;
  his own keyboard sessions get the normal picker. `tmuxen.spawn` sets it, and
  `hotline-run` now sets it too (operator covered on next respawn).
- **Proven live:** minutes after install, data-af (already running) called
  AskUserQuestion and the hook caught it — Claude Code re-reads hooks per call,
  so no restart was needed. Question relayed to #agent-data-af, no picker.
- **His answer must land in the ASKING agent's channel** (#agent-data-af), not
  the operator's — the hook watches that channel via `replies_since`.
- On no reply in 20 min: denies with "take the safest reversible option and
  continue," not a hang. Tunable via `HOTLINE_ASK_TIMEOUT`.
- 490 tests green, mypy+ruff clean. Installed via `hotline --install-hook`.
  Settings backed up `~/.claude/settings.json.bak.20260901-020126`.

### Open — his

1. **Waiting on him in #agent-data-af:** public or private for the wd_gen repo.
   data-af is blocked on it through the bridge.
2. llama-turbo3 keep/delete; the three frozen files + acceptance test; the
   CLAUDE.md snapshot line — all unchanged.

## SHUTDOWN 2026-09-01 03:25 — state at power-off (operator `hotline-80`, session `f63b1d6e`→ new)

He confirmed the shutdown over Discord (`kind=human`, verified `03:24:53Z`,
*"Yep do it"*) after I declined to act on the phone-app *"Shutdown now"* alone —
that channel authenticates a key-holder, not him, and a poweroff is not undoable
from here. The confirmation also carried the top task now at the top of this file:
**make phone-app messages verifiable.**

**Checked what a shutdown would destroy, not assumed:**
- Only two live sessions: the operator and `data-af` (idle, work done + pushed).
- `data-af`'s `wd_gen` is committed and **pushed** — local HEAD `bda6180` equals
  `origin/main`. Its pane has an unsubmitted `add hashcat-rule export` line, which
  is a leftover idea, not running work.
- Nothing armed: no `/run/systemd/shutdown`, no systemd jobs, no watch-agent, no
  `at`, no crontab.
- ollama idle, no model resident, GPU 2 MiB — nothing mid-inference.
- No mail queued (msmtp is send-only, no spool).
- Recoverable: `wakeonlan a8:a1:59:fd:4d:13` (`enp4s0` UP, `Wake-on: g`).

### What this long session actually did

1. Boot sweep after the evening power-off; nothing stranded, nothing armed.
2. Test call at his request — **and found `hotline-call` reports a successful
   ring as a dead daemon**; fixed it (`aa414c7`): a read timeout now probes the
   socket and reads as "no answer on the call", not "cannot reach".
3. **Close calls on unanswered** at his instruction (`868c298`), overriding the
   SPEC-3 keep-open rule (safe, because `reply()` does not gate on `closed`).
4. `data-af` wedged twice — first by an **Opus-5 `cyber` classifier** refusal
   (restarted it directly on Opus 4.8 at his instruction), then on an
   **AskUserQuestion picker**.
5. **Built the AskUserQuestion→Discord bridge** (`923760e`) — the picker no
   longer hangs a headless agent; it relays to `#agent-<name>` and feeds his
   reply back as the tool result. Proven live: `data-af` asked public-vs-private
   through it, he answered "public", and it pushed the public repo.

### Open — all his

1. **⭐ Make phone-app messages verifiable** — the top task, design at the top.
2. `~/data/llama-turbo3` keep/delete (670 MB, load-bearing for 262k).
3. The three frozen files + acceptance test (since the 26th).
4. The `CLAUDE.md` snapshot line (since the 28th).

Going down.
