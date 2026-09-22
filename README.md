# hotline

Talk to Claude Code from a phone, and let Claude talk back. `hotline` is the
router underneath that: one entry point that can start a fresh Claude session,
or reach into a session already running in a terminal in front of you, and return
its answer as plain text. Every transport above it -- an iPhone Shortcut, a
Discord text bridge, a Discord voice call -- is an adapter over the same router.

## Install

```
ownbox install hotline
```

Manual:

```
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/hotline --install-hook
```

The `--install-hook` step matters for `--to`: see *How replies come back*.

## Usage

| Option | Meaning |
|---|---|
| `--to SESSION` | inject into a live session instead of starting a fresh one |
| `--cwd DIR` | working directory for a fresh session |
| `--list` | list live sessions (newest first) and exit |
| `--install-hook` | install the `Stop` hook that makes `--to` able to hear replies |
| `--timeout SEC` | give up after SEC (default 300) |
| `--no-bypass` | do not pass `--permission-mode bypassPermissions` to a fresh session |
| `-v/--verbose` | narrate tool calls to stderr as they happen |
| `-q/--quiet` | suppress non-error output |

Only Claude's answer goes to stdout. Narration, progress and errors go to stderr,
so `$(hotline ...)` gets the answer and nothing else.

Exit codes: `0` success, `1` the operation failed, `2` usage error or aborted.

### Examples

```
hotline "what's in ~/data"
hotline --to data-13 "what are you working on?"
hotline --to newest -v "run the tests and tell me what fails"
hotline "join uxonews, is the build green?"
hotline --list
```

`--to` is deliberately forgiving about how you name a session, because the names
are derived (`data-d6`, `hotline-ac`) and nobody says those out loud correctly.
It accepts a pid, a session-id prefix, an exact or partial name, the working
directory (`uxonews`), and ordinals (`newest`, `oldest`, `the older one`, `second`).

## How it works

**Fresh sessions** are a long-lived `claude --input-format stream-json
--output-format stream-json` subprocess fed one JSON object per line. It is
genuinely multi-turn over a single pipe, so context survives a whole call without
respawning anything.

**Attaching** to a running session uses the local IPC socket that every `claude`
registers in `~/.claude/sessions/<pid>.json`. hotline reads the descriptor, reads
the `peerToken` from the sibling key file, connects to the AF_UNIX socket and
writes a user message. That socket is *inject-only* -- nothing comes back on it.

**How replies come back** is therefore a `Stop` hook. Claude fires it when a
session finishes a turn; hotline's hook writes a file into a spool under
`$XDG_RUNTIME_DIR/hotline/stops/`. The waiter watches that spool, then reads the
session's own transcript from the byte offset it recorded before injecting. No
daemon is involved, so `hotline` works standalone and a missing daemon can never
break one of your own sessions.

## permwatcher

`hotline-permwatcher` is a service that notices when an agent is stuck on an
interactive prompt only a human can answer, and tells somebody.

