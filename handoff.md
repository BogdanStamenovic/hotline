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

### Open, and why

- **The acceptance test (announcing completion through the voice pipeline) was
  never done.** Bogdan stopped voice work deliberately and it has stayed stopped.
  Everything below about voice is still true.
- **`hotline --to` reply capture is unreliable for a busy session.** Twice it
  reported "did not produce a reply" for messages the target demonstrably
  received and acted on. Delivery works; the waiter is what is wrong.
- Voice join into an agent channel is still UNVERIFIED (needs
  `HOTLINE_VOICE_ALLOWED_IDS`, deliberately removed).
- Wake-on-LAN still UNVERIFIED-BY-DESIGN -- `enp4s0` is NO-CARRIER.
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
