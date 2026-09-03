# split-packages: where this is, as of 2026-09-03 22:30

Written before a planned poweroff rehearsal. Delete this file at merge — it
describes a moment, not the design. The design is in the three package READMEs.

## State: done and green, waiting on a merge decision

Five commits on top of `ce8a211`, rebased, nothing merged, no daemon restarted.

```
f274f54  Relock for the hotline[admin] extra
3cb500d  Move --adopt and --declare into the hotline-admin plugin
aee2180  Stop documenting the withdrawn "frozen files" rule as live
6f6cc25  Make the router and pool shims statically honest
2f559e6  Split hotline into hotline / hotline-claude / hotline-admin
```

Working tree clean. Local durability: `backups/split-packages-final.bundle`
(verified, complete history — `git clone` it if `.git` is ever lost).

## Verified green — actually run, not inferred

| check | result |
|---|---|
| pytest | 510 passed (501 was the baseline on `main`; 9 new in `tests/test_plugins.py`) |
| ruff | 5 findings, byte-identical to `main`'s pre-existing set (mirror.py ×4, pager.py ×1) |
| mypy | 33 findings on both `main` and this branch; **zero new**, compared entry by entry |
| hotline-ios | whole coupling surface resolves; `hotline.tmuxen is hotline_claude.tmuxen` |
| CLI | all 12 verbs driven against the real binary in a sandboxed HOME/state |
| secrets | all 8 live `.env` values checked against 120 tracked files + the 5-commit diff: absent |

## NOT verified

- **Nothing has been run against the live daemons.** `hotlined` (8788) and
  `hotline-ios.service` (8789) are still on `main` and were never restarted.
  The split is proven by the suite and by the CLI, not in production.
- `hotlined` reports `mirror_degraded: true`. That predates this work (~3h
  uptime at the time of writing) and was left alone.
- The `admin` extra has never been installed anywhere except this worktree's venv.

## The next step, and the trap in it

Merge to `main`, then **install `hotline[admin]` in the live venv**, then restart.

That middle step is not optional and is easy to miss, because a plain
`pip install -e .` of core deliberately does **not** pull the plugin. `--adopt`,
`--declare` and `--grant` now live in `hotline-admin` and are discovered through
the `hotline.plugins` entry point. Every spawned agent runs `hotline --adopt` as
its first act, so a live venv without the plugin is one where **no new session
can get an identity**. The failure is loud (`--adopt requires hotline-admin,
which is not installed`, exit 1) but total.

Check after any reinstall:

    hotline --adopt <some-known-agent>    # exit 0, prints "adopted: ..."

## Rollback

- Tag `pre-rebase-split` — the branch before it was rebased onto `ce8a211`.
- `backups/pre-rebase-20260903-212904.tgz` — src/packages/tests/pyproject.
- `backups/pre-split-20260903-195134.tgz` — the pre-split snapshot (verified).
- `main` itself is untouched at `ce8a211`.

## One correction to the original charter

It listed "operator spawn/retask/shutdown controls" as admin-plugin verbs.
**Those verbs do not exist.** Retasking is `--declare` run again, and session
kill is natural-language routing through the pool — there was nothing to move.
`--agents`, `--list` and `--provenance` stayed in core deliberately.

## Not pushed to origin, on purpose

`origin` is a **public** repo, and publishing is an outward action that needs
Bogdan's own yes — the operator's sys-admin grant explicitly does not cover
approving outward actions. The work is not at risk from this: the commits are
in `.git` and in the bundle above, and a poweroff does not touch either.

One command completes it once he says yes:

    git push -u origin split-packages
