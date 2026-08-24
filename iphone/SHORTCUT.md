# Build the "Hotline" iPhone Shortcut

Same shape as the `Todo` shortcut you already run against pigion-todo, pointed at
a different URL. About three minutes. Building it by hand in the Shortcuts app
keeps everything on the phone and produces an Apple-trusted shortcut; generated
`.shortcut` files cannot be signed without Apple's macOS-only signing command.

Before starting: the iPhone must be on the same Tailscale network as `pigion`, and
Dictation must be on in **Settings → General → Keyboard**. On supported languages
Apple Dictation runs on-device, so no audio leaves the phone and archserver's GPU
is not involved at all.

## The actions

Create a shortcut named **Hotline**, then add these in order:

1. **Text** — a fixed random session id, e.g. `iphone-6f2a1c9e-hotline`.
2. **Set Variable** — name it `SessionID`.
3. **Speak Text** — `Hotline. What do you need?`
4. **Repeat** — `999` times.
5. Inside Repeat: **Dictate Text**. Language as usual, "Stop Listening" → **After Pause**.
6. **If** — Dictated Text **is** `stop`.
7. Inside If: **Stop This Shortcut**.
8. **Otherwise**.
9. Inside Otherwise: **Dictionary** with three Text entries:
   - `text` → the **Dictated Text** magic variable
   - `session_id` → the `SessionID` variable
   - `client` → `iphone`
10. **Get Contents of URL**:
    - URL: `http://pigion:8788/api/v1/claude`
    - Method: **POST**
    - Request Body: **JSON**, then select the **Dictionary** magic variable
    - Headers: none needed unless you set `HOTLINE_API_KEY` (see below)
11. **Get Dictionary Value** — key `response`, dictionary **Contents of URL**.
12. **Speak Text** — speak the Dictionary Value.
13. Close **End If**, then **End Repeat**.

Then say **"Hey Siri, Hotline"**. Say `stop` to end.

If `pigion` does not resolve on the phone, use `100.114.148.69` instead.

## What you can say

The same routing the CLI uses, so speech works:

| You say | What happens |
|---|---|
| anything | a fresh Claude session, kept alive for the whole call |
| "new session, …" | throws away the current context and starts over |
| "join data-13, what's failing?" | injects into that live terminal session and reads its reply |
| "join the one in uxonews, is the build green" | same, resolved by directory |
| "what are you working on" | attaches to the most recently started session |

Session names are derived (`data-d6`, `hotline-ac`) and awkward to dictate, so the
router also takes a directory name, a pid, or an ordinal — "the older one", "the
newest", "the second one".

## Long answers

A real turn can take minutes; a phone will not wait that long. After 100 seconds
the reply becomes *"Still working on that one. Ask me again in a moment and I'll
have the answer."* The turn is **not** cancelled — it keeps running, and whatever
you say next collects the finished answer.

One consequence worth knowing: **while a turn is in flight, anything you say is
treated as a check-in on it, not as a new instruction.** Say "are you done?" or
just "hello" — the words are discarded either way and you get the pending answer.
If you want to ask something genuinely new, wait for the previous answer first.

## Authentication

By default the gate is the **source address**: only the phone, pigion and
archserver's own Tailscale addresses can reach `/api/v1/claude`. There is no
shared secret, which means there is nothing to transmit to the phone.

If you want a second factor, put the same value in `HOTLINE_API_KEY` in **both**
`~/data/hotline/.env` on archserver and `~/.config/hotline-frontdoor.env` on
pigion, restart both services, then add a header to step 10:
`X-Hotline-Key` → that value. Read the value out of the file yourself; it is
deliberately never printed into a log, a commit, or a message.

## What is actually running

```
iPhone ──Tailscale──▶ pigion:8788  (frontdoor.py, stdlib only, 23 MB RSS)
                          │
                          ├─ GET /health          is archserver awake?
                          └─ POST /api/v1/claude  forwards it on
                                    │
                          ──Tailscale──▶ archserver:8788  (hotlined)
                                    │
                                    └─ session pool ──▶ claude --input-format stream-json
```

Both are systemd **user** units with lingering enabled, so they come back after a
reboot without anyone logging in:

```
pigion      systemctl --user status hotline-frontdoor
archserver  systemctl --user status hotlined
```

The phone points at pigion rather than straight at archserver on purpose: the URL
inside a Shortcut is annoying to change, and in Phase 5 that same endpoint gains
the job of sending a magic packet and waiting for archserver to boot. The phone
never has to know.

## Limitations

- **Half duplex.** You talk, then it talks. There is no interrupting it mid-answer;
  that needs the Discord voice path.
- **You cannot start a call from the machine's side.** This loop only exists while
  the Shortcut is running on the phone. Claude paging *you* is the Discord path.
- **Tool-call narration is collected but not spoken.** It comes back in the
  `narration` field, which is useful for debugging but arrives with the answer
  rather than during the wait. Speaking it during the wait requires a duplex
  transport.
- **Attached sessions treat you as a peer, not as their user.** A live session
  will refuse things from an injected message that it would accept typed directly
  — it is told explicitly that the message came from another session and that a
  peer cannot grant escalation. Fresh sessions have no such reduction.
- **Reboot survival is configured but not yet proven.** Lingering is on and both
  units are enabled; restarting the services works. Nobody has power-cycled either
  machine since, so "it comes back after a reboot" is a reasonable expectation
  rather than an observed fact.
