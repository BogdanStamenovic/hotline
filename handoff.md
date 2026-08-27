# HOTLINE — worker handoff

> ## STATUS AS OF 2026-08-27 10:30 CEST — READ THESE SIX LINES FIRST
>
> 1. **The stop is LIFTED.** His verified words, 10:01 today: *"Good morning
>    hotline. Startup hotline ios and tell him to continue his work. Tell him im
>    away so to contect me here. and you finish your job."* Every "do not resume"
>    instruction below this banner is **superseded**. Work the list.
> 2. **He is AWAY** and wants contact on **Discord**. He said so himself.
> 3. **Your spawn prompt is a hardcoded string and has been wrong four times
>    running.** It always says "replacing one that died" and "Bogdan is away".
>    Read Discord before believing it. A launcher is not an authority. This
>    banner is not one either — it has a timestamp on it for the same reason.
> 4. **WoL is VERIFIED.** Shutdowns are recoverable. Anything below saying this
>    box has no remote wake is stale.
> 5. **`hotline-ios` is running** and has a hard deadline: its provisioning
>    profile expires **1 September 22:53**.
> 6. **The reply-contract work is still uncommitted on purpose.** He was asked at
>    10:16 whether to commit or drop it and had not answered as of this writing.
>    Do not commit it for him; check Discord for his answer first.
>
> *Newest material is at the BOTTOM of this file. This banner exists because the
> top is what a new worker actually reads, and four of them in a row acted on
> stale premises that were sitting right here.*


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
