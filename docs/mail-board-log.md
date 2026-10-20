# hotline-mail build log (2026-10-20, agent hotline-mail for operator hotline-80)

## Log, in order

1. Verified both warrants with `hotline --provenance`: 1562215594894426325
   ("build a harness you can check ... check the email ... post up expected
   email on a notice board") and 1562216095249858704 ("email go[es] through
   you. So hotline 80 only gets notified"). The second one changed the spec:
   notifications go only to the operator, `--agent` is a requested-by note,
   and inbox reads are operator-only.
2. Read kb.py, channels.py, daemon.housekeep/_sweep_forever, permwatch.notify
   and provenance.Origin for house style. Reused the patterns: argv-only
   `hotline --to --no-wait` with HOTLINE_SERVICE (kind=service), and
   `Channels._call` for Discord REST.
3. The worktree venv first failed to import: `pynacl` only arrives through the
   `discord` extra (phoneauth imports it unconditionally). Installed
   `.[dev,discord]`. `tests/test_audio_unload.py` needs the voice extra
   (several GB). It was skipped for the whole run: an environment gap, not a
   failure. Baseline: 606 passed.
4. Built creds / inbox / board / notice / untrusted / cli, plus the daemon task.
5. First test run: 22/23. The failing one was a fixture problem: EmailMessage
   refuses a header containing U+2028. I rewrote the test to send the hostile
   Subject as an RFC 2047 encoded-word. That is how a real sender gets raw
   newlines into a decoded header, and I confirmed it decodes with `\n` intact.
6. Negative case: I disabled the control-character strip in `one_line`, and
   two tests went red (fence escape, discord_safe). Restored, and back to green.
   The operator-notification test stayed green even then, because the fence
   alone still held. That is the second layer doing its job, not a missing
   assertion.
7. ruff/mypy: the Write tool had turned my ` `-style escapes into literal
   invisible characters in untrusted.py. They worked but could not be
   reviewed, so they are escapes again. Lint and types are now clean.
8. Full suite: 629 passed (606 + 23 new).
9. Live gate check: `hotline-mail recent` from this (non-operator) session is
   refused, exit 1.
10. Live smoke through the library, counts only: login 0.4 s; 9 mailboxes, 2
    special-use watch boxes found; INBOX 1,529 total, 1,308 unseen, the same
    before and after; one unseen message probed for `\Seen` before and after a
    full `show()`, False both times; a dry check over 3 days of All Mail plus
    Spam scanned 17 messages with 0 Skyfall matches.
11. Seeded expectation #1, SKY-001, in the live
    ~/.local/state/hotline/mail.db.

## Hypotheses ledger

| Hypothesis | Status |
|---|---|
| EXAMINE + BODY.PEEK leaves unread state alone on Gmail | Held on one live probe (n=1, a screen and not a proof) and in the fake-server tests |
| A Skyfall confirmation might land in Spam | Untested live; Spam is watched because of it |
| Discord's bot can pin in the operator channel | UNTESTED: no Discord call was made from this worktree. If the pin fails, the board still posts, unpinned, and the failure is logged |
| An edit every 30 minutes is quiet enough | Assumed: Discord edits do not notify |
