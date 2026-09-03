# hotline-claude

Session-orchestration infrastructure underneath [`hotline`](../../README.md):
spawning and pooling Claude Code sessions (`pool.py`, `tmuxen.py`), the
control-socket and transcript plumbing that talks to a live session
(`ccsocks.py`, `transcript.py`), the session registry (`agents.py`), reviving
a finished agent (`revive.py`), a stand-in reply while a session is busy
(`standin.py`), and the hooks that make a session addressable at all --
the `Stop` hook (`stops.py`), the `PreToolUse` denylist guard (`guard.py`), and
the `AskUserQuestion` -> Discord bridge (`ask.py`). `hotlined`, the daemon the
iPhone Shortcut talks to, lives here too (`daemon.py`, `httpd.py`).

`router.py` also lives here. It is one of hotline's three frozen files (see
the root README) and this split did not edit it -- it moved verbatim because
its content drives the session registry, control sockets, hooks and
transcripts in this package directly, not because "routing" is conceptually
infrastructure rather than comms. That is also why this package depends on
`hotline` rather than the other way around only: `pool.py` and `daemon.py`
need `hotline`'s `Router`, Discord bot, channel manager, pager and provenance
checker on top of what lives here. The two packages are genuinely coupled at
the module level -- not a workspace formality -- and are always installed
together in the same virtualenv.

Every module `hotline` used to own directly (`hotline.agents`, `hotline.pool`,
`hotline.router`, ...) still resolves: `hotline`'s own package carries a
permanent re-export shim at each old path, load-bearing for `hotline-ios`
(a separate repo, the iPhone bridge) which imports several of them by their
pre-split names.
