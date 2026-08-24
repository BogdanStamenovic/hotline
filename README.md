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
