---
name: call-bogdan
description: Reach Bogdan and wait for his answer when you are genuinely blocked - an approval he has to give (spending money, sending email, an outward action), a decision only he can make, or a real dead end you cannot route around. Escalates from a Discord @mention to a DM to the physical siren on his workstation, and returns his reply. Use when you would otherwise stop and wait, or guess. Do NOT use for progress updates.
---

# Calling Bogdan

`~/.claude/bin/hotline-page` posts to Discord, `@mention`s him (which pushes to his lock screen),
DMs him, escalates if he is slow, and **blocks until he replies**. His answer comes
back on stdout.

```bash
answer=$(~/.claude/bin/hotline-page --source "the deploy script" "Staging is green. Push to prod?")
echo "$answer"
```

Exit codes: `0` he answered · `1` the page could not be delivered · `2` usage
error · `3` delivered but nobody answered before the timeout.

**`3` is not `1`.** "He did not reply" and "Discord is broken" call for completely
different behaviour, which is why they are different codes. On `3` you decide
whether to proceed under a stated assumption or stop. On `1` nobody was asked at
all, and you must not act as though they were.

## When to use it

Use it when you would otherwise **stop and wait**, or **guess**:

- An approval only he can give. CLAUDE.md is explicit: **spending money and sending
  email require calling him first.** Any outward action on someone else's system.
- A decision that changes the shape of the work, where guessing wrong wastes hours.
- A real dead end — you have tried several approach *classes*, not five variations
  on one, and you are actually stuck.
- Something destructive with no reversible path and no snapshot.

## When not to use it

- **Progress updates.** Post those with the bot, or just write them down. A pager
  that fires for news gets muted, and then it is worth nothing when it matters.
- Anything you can find out yourself. Read the file, run the command, check the log.
- A question with an obvious default. Pick the default, say you picked it, move on.
- Batch your questions. Bring him the whole plan and the cost in one page, not one
  page per step.

## Options

| Flag | Effect |
|---|---|
| `--source` | who is asking, e.g. `"the hotline build"` — he sees this |
| `--context` | extra detail, rendered in a code block |
| `--timeout SEC` | give up after SEC (default 1800) |
| `--no-wait` | post it and exit; use when you can genuinely carry on without the answer |
| `--no-siren` | never fire the workstation siren, however long he takes |

## The escalation ladder

| Elapsed | What happens |
|---|---|
| 0 | `@mention` in the Discord channel, **and** a DM |
| 2 min | nudge |
| 5 min | nudge |
| 10 min | **siren** on the workstation — full volume, in case he is sitting there with his phone face down |
| 15 min | nudge |
| 25 min | siren |
| 30 min | gives up, tells him nobody is waiting any more, exits `3` |

Pass `--no-siren` if you know he is not at the machine. The siren is
`~/.claude/bin/wake-bogdan.sh` — a real alarm, not a notification sound.

## Prefer --no-wait when you can keep working

Blocking for half an hour is usually the wrong shape. If the answer only affects
one branch of the work, post it with `--no-wait`, do everything that does not
depend on it, and pick the answer up later. Being blocked while he is away is not
a reason to stop.

## Notes

- `~/.claude/bin` is **not** on `$PATH`, so invoke it by full path, the same way
  `wake-bogdan.sh` is invoked. The shim there points at hotline's venv, so no
  activation is needed.

- Works with no daemon running: it is plain Discord REST, so it still reaches him
  when `hotlined` is dead — which is exactly when you most need a human.
- Configuration comes from `~/data/hotline/.env` (`HOTLINE_BOT_TOKEN`,
  `DISCORD_USER_ID`, `DISCORD_TEXT_CHANNEL_ID`). Never print those values anywhere.
- While a page is outstanding the Discord text bridge stops answering that channel,
  so his reply reaches you and is not swallowed by a new Claude turn.
