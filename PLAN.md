# Hotline — build plan

Voice-call a Claude Code session from an iPhone; let Claude page back.
Written 2026-08-24. **Nothing here is built.** This is the design for approval.

Decisions already taken by Bogdan this session:
- **No money.** "Agent rings me" becomes escalating Discord `@mention` push, not a PSTN call.
- **iPhone.** Rules out self-hosted SIP/Matrix ringing (needs PushKit + a vendor's APNs certs).
- **Full bypass, locked to my Discord ID.**

---

## 1. What recon found that changes the design

Eight agents swept both machines, the 25 docs in `~/research`, and the Hermes clone.
Five findings reshaped the plan:

### 1.1 There is already a working iPhone voice loop

`pigion.service` — "Pigion voice-first todo server" — has been running 3 weeks and is
*actively serving* `POST /api/v1/voice` from the iPhone at `100.108.255.28`.

`~/pigion-todo/iphone/SHORTCUT.md` describes the loop, and it is a complete
conversation:

```
Siri "Todo" → Dictate Text (on-device) → POST {text, session_id, client}
            → Get Dictionary Value "response" → Speak Text → Repeat 999×
```

Apple Dictation does STT on-device. Apple TTS speaks the reply. Tailscale carries
the text. **Zero audio leaves the phone, zero GPU is involved, and it already works.**

This is a proven transport that can be pointed at Claude in an afternoon. It is
half-duplex and cannot be initiated remotely — but as a *first* milestone it is
unbeatable value.

### 1.2 Live Claude sessions expose a local IPC socket

Undocumented, found by `strings` on the 343MB binary, then **verified live**:

```
~/.claude/sessions/<pid>.json          → {sessionId, cwd, messagingSocketPath, name, ...}
~/.claude/sessions/<pid>.<64hex>.key   → 0600 auth token
/run/user/1000/cc-socks/<pid>.sock     → AF_UNIX, newline-delimited JSON
```

The binary self-documents its own wire protocol:

```bash
{ echo '{"type":"auth","token":"'"$CLAUDE_CODE_MESSAGING_TOKEN"'"}'
  echo '{"type":"user","message":{"role":"user","content":"hello"}}'
} | socat - UNIX-CONNECT:/run/user/1000/cc-socks/<pid>.sock
```

Confirmed right now on this box — two live sessions, `data-d6` (pid 5596) and
`data-13` (pid 35248, this one), both with sockets and key files present, and
`claude agents --json` enumerates them.

**This is the answer to "connect to the current session."** No tmux `send-keys`
fragility, no screen-scraping a TUI. An external process reads the descriptor,
reads the key, opens the socket, writes JSON.

Caveat: it is **inject-only**. There is no synchronous reply on that socket.

### 1.3 The `Stop` hook is the reply channel

Claude Code fires a `Stop` hook "when Claude stops". Registered in `settings.json`:

```json
{"hooks":{"Stop":[{"matcher":"","hooks":[{"type":"command","command":"...","timeout":30}]}]}}
```

It receives `{"session_id": "..."}` on stdin. That is the structural
turn-complete signal — strictly better than tailing JSONL and looking for
`stop_reason:"end_turn"`, and far better than parsing a redrawing TUI.

**Inject over the socket, get the reply back via the Stop hook.** That pair is
the whole mechanism.

### 1.4 Requirement (b) is already satisfied

`/etc/sudoers.d/10-wheel-nopasswd` grants `%wheel ALL=(ALL:ALL) NOPASSWD: ALL`.
`bodas` is in `wheel`. `/usr/local/bin/desktop` only ever calls
`systemctl start gdm`, `systemctl stop gdm`, `systemctl isolate multi-user.target`.

**`desktop on` already runs without a password.** No sudoers work is needed.
And "make my user passwordless" is the wrong tool for what you were reaching for —
see §6.

### 1.5 The wake path does not exist yet

- `enp4s0`: still `NO-CARRIER`. **The cable is not plugged in.**
- WoL: `Wake-on: d` — the arming from 02:45 did not survive the reboot, as predicted.
- Both machines are on **wifi**, same `/24` (pigion `.8`, archserver `.9`), same
  gateway. Same L2 domain, so a broadcast magic packet will reach — once cabled.
- WoWLAN: the chip supports magic-packet wake, but it is **runtime-only state**
  with no persistent config surface in either iwd or NetworkManager, and it is
  an S3-only mechanism — a powered-off box has no radio. Not a foundation.

Everything else can be built and tested with the box awake. The wake layer is
gated on two physical tasks (§8).

---

## 2. Architecture

```
                        ┌──────────── iPhone ────────────┐
                        │  Discord app  │  Shortcuts app │
                        └───────┬───────┴────────┬───────┘
                     voice channel          POST /voice
                                │                │
                    ┌───────────▼────────────────▼──────────┐
   always on        │   PIGION  (Pi Zero 2 W, 237MiB free)  │
   36d uptime       │   • sentinel bot: raw WS gateway,     │
                    │     watches VOICE_STATE_UPDATE only   │
                    │   • WoL magic packet  → archserver    │
                    │   • /api/v1/claude → forwards to bridge│
                    │   NEVER thinks. Receives, wakes, routes│
                    └───────────────────┬───────────────────┘
                                        │ Tailscale
                    ┌───────────────────▼───────────────────┐
                    │   ARCHSERVER (4060 8GiB, the muscle)  │
                    │                                       │
                    │  ┌── transport adapters ────────────┐ │
                    │  │ discord-voice │ discord-text │   │ │
                    │  │ http (shortcut)                  │ │
                    │  └──────────────┬───────────────────┘ │
                    │   48k stereo PCM ↕ text                │
                    │  ┌──────────────▼───────────────────┐ │
                    │  │ audio core (only for voice path) │ │
                    │  │  silero VAD → utterance segment  │ │
                    │  │  → faster-whisper (GPU)          │ │
                    │  │  ← Piper/Kokoro ← barge-in stop  │ │
                    │  └──────────────┬───────────────────┘ │
                    │  ┌──────────────▼───────────────────┐ │
                    │  │ SESSION ROUTER                   │ │
                    │  │  new   → claude --input-format   │ │
                    │  │          stream-json (persistent)│ │
                    │  │  join  → cc-socks UDS inject     │ │
                    │  │          + Stop hook reply       │ │
                    │  │  agent → named systemd session   │ │
                    │  └──────────────────────────────────┘ │
                    └───────────────────────────────────────┘
```

**The design rule:** one router, many transports. Everything above the router
speaks text. Everything below the audio core speaks 48kHz PCM. Adding a
transport later (SIP, a paid PSTN number, a web page) is a new adapter, not a
rewrite.

### Why Pigion never runs the voice bot

237MiB free, 107MiB of zram already in use. A full `discord.py` client with
default caching is 60-150MB RSS. A hand-rolled websocket client doing only
IDENTIFY + HEARTBEAT + `VOICE_STATE_UPDATE`, no caching, is ~25-45MB.
Pigion gets the second one. `python3-websockets 15.0.1` is in trixie
(pip is PEP668-managed there — apt or a venv, following the `/opt/pigion/.venv`
precedent).

### Why two bot tokens

One token connected from two processes causes gateway identify/session conflicts.
Two separate Discord applications: `hotline-sentinel` (Pigion) and `hotline`
(archserver). Both in the same private guild. Free.

---

## 3. The session router — the piece that must be right

Three routing modes, chosen by what you say at the start of a call.

| Say | Mode | Mechanism |
|---|---|---|
| *(default)* / "new session" | fresh | persistent `claude` subprocess |
| "join data-13" / "what are you working on" | attach | cc-socks inject + Stop hook |
| "ask watchdog" | named agent | same UDS, systemd-supervised session |

### Fresh session
```
claude --input-format stream-json --output-format stream-json --verbose \
       --permission-mode bypassPermissions
```
One long-lived process, fed one JSON object per line on stdin. Genuinely
multi-turn over a single pipe — no respawn per turn, so context persists across
the whole call. Verified accepted:
`{"type":"user","message":{"role":"user","content":"hi"}}`

### Attach to a live session
1. Enumerate `~/.claude/sessions/*.json` → pid, sessionId, socket path, name.
2. Read the sibling `.key` file for the token.
3. Connect AF_UNIX, send `auth` then `user`.
4. Reply arrives via the Stop hook (installed once in `~/.claude/settings.json`),
   which POSTs `{session_id}` to the bridge; the bridge reads that session's
   transcript tail and speaks the final assistant text.

Note: sessions have derived names (`data-d6`, `data-13`) — awkward to say aloud.
The router should also accept "the one in uxonews" and ordinals ("the older one").

### Named standing agents
`claude-agent@.service` template, `Type=oneshot` + `RemainAfterExit=yes` +
explicit `ExecStop`, wrapping `tmux new-session -d` — because `claude` needs a
real PTY and a systemd unit's stdio is a pipe. `Type=forking` is wrong here:
`tmux new-session -d` hands off to the self-daemonizing server and exits, which
does not satisfy forking's PID-tracking contract.

Claude's own background daemon self-terminates after ~5s idle
(`~/.claude/daemon.log`: "idle 5s with no clients — exiting"), so persistence
must come from systemd, not from `claude --bg` alone.

---

## 4. Dead air is the real UX problem — and it has a good answer

A tool call can run 30+ seconds. The voice research flagged "what should the
caller hear during a 30s wait" as **genuinely unsolved** — every published
voice-agent pattern assumes a few seconds, and suggests hold music.

We can do better, for free. `--output-format stream-json` already emits
`tool_use` events on the wire. So instead of hold music:

> "Let me look… reading the nginx config… running the test suite… okay, three
> failures, all in the auth module."

Narrate the tool calls as they happen. It turns dead air into presence, needs no
new component, and it is the thing that will make this feel like talking to
someone rather than querying a machine. **This is the feature to get right.**

Fallback for genuinely silent stretches: a soft ambient loop plus a short
backchannel every ~8-10s.

---

## 5. Voice stack (Discord path only — the Shortcut path needs none of this)

Settled by the three existing docs in `~/research`; not re-researched.

| Stage | Choice | Cost |
|---|---|---|
| VAD / endpoint | silero-vad, ~600-800ms silence | CPU, negligible |
| Resample | `python-soxr` streaming, 48k stereo ↔ 16k mono | CPU |
| STT | faster-whisper `distil-large-v3` int8_float16 | ~1.5GB VRAM |
| TTS | Piper (fastest TTFA) → Kokoro-82M if quality matters | ~0-400MB VRAM |
| Discord voice | **py-cord** `VoiceClient.start_recording` + custom `Sink` | — |

~2GB of 8GB. Currently 609MB in use with GNOME up. Comfortable.

**Python version — install this in a `uv`-managed Python 3.12 venv, not system
3.14.** `discord.py[voice]` pins `PyNaCl<1.6` which has no 3.14 wheel;
`kokoro`/`kokoro-onnx` are hard-blocked below 3.13. CTranslate2 wants CUDA 12
while Arch ships 13.3. Pinning 3.12 in the venv makes an entire class of
blockers vanish for free. `uv` is already installed.

**py-cord over discord.py**: its voice extra already requires `PyNaCl>=1.6.2` and
bundles `davey` for Discord's now-mandatory DAVE E2EE. discord.py needs a manual
override. Node's `@discordjs/voice` had a month-long DAVE-related receive outage
in early 2026 with no clear changelog proving the fix shipped — avoid.

**Prior art:** `~/.hermes/hermes-agent/plugins/platforms/discord/adapter.py` has a
working `VoiceReceiver` (~350 lines) doing RTP/NaCl/DAVE/Opus receive and
silence-based segmentation, MIT licensed. Keep it as the reference implementation
if py-cord's sink misbehaves. Do **not** adopt Hermes wholesale — 1MB `cli.py`,
96KB `AGENTS.md`, 35 provider plugins, and no way to point its model layer at a
local subprocess. That is a black box by any measure.

---

## 6. "Make my user passwordless" — the wrong tool

Three different things get called this:

- **`passwd -d bodas` / PAM `nullok`** — deletes the account password. An
  *authentication* bypass affecting every PAM consumer, with SSH reachable over
  Tailscale. **Don't.**
- **NOPASSWD sudo** — doesn't touch login at all, only the second prompt.
  **Already in place**, blanket, for all of `wheel`.
- **TTY autologin** — removes the console login prompt only. Doesn't make
  services start (that's lingering / system units) and doesn't help `desktop on`
  (that's sudo). **Not needed.**

What you actually wanted — "services run at boot with nobody logged in, and
Claude can bring up the desktop" — is already solved by the existing NOPASSWD
plus a system unit. Nothing to change.

One thing to raise, not to act on: `%wheel NOPASSWD: ALL` means a voice session
running `bypassPermissions` can `sudo` anything, so **voice is root-equivalent**.
Narrowing that to a scoped `Cmnd_Alias` is a real security decision and yours to
make. Note that a narrow rule added *alongside* the blanket one is decorative —
sudoers rules OR together, they don't intersect to a minimum.

---

## 7. Security — the one thing that genuinely worries me

You chose full bypass gated on your Discord ID. Building it that way. Two notes:

**1. Gate at the audio sink, on user ID — not just at the guild.** py-cord's sink
gives per-user streams. If the bridge transcribes whatever it hears rather than
filtering by your user ID first, anyone who joins that voice channel can speak
commands into a root-equivalent shell. This is cheap to get right and expensive
to get wrong. Same for text: user ID **and** guild ID **and** channel ID.

**2. Mis-transcription has no undo.** With full bypass there is no confirmation
step, and Whisper will occasionally hear something other than what you said. The
cheap mitigation is a `PreToolUse` hook denying a short list of catastrophic
shell patterns (`rm -rf /`, `mkfs`, `dd of=/dev/`, `> /dev/sd*`), which costs one
file and can't be talked around by a bad prompt. Say the word and I'll include
it; say no and I'll leave it out.

Also: Discord 2FA on your account is now load-bearing infrastructure.

---

## 8. Blocked on you — physical or account tasks

1. **Plug the ethernet cable into `enp4s0`.** Nothing about WoL works until this
   happens. The OS-side config will self-arm once carrier appears.
2. **Two BIOS settings** (ASRock B550M-HVS SE, no IPMI, needs physical presence):
   disable **ErP / ErP Ready**, enable **PCIE Devices Power On / PME Event Wake Up**.
   Without these, WoL from a full poweroff won't work no matter what the OS says.
3. **Create two Discord applications** at discord.com/developers — `hotline` and
   `hotline-sentinel`. Enable the `voice_states` intent (not privileged) on both;
   `message_content` (privileged, one toggle) on `hotline`. Give me the tokens.
   ~5 minutes.
4. **Confirm you ran `/login`** — the CLI went from unauthenticated at 02:45 to
   `loggedIn: true, subscriptionType: max` by 16:37. It wasn't me. Worth knowing
   it was you.

---

## 9. Build order

Each phase ends at something demonstrable. Nothing proceeds past Phase 0
without a snapshot.

### Phase 0 — reversibility *(~15 min, do first)*
Timeshift is installed with **zero snapshots** and unconfigured. There is no
filesystem rollback on this box today. Configure RSYNC mode → `/dev/nvme0n1p4`,
take snapshot zero. Then system config changes stop being an hour of repair.

### Phase 1 — the router, headless *(the load-bearing piece)*
No audio, no Discord. A CLI: text in, text out.
- cc-socks client (discover / auth / inject)
- Stop-hook reply capture
- persistent `stream-json` subprocess driver
- three routing modes
- **Milestone:** `hotline "what's in ~/data"` starts a fresh session and answers;
  `hotline --to data-13 "..."` injects into a session already running in front of
  you and speaks its reply back.

### Phase 2 — the 2-hour win: iPhone Shortcut → Claude
Reuses the *proven* pattern. A `/api/v1/claude` endpoint (on Pigion, or a small
service beside it) forwarding to the Phase 1 router. A new Shortcut, same recipe,
different URL.
- **Milestone:** "Hey Siri, Hotline" → you talk → Claude answers aloud, in a loop.
  On-device dictation, no GPU, no Discord, no new models.

### Phase 3 — Discord text bridge + the page-me path
- `hotline` bot, DM + guild text, hard-gated on your IDs
- escalating `@mention` ladder → APNs push on your phone
- a `call-bogdan` skill any session can invoke, falling back to
  `wake-bogdan.sh`'s siren if you're at the machine
- **Milestone:** DM the bot and get a real answer; a blocked agent pages your
  lock screen. **This is where "Claude calls me" starts working.**

### Phase 4 — Discord voice, the actual call
py-cord sink → VAD → whisper → router → TTS → playback, with tool-call narration
and barge-in.
- **Milestone:** tap into the voice channel, talk, be talked back to.

### Phase 5 — Pigion front door + wake
Sentinel bot, magic packet, boot units (`loginctl enable-linger bodas`, system
unit with `User=bodas`, `After=network-online.target tailscaled.service`,
`Wants=` not `Requires=`), NM `wake-on-lan magic` + a udev rule as belt and braces.
- **Milestone (REDEFINED 2026-08-27, by him — see below):** archserver powered
  off. Wake it and send it a message from the phone app. It wakes, an agent
  answers you in the app. **That is the whole thing working.**

> **Why this changed, so nobody restores the old one.** This milestone used to be
> *"join the voice channel... you talk"*, and the acceptance test in `handoff.md`
> matched it: the system had to announce its own completion through the voice
> pipeline. He then decoupled the architecture and called the voice route
> *"basically a gimmick"*, freezing `voice.py`, `audio.py`, Whisper and Piper —
> kept on disk, not invested in further.
>
> Nobody walked that premise change, so for several days the project's own
> definition of *finished* was a live test of the one subsystem he had scrapped,
> and it sat open waiting for him to join a channel he had no reason to join.
>
> Asked to choose between running the old test as written (voice still works;
> it costs two minutes and touches no code) and redefining the milestone around
> the text path he actually uses, **he chose to redefine it.** Verified against
> Discord: message `1542452470528090182`, 2026-08-27T08:35:26Z, *"B."*
>
> The voice code stays. Only the finish line moved, and it moved because he said
> so rather than because it was easier to reach.

### Sleep policy
Default to **S5 poweroff**, not suspend. `nvidia-suspend/resume/hibernate`
services all exist and are all **disabled** while the nvidia modules are loaded —
suspending in that state is a known black-screen-on-resume failure mode. S3 is
faster to wake and often more WoL-reliable, but only after enabling those hooks
and testing one real cycle with the GPU loaded. Not on the critical path.

---

## 10. What I am *not* proposing

- **A real ring.** On iPhone, only a PSTN call rings unconditionally, and you
  said no money. `@mention` spam is the honest free substitute: it pushes to your
  lock screen, it repeats, it does not ring.
- **SIP / Matrix / Mumble.** iOS backgrounding kills self-hosted ringing without
  PushKit and a vendor's APNs certs. Matrix call ringing has open upstream bugs.
  Not worth the build.
- **Hermes as the gateway.** Borrow its `VoiceReceiver` if needed; don't inherit
  its scope.
- **WoWLAN as the wake path.** Runtime-only state, no persistence surface, S3-only.
  The cable is the answer.
