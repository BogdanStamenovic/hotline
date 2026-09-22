# hotline-permwatcher (hotline-4e, 2026-09-22/23)

A systemd user service that escalates agents blocked on prompts only a human
can answer. Built, installed, running, reviewed, and verified across two CLI
releases. Commits ba81455..5fb4530 on main, all pushed.

## State

- `hotline-permwatcher.service` is active and enabled (user manager; linger on,
  so it survives a headless boot). Poll 12s, grace 45s, reminders hourly x4.
- 567 tests green; 57 are this module's. Detector scores 0 FP / 0 FN on 12 real
  pane captures (5 blocked, 7 healthy), spanning CLI 2.1.269 and 2.1.280.
- Code: `src/hotline/permprompt.py` (pure detector), `src/hotline/permwatch.py`
  (service), `transcript.dangling_tool_use`, `tmuxen.Pane`/`panes()`.
- Docs: README under `## permwatcher`, LIMITATIONS is concrete and honest.

## The two things a successor must not undo

1. **It never answers a prompt and never writes to a pane.** Only read-only
   tmux verbs are imported. `tests/test_permwatch.py` asserts this against the
   module's *compiled* code (comments and docstrings stripped, because the
   first version failed on its own documentation). Confirmed to go red when a
   `send_command` call is injected.
2. **`tests/panes/forged/` is SUPPOSED to match.** A shell that printed a
   captured prompt is byte-identical to a real one. Text cannot reject it; the
   `claude`-foreground gate is the whole defence. The test asserts the match
   rather than hiding the limit. Do not "fix" it.

## Open, and deliberately not decided here

**No phone ring.** Escalation goes to the sys-admin agent, falling back to
Bogdan's Discord channel when there is no operator, it is unreachable, or it IS
the blocked agent. Whether some blocks deserve a real ring is his call; he was
asked and has not answered. Until he does, it never rings.

## Facts worth keeping

- A pending prompt's TEXT is never in the transcript, but the blocked tool call
  is: the file ends on a `tool_use` with no `tool_result`, carrying the command.
  For a SUBAGENT-raised prompt that lives in `<session>/subagents/agent-*.jsonl`,
  not the session file. Take the newest UNMATCHED call, never the newest --
  taking the newest reports a completed benign command as the trigger, which is
  how a real operator denied a real `rm` on bad evidence.
- An agent at the folder-trust prompt has no transcript, no descriptor and no
  project directory. `hotline --list` cannot see it at all. That is the single
  strongest justification for reading panes.
- The signature only exists while the prompt is pending; a resolved transcript
  proves nothing either way. Test against a live blocked agent.
- Load-bearing and invisible: options must be indented to the cursor's text
  column, and captures must carry no ANSI (true only because `capture-pane` is
  called without `-e`). Both fail silently toward "nothing is blocked".