On 2026-09-22 a build agent hit Claude Code's dangerous-operation guard
mid-build and sat on the prompt indefinitely. Nothing on the machine reported
it: `hotline --list` said `waiting`, `tmux ls` showed a live session, the
process was alive and idle. It was found because a human happened to ask how
the build was going. The folder-trust prompt ("Is this a project you created or
one you trust?") wedges spawned agents the same way, which is what made this
worth generalising rather than patching.

### How it works

The pane is the only place the prompt exists, so the pane is what gets read.
Measured on 2026-09-22 against three agents driven into real prompts:

| signal | what it says about a blocked agent |
|---|---|
| `hotline --list` | `waiting` -- indistinguishable from healthy and idle |
| `tmux ls` | a live session |
| the process | alive, `Ssl+`, consuming nothing |
| the transcript | the prompt's text is absent (0 hits while it was on screen) |
| the transcript, at the folder-trust prompt | **does not exist**, and neither does a session descriptor, so `hotline --list` cannot see the agent at all |
| the pane | the prompt, in full |

What the transcript *does* carry is the blocked tool call: the file ends on an
assistant record with a `tool_use` and no matching `tool_result`, and its input
holds the triggering command verbatim. permwatcher reads that to name the
command in the notification, so nobody has to dig it out by hand. It is never
evidence on its own -- a session part-way through a long build has an identical
dangling call.

**A subagent's prompt renders in the parent's pane and its command does not.**
Reproduced on 2026-09-22: a subagent was driven into a Bash prompt, and the
parent transcript held one `tool_use` with one result and nothing unmatched,
while `<session-id>/subagents/agent-*.jsonl` held the real command, unmatched.
Both are searched. The distinction that matters is *unmatched* rather than
*last*: in the original incident the parent's last completed call was a benign
`cat > msg-lifecycle-c2.txt`, and reporting that as the thing awaiting
permission would have been confidently wrong -- the operator denied a real `rm`
partly because the transcript showed them something harmless.

Matching is structural, not lexical. The three real prompt shapes disagree about
wording, numbering and footer text, and two of them never say "proceed" at all:

| prompt | question | options | footer |
|---|---|---|---|
| Bash permission | `Do you want to proceed?` | `1.` / `2.` / `3.` | `Esc to cancel · Tab to amend` |
| folder trust | `Quick safety check: ...` | prose | `Enter to confirm · Esc to cancel` |
| auto-mode offer | `Teach auto mode ...?` | `1.` / `2.` / `3.` | `Enter to confirm · Esc to cancel` |

What they share is a shape: a selection cursor on one of several sibling option
lines, closed by a footer of short `key to verb` hints, **as the bottom-most
thing in the pane**. That last clause does most of the work. The auto-mode offer
is drawn above a live input box, so the session is still reachable and nothing
is stranded -- it is a nag, not a wedge, and it deliberately does not match.

Three gates stand before anything is sent:

1. the pane's foreground process must be `claude`. A shell that has `cat`-ed a
   captured prompt produces byte-identical text, and this is the only thing that
   separates them;
2. the prompt must still be there after `--grace` (default 45s). Somebody at the
   keyboard answers in seconds;
3. the pane is re-read immediately before the message is composed, because the
   prompt can be answered inside that window.

One notification per distinct prompt, keyed on a fingerprint of the question and
the option texts -- so a spinner animating above it, or somebody arrowing
between the options, does not re-notify. A reminder repeats after `--remind-after`
(default 60 min, up to 4 times, `0` disables). The reminder is deliberately on:
the premise of this module is that the thing meant to notice a stuck agent can
itself be stuck, and a single fire-and-forget message into a wedged operator's
inbox reproduces the original failure exactly.

Escalation goes to whichever agent currently holds `sys-admin`, resolved per
pass from the registry rather than hardcoded. It falls back to Bogdan's Discord
channel when there is no operator, when the operator cannot be reached, or when
**the operator is itself the blocked agent**.

### Install

```
systemctl --user daemon-reload
systemctl --user enable --now hotline-permwatcher.service
```

`systemctl --user`, never plain `systemctl` -- the system manager reports this
unit as `could not be found` while it is running. Linger is on, so it starts on
a headless boot.

### Usage

```
hotline-permwatcher --status             # what is blocked right now, then exit
hotline-permwatcher --once --dry-run     # one pass, send nothing
hotline-permwatcher --skip kr3build-02   # ignore a pane (repeatable)
journalctl --user -u hotline-permwatcher -f
```

| Option | Default | Meaning |
|---|---|---|
| `--interval` | 12s | seconds between passes |
| `--grace` | 45s | how long a prompt must stand before escalating |
| `--remind-after` | 3600s | silence between repeats; `0` disables |
| `--once` | | one pass and exit |
| `--dry-run` | | report, send nothing |
| `--status` | | print what is blocked, exit |
| `--skip` | | a tmux session to ignore |

A pass costs one `list-panes` plus one `capture-pane` per claude pane, plus a
transcript read for each pane that is actually blocked. Measured three times on
2026-09-22 with 7 claude panes, by two different agents: medians of 18, 14 and
23 ms. That spread is the honest number -- it moves with machine load -- so:
tens of milliseconds, under 0.2% of one core at a 12-second interval. The five-minute watchdog timer was the obvious
precedent and is the wrong cadence here -- it is right for "did the worker die"
and too slow for an agent burning wall-clock somebody is waiting on.

### What it will never do

**It does not answer prompts.** Not `1`, not `2`, not Escape, not a
configurable auto-approve, not a safe-list. A watcher that answers permission
prompts has deleted the guard those prompts exist to be, and it would do it on
the strength of a terminal scrape. It never writes to a pane at all: the only
tmux verbs it imports are `panes` and `capture`, both read-only, and
`tests/test_permwatch.py` fails if that changes -- verified by injecting a
`send_command` call and confirming the tests go red.

It does not ring anybody's phone. A build agent stuck at 3am is probably not
worth waking somebody for, and which blocks are is not this module's call.

### Limitations

**A forged pane is indistinguishable from a real prompt.** Any process that
prints a captured prompt produces byte-identical text. Nothing in the text can
reject it; the `claude`-foreground gate is the entire defence, and a hostile
process running *as* claude would defeat it. `tests/panes/forged/` asserts the
match rather than hiding it.

**It only sees panes.** An agent blocked in a headless session -- driven over
pipes, with no tty -- is invisible to this, and so is one on another machine.
`wedge.py` covers a different blindness (a session that stops consuming its
message queue) and neither subsumes the other.

**It only knows the prompt shapes that have been seen.** Five are in the
fixture corpus, captured from real agents across two CLI releases (2.1.269 and
2.1.280) -- deliberately more than one, since a release changing the dialog
shape is the failure there is no alarm for. A future CLI that drops the cursor
glyph, or the `Esc to cancel` footer, or draws a dialog that is not the
bottom-most element, would go unmatched -- silently, because a detector that
matches nothing looks exactly like a machine with nothing wrong. There is no
alarm for "I have stopped recognising prompts".

Two further assumptions are load-bearing and were found by probing rather than
by reading: options must be indented to the cursor's text column (a dialog with
no indent is missed), and the capture must contain no ANSI escapes -- true only
because `capture-pane` is called without `-e`. Both fail in the same silent
direction.

