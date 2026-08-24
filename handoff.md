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
- [ ] **Phase 3 — Discord text bridge  <-- START HERE** + escalating `@mention` page ladder +
      `call-bogdan` skill.
- [ ] Phase 4 — Discord voice (py-cord sink -> silero VAD -> faster-whisper ->
      router -> Piper), with tool-call narration and barge-in.
- [ ] Phase 5 — Pigion sentinel bot + magic packet + boot units on archserver
      (`loginctl enable-linger bodas`, system unit `User=bodas`,
      `After=network-online.target tailscaled.service`, `Wants=` not `Requires=`)
      + NM `wake-on-lan magic` + udev rule.

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
