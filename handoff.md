# HOTLINE — worker handoff

You are the worker on the **hotline** build. Bogdan is AWAY. Read this file, then
read `PLAN.md` in this directory (the full architecture), then continue from
"CURRENT STATE" below. Append to `PROGRESS.md` as you go.

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

Started 2026-08-25 evening on his verified instruction. `/home/bodas/data/hotline-ios`,
spec at `SPEC.md`, agent `hotline-ios` (own channel `#agent-hotline-ios`).

**Goal:** a real ringing call on his iPhone, replacing the `@mention` he calls a
fake call. He ruled out the $99 Apple Developer Program — "just do whatever is
free" — so there are two free paths and they compose:

- **C (recommended, actionable now):** stock App Store Linphone registers to a
  self-hosted SIP domain on archserver over Tailscale. Its REGISTER carries RFC
  8599 push params; our server calls Belledonne's free `/api/push_notification`;
  their gateway holds the real APNs cert and wakes the closed app into CallKit.
  **I verified the server half at source** — the route sits behind ordinary
  account middleware with no admin tier, and the controller pushes to whatever
  `pn_prid` it is handed with no ownership check. **The client half is unverified**
  and is what his Linphone test settles.
- **B (the upgrade):** his own sideloaded app, kept alive by a silent
  `AVAudioSession`, holding a socket over Tailscale, ringing via a LOCAL
  `CXProvider.reportNewIncomingCall`. No APNs at all. CallKit needs no
  entitlement; `UIBackgroundModes` is an Info.plist key, not an entitlement.
  Dies at the app layer: reboot (a sideloaded app cannot self-start), a routine
  incoming phone call killing the audio session, force-quit, 7-day cert expiry.

**A SECOND, INDEPENDENT DOORBELL EXISTS: Telegram.** `data-89` first reported a
nine-app sweep concluding nothing already on his phone can be made to ring, and I
filed that with a "do not re-run this research" line. **Both were wrong** — it had
filed a partial fork's result before the parent agent reported. Struck, and this
is the corrected version.

**Telegram 1:1 calling is real, free, headless and released.** Verified by me
directly rather than relayed: `RequestCallRequest` is present in released
**Telethon 1.44.0** from PyPI, with `AcceptCallRequest` and `DiscardCallRequest`
alongside it and parameters matching MTProto's `phone.requestCall`
(`user_id, g_a_hash, protocol, video, random_id`). That was the open question —
release or unmerged branch — and it is released. `bbimer/tg-alarm-sentinel`
(pushed 2026-08-08) exists for exactly this use case: triggering the native iOS
incoming-call screen to wake a sleeping user.

- **VERIFIED: it rings.** Telegram-iOS registers `PKPushRegistry` for `.voIP` and
  reports to `CXProvider`; the ring fires on the bare call request without
  completing key exchange.
- **UNPROVEN: it carries audio.** 1:1 media needs a key exchange the live tests
  did not complete. Treat "Telegram rings the phone" as fact and "Telegram carries
  the conversation" as unproven.
- **Costs:** needs a real Telegram account with a phone number to ring *from* —
  bot tokens cannot call `phone.requestCall` — so he would need a second account.
  Telegram's anti-abuse flood limits on automated calling are an unknown. And it
  presumes Telegram is on his phone, **which nobody has asked him.**

**Why this is worth having even if C ships:** it attacks C's single biggest
durability risk. C depends on Belledonne continuing to relay pushes through an
endpoint with no ownership check — a silent-failure mode outside our control. A
Telegram ring depends on none of that. **The two failure modes are uncorrelated**,
which is worth more than either doorbell alone.

C remains primary because it is the one that carries a real conversation rather
than only a ring. But "C by elimination" is **not** true and should not be said.

Also verified from the same sweep, both genuine but both downgrades: Home
Assistant critical alerts really do bypass DND and silent, triggerable by `curl`
over Tailscale, free and self-hosted — but need the HA app installed and are an
alert, not a call. iOS PWA web push needs no App Store and no Apple Developer
account at all (standard VAPID) — but WebKit says it behaves as an ordinary
notification and respects Focus, so it buzzes rather than rings.

Still genuinely dead, so do not re-check these: Signal (`signal-cli` is text-only),
Messenger, Viber, Discord (bots join guild voice; nothing rings a DM), Zoom (paid
licence), Google Meet (the API makes spaces, it does not ring), FaceTime (CallKit
can join the native UI, not invoke FaceTime), Skype (retired 2025-05-05).
WhatsApp's Business Calling API rings but needs Meta business verification, a WABA
number and the callee's prior opt-in — not a cold-call API.

**Every rung below the ring is an alert, not a call**, and a critical alert is a
louder fake call — which is the thing he asked to be rid of. When the system
degrades it must say which rung it landed on rather than quietly substituting a
notification and letting it read as success.

**`ConfirmedRing` arbitrates between them** and is the piece that matters most: a
transport must produce positive evidence it rang — SIP 180, push accept, app ack —
or the silence becomes `CallUnreachable` and degrades loudly to the pager. It
**fails closed**: no confirmation channel means reported-unreachable, not trusted.

**Do not "fix" this by sideloading Linphone.** He suggested it and it is the one
idea that specifically cannot work: stock Linphone is valuable for its IDENTITY,
not its code. `pn-param` is `<TEAMID>.org.linphone.phone.voip` — their team, their
bundle, their cert. Re-signing it breaks all three legs at once and free
provisioning could not obtain a push token anyway. Sideloading it turns C into B
minus B's advantages.

**Toolchain, verified by running it:** Swift 6.3.3 compiles and executes on this
Arch box from a private toolchain under `/mnt/iosbuild`, nothing installed
system-wide. Only Apple's SDK is missing, and **that is an account problem, not a
machine problem.** The route is a GitHub Actions macOS runner (Xcode preinstalled,
so no Apple ID and no 13GB `Xcode.xip`) in a **throwaway public repo he explicitly
authorised** — two files only, delete it once the artifact is out.

`/mnt/windows/hotline-ios-build.img` is an ext4 image, loop-mounted at
`/mnt/iosbuild`. It is a FILE. His Windows is untouched. Do not delete
`/mnt/windows/pacman-cache-archive-20260825` — the moved package cache and this
box's only rollback.

**Blocked only on him, and only until he is home on wifi** (his cellular is bad,
which is also why everything measured relayed): install Linphone and point it at
the SIP probe on `100.72.2.62:5060`, and leave the phone unlocked on home wifi for
thirty seconds so the direct-path measurement can run. One sitting, both answers.

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

- **Everything on `hotline-ios` now waits on him being home on wifi** — the
  Linphone push-token test and the direct-path measurement, one sitting. Nothing
  else is blocked; the SDK build and the server side continue meanwhile.
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

- Plug an ethernet cable into `enp4s0` (it is NO-CARRIER; nothing can wake it).
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
  PROGRESS.md and stop. Tell him everything works **using the emulated call** —
  i.e. drive the finished voice path end to end and have it speak the completion
  message to him. Either transport counts (Discord voice, or the iPhone Shortcut
  path), but it must be the real pipeline, not a synthetic wav or a text message.

  This is the acceptance test, not a victory lap: if the system cannot announce
  its own completion through itself, it is not complete. If it fails, that failure
  IS the result — log it honestly and tell him over Discord instead.