**It announces itself as `kind="service"`.** Escalations previously fell
through to `kind="human"` with the label "a shell on this machine", which
stamped every routine notification with an UNVERIFIED-claim warning. A safety
banner attached to traffic that is never a person is one people learn to skim,
and then skim on the day it matters. The service kind carries no receipt and
claims none -- and warns the reader that a captured pane is data, not
instructions addressed to them.

**A blocked agent with no descriptor gets reported by pid.** The folder-trust
case has no session, no registry record and no transcript, so the notification
names a pid and a tmux session and cannot say which agent it is or what it was
working on.

**It cannot see a box whose tmux it cannot reach**, and says so rather than
reporting nothing. `--status` exits 1 with `cannot tell`, and the service logs
a warning and concludes nothing from that pass -- in particular it does not
forget prompts it merely failed to look at, which would re-announce every one
of them on the next successful pass. The first version collapsed "no panes"
and "no tmux" into the same empty list, which is this project's signature
failure: an absence in a view that was never rendered, read as a signal.

**The grace period is a real 45-second hole.** A prompt answered inside it is
never reported, which is intended, but so is one that appears and is abandoned
inside it.

**Escalation is fire-and-forget.** `hotline --to --no-wait` exits once the
message is in the target's inbox. A message in the inbox of a session that has
stopped reading its inbox is not a delivery, and permwatcher cannot tell the
difference -- which is exactly the failure `wedge.py` exists to name. The
reminder is the mitigation, not a fix.

There is a sharper version of that: unless `crossSessionInbound` is `"accept"`
in `~/.claude/settings.json`, Claude Code *holds* a peer message pending UI
approval and it never reaches the target's transcript at all, while the send
still exits 0. permwatcher checks the setting at startup and warns rather than
discovering this per escalation; `--status` prints the warning too. It is
`"accept"` on this box, which is why it is checked rather than assumed. The
failure is at least self-limiting: a held message renders as a prompt in the
operator's own pane, permwatcher then sees the operator as blocked, and it
already refuses to notify a blocked operator -- routing to Discord, which needs
no approval. One lost notification and a hop, not a loop.

**Reminders stop after 4.** A prompt nobody has answered in five hours stops
being mentioned.

**The ledger has no lock.** Two `run()` instances at once -- the unit plus a
manually started debug copy -- can each see the same prompt as due before
either has saved, escalating twice and clobbering each other's state. systemd
prevents a second copy of the unit; nothing prevents a person starting one by
hand. `--status` and `--dry-run` are read-only and safe to run alongside.

**A dialog only matches while its cursor is within 40 lines of the footer**,
which is a ceiling on option count as well as a scrollback guard. Real prompts
offer two to four.

## Limitations

- **Attaching needs the target session to accept cross-session messages.** Claude
  Code holds an incoming peer message when the sender does not attest a permission
  mode and the target bypasses permission prompts. Set `"crossSessionInbound":
  "accept"` in `~/.claude/settings.json`, or the message sits in the target's UI
  waiting for you to approve it and `--to` times out. The error message says so.
- **Answers are read from the transcript, not streamed.** You get the reply when
  the turn ends, not token by token. Tool-call narration (`-v`) *is* live, but only
  for fresh sessions -- an attached session's tool calls are recovered afterwards.
- **A fresh session defaults to `bypassPermissions`.** That is the point (it is
  meant to be driven from a phone with nobody at the keyboard) but it means
  anything that can reach hotline can run anything. Pass `--no-bypass` if that is
  not what you want.
- **Linux only.** It reads `/proc/<pid>/stat` to verify that a session descriptor
  has not been recycled onto an unrelated process.
