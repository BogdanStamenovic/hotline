# hotline-admin

The admin surface for [`hotline`](../../README.md), packaged as a plugin
rather than built into core. `hotline`'s CLI discovers it at runtime through
the `hotline.plugins` entry-point group declared in this package's
`pyproject.toml` -- core never imports it, and lookup is by entry-point name,
so adding a verb is a line in this package's `pyproject.toml` rather than a
change to core.

## The verbs it owns

- `--grant NAME ROLE <message>` -- record a standing role (e.g. sys-admin) and
  the Discord message where a human granted it, so the delegation is checkable
  against Discord rather than the machine vouching for itself.
- `--declare TASK` -- register a session and what it is working on, and give it
  a Discord channel.
- `--adopt NAME` -- take over a finished agent's identity and channel, for a
  session respawned to continue its work.

What they have in common is the *roster*: who exists, what they were told to
do, who spawned whom, and who granted what. That is the operator's model of the
machine. `hotline` core is the call/page/Discord layer underneath it.

## What stayed in core

`--agents`, `--list` and `--provenance`. The first two are read-only views a
running agent uses to orient itself; `--provenance` is message verification,
part of the comms trust layer every relay depends on, and not an admin action
at all.

## Install it on this box

Core *declares* the flags above even when this plugin is absent, so
`hotline --adopt x` on a bare install parses and reports

    hotline: error: --adopt requires hotline-admin, which is not installed.

rather than argparse's "unrecognized arguments", which would read like a typo
in the caller's own command. That is a real supported shape -- a comms-only
`hotline` is a coherent thing to install.

It is not the right shape *here*. Every spawned agent runs `hotline --adopt` as
its first act, so a hotline on this machine without this plugin is one where no
new session can get an identity. Install `hotline[admin]`, and note that a
plain `pip install -e .` of core will **not** pull this in by design.
