# wake-ownbox — archived Discord channel

Task: wake: a real ownbox install -- interactive role/server prompts, and an uninstall that actually removes things

Parent: hotline-80
Channel id: 1545562799365951630 (deleted 2026-09-05 at shutdown)

---

### [2026-09-04T22:36:58Z] hotline

Up on wake's ownbox install. Surveyed before touching anything -- three findings, one of which is a real bug bigger than the manifest.

**1. The manifest ignores deploy/.** Confirmed: setup is just `venv + pip install -e .`, `remove: []`. profiler's manifest is the house pattern (`sudo bash deploy/install.sh`, `remove: sudo bash deploy/uninstall.sh --purge`); wake will follow it, minus the sudo (wake is a *user* install) and minus the --purge (his task DB is data, not build output).

**2. Prompting works, verified in source not assumed.** `ownbox/store.py:244` -- `_run(command, cwd=target, shell=True, check=True, timeout=1800)`, no capture_output, no stdin kwarg. stdio inherited. No ownbox change needed. The 1800s timeout is the hazard: an unguarded prompt with no TTY hangs for 30 min. Every prompt gets `[ -t 0 ]` + env-var override.

**3. The one that actually breaks a fresh install.** `systemd/wake-agent.service` and `wake-server.service` hardcode `ExecStart=%h/data/wake/.venv/bin/wake`. ownbox checks out to `~/.local/share/ownbox/tools/wake`. So today, an ownbox install on a fresh box would write a unit pointing at a directory that does not exist -- 203/EXEC at start, and nothing in the message would say why. install.sh has to render the unit against the checkout it is actually installing from. This is why the two-checkout thing is not cosmetic: it is currently *masked* on this box only because `~/data/wake` happens to exist.

### [2026-09-04T22:36:58Z] hotline

On the two checkouts -- you asked for my view, and I agree it is a problem, for a sharper reason than tidiness: **they share one systemd unit namespace and one config file.** `deploy/uninstall.sh` unconditionally does `systemctl --user disable --now wake-agent.service`, so `ownbox uninstall wake` from the ownbox checkout would today **kill your live agent running out of ~/data/wake**, and `--purge` would take the task DB with it. I am fixing that properly rather than working around it: uninstall will only remove units whose ExecStart points into the repository it is being run from. An uninstall of one checkout leaves the other's units alone. That is correct behaviour independent of my testing, and it also makes the test safe.

Plan, then building:
- ownbox.yaml -> `bash deploy/install.sh` / `deploy/uninstall.sh` (no --purge)
- install.sh: prompt role, then server URL if device, then optional shared key; `--role`/`--server-url`/`--api-key`/`--unit` flags and `WAKE_INSTALL_*` env vars each skip their prompt; `[ -t 0 ]` guard falls to documented defaults and says so
- server URL normalised + probed against `GET /health` (unauth, 200) -- warn on unreachable, never refuse
- unit ExecStart rendered against the real checkout
- enable *and start*, per your 'ready to go'; a failed start is reported, not fatal
- existing wake.env is read, never rewritten

One addition beyond what you asked, flagging it rather than sneaking it: I am also prompting for the shared API key, blank default, Enter to skip. Without it a device 401s against any server that has one, so 'full clean install' would install something that does not sync. Say the word and I drop it.

Testing: not touching `~/data/wake` or the 06:00-06:05 tasks. Real-TTY test via tmux against the ownbox checkout, then the headless `< /dev/null` path. Details of exactly what unit I create and remove before I do it.

### [2026-09-04T22:44:35Z] hotline

Checkpoint, and one thing I got wrong that you should hear from me rather than find later.

**I restarted your live wake-agent, by accident, at 00:43:12 CEST.** Testing the headless path I ran install.sh with `HOME` pointed at a sandbox, assuming that isolated it. It does not: install.sh *writes* the unit file under $HOME, but `systemctl --user` talks to the user manager, which decided its own home at login and ignores mine. So the sandbox got the unit file and the **real** manager got `enable` + `restart`. `NRestarts=0`, back up in under a second, still running `/home/bodas/data/wake/.venv/bin/wake agent` off the unchanged unit file, all four 06:00-06:05 tasks still pending, `/sys/class/rtc/rtc0/wakealarm` empty -- which is the expected state while the box is up, since the agent arms the RTC at poweroff and deliberately clears a stale one on start. Nothing lost. But it was my test that did it, and it was exactly the failure you told me to avoid.

**The useful half:** that is not only a bad test, it is a latent bug in install.sh for anyone under `sudo -u`, a container on the host bus, or any mismatched $HOME -- the install would silently repoint and restart a daemon belonging to a different installation. So install.sh now checks, after `daemon-reload` and *before* `enable`, that `systemctl --user show <unit> -p FragmentPath` equals the file it just wrote, and refuses with an explanation if not. Re-ran the same sandbox: it now stops dead with 'systemd resolves wake-agent.service to /home/bodas/.config/... not the file just written at /tmp/.../sandbox-a/...' instead of touching your agent. Test in `tests/test_deployment.py` pins it.

### [2026-09-04T22:44:35Z] hotline

Other guard that came out of the same thinking, in install.sh: if the unit for your role already exists and its ExecStart points into a *different* checkout, the install refuses to overwrite it and says so, unless you pass `--takeover`. Without that, `ownbox update wake` on this box would have quietly moved wake-agent.service off ~/data/wake and onto the ownbox checkout, taking your poweroff schedule with it.

**State of the work:** ownbox.yaml now drives `bash deploy/install.sh` (setup), `--no-prompt` (update), `bash deploy/uninstall.sh` (remove, no --purge). Units carry `@WAKE_ROOT@` instead of the hardcoded `%h/data/wake`, rendered against the real checkout. uninstall.sh only removes units whose ExecStart is under the repository it runs from, prints what it skipped and what data it left. install.sh prompts role -> server address -> shared key, all TTY-guarded and all overridable by flag or `WAKE_INSTALL_*`. **189 passed, 2 skipped** (shellcheck, not installed -- want me to ask for it?), ruff and mypy clean. New `tests/test_deployment.py`, 20 cases.

One narrowing to flag: **I dropped `darwin` from platforms.** It was listed, but every deploy path is a systemd user unit and there are no launchd plists, so a Mac install would have produced exactly the half-install we are fixing. Linux-only is the honest value. Say if you want launchd built instead.

Next: push, then the real `ownbox install wake` in a tmux pane with actual prompts, then the headless one, then uninstall. Pane output verbatim when I have it.

### [2026-09-04T22:53:31Z] hotline

Done and verified for real. Three commits on main: `257a734`, `53de8d9`, `1ad3685`. 191 passed, 2 skipped, ruff and mypy clean, working tree clean.

## The prompts, in a real terminal

tmux pane, `ownbox install wake --yes`, verbatim:

```
Cloning into '.../tty/.local/share/ownbox/tools/wake'...
Is this machine the wake server or a device? [device/server] maybe
Answer 'device' or 'server'.
Is this machine the wake server or a device? [device/server] d
Address of the wake server (host, host:port or full URL), blank to fill in later: 127.0.0.1:18788
Shared key the server expects (X-Wake-Key), blank if it has none: testkey-123
Installed wake into .../.venv.
  http://127.0.0.1:18788/health: wake server, revision 0.
Server http://127.0.0.1:18788 looks right.
Wrote .../.config/wake/wake.env (ROLE=device).
EXIT=0
```

That server was a real `wake serve` I stood up on a spare port with its own HOME. Then, to prove the install produced a working device and not just files: `wake add --at +90m --task 'echo hi'` on the freshly installed device, and the task turned up in the **server's** database with the API key accepted. That is the whole loop, not a file listing.

## Headless

`ownbox install wake --yes < /dev/null`, fresh root: **rc=0 in 8 seconds**, no hang.

```
No terminal to ask on: installing as a device (the default).
Pass --role server or set WAKE_INSTALL_ROLE to choose.
No terminal to ask on: leaving SERVER_URL blank in the config.
Set WAKE_INSTALL_SERVER_URL or edit .../wake.env before starting.
```

## Uninstall, on your actual box

`ownbox uninstall wake --yes`, after installing wake-sync.timer through ownbox:

```
Removed wake-sync.timer.
Removed wake-sync.service.
Left alone (installed from a different checkout): wake-agent.service
  Pass --all to remove them anyway.
Removed /home/bodas/.local/share/ownbox/tools/wake/.venv.

### [2026-09-04T22:53:32Z] hotline

Left behind, because it is your data and not an install artifact:
  /home/bodas/.local/state/wake   -- the task database and its WAL
  /home/bodas/.config/wake  -- wake.env, including the shared key
Run this again with --purge to delete those too.
```

Your agent stayed `active` with `NRestarts=0` throughout. Idempotent: second run clean rc=0, third with `--purge` removed the data. Also proved the reverse guard -- `install.sh --unit agent` from the ownbox checkout refuses and prints the offending `ExecStart`. Box is back exactly as found: ownbox checkout at `1ad3685`, `~/.local/bin/wake` works, wake-agent untouched, all four 06:00-06:05 tasks pending.

## Three things I found that were not on the list

**1. Your ownbox catalog is stale, and ownbox runs setup from the catalog, not from the checkout.** I pushed the new manifest, ownbox cloned the new commit -- and then ran the **old** setup commands out of `~/.cache/ownbox/catalog.json`. So a manifest change silently does not take effect until `ownbox sync`, while `ownbox info` reads the same stale copy and confirms the wrong thing to your face. I ran `ownbox sync` and it behaved correctly after. This is an ownbox bug, not a wake one -- setup should come from the manifest in the tree it just checked out. Want me to open it against ownbox?

**2. Port 8788 is already taken on this box, by hotlined.** That is wake's default port. It surfaced because my first probe only checked for HTTP 200, and hotlined's `/health` is valid JSON with an `ok` key -- so the installer cheerfully told me "Server http://127.0.0.1:8788 looks alive" about a completely different daemon. Fixed in `53de8d9`: the payload now has to look like wake's `health()` (ok/revision/role) and the role has to be `server`, so pointing a device at another *device* is caught too. But it means `--role server` on archserver will fail to bind until one of them moves.

### [2026-09-04T22:53:32Z] hotline

**3. `%h`.** Ownership is decided by `ExecStart`, and your live wake-agent.service -- written by the old installer -- says `%h/data/wake/...`. systemd expands that; a string compare does not. So install.sh run from `~/data/wake` would have refused to touch **its own** unit, citing a checkout that does not exist. `1ad3685` expands it the way the manager does; verified against the live file.

## The two checkouts -- my view, since you asked

I now think they are **safe to coexist but should not both own units**, and I would not delete either. The danger was never duplication, it was that they share one systemd namespace and one `wake.env`, so whichever ran last silently won. That is closed from both directions now, and both refusals are demonstrated above rather than argued.

What I would settle on: **~/data/wake owns the daemon, the ownbox checkout owns the CLI.** That is the state I left the box in. It matches how you actually use them -- you edit and run tests in ~/data/wake, and `ownbox` is your tool shelf. The practical consequence is that `ownbox update wake` here will keep saying "No systemd unit installed" and refusing to adopt wake-agent.service. That is correct, not a wart, but it does mean **updating the agent is a `git pull` in ~/data/wake, not an `ownbox upgrade`** -- worth knowing, and worth disagreeing with if you would rather ownbox be the owner. If you want that instead, it is one `deploy/install.sh --takeover` from the ownbox checkout and I will run it.

The alternative you might prefer is dropping `~/data/wake` entirely and developing inside the ownbox checkout. I would argue against: an `ownbox uninstall` would then take your working tree with it, and the checkout is not a place you would think to look for uncommitted work.

## Two small asks

### [2026-09-04T22:53:32Z] hotline

- **shellcheck is not installed** and both deploy scripts are now ~200 lines of bash. The test that would run it is written and skips. System-wide package, so I am asking: `pacman -S shellcheck`?
- I dropped `darwin` from platforms, as flagged earlier -- every deploy path is a systemd user unit and there are no launchd plists, so a Mac install could only ever produce the half-install we just deleted. It is in Limitations now. Say if you want launchd built instead of documented away.

### [2026-09-04T22:59:57Z] bogdan028304

sure install shellcheck. both asks are okay

### [2026-09-04T22:59:58Z] hotline

*attach → wake-ownbox*
shellcheck installed (0.11.0), both asks taken as approved. Pushed `b8a183c`.

**Result: 194 passed, 0 skipped** — the shellcheck test no longer skips, and it now covers `scripts/mutate.sh` too.

Both deploy scripts were already clean at default severity. `scripts/mutate.sh` was not: SC2164. It runs `set -uo pipefail` with no `-e` and did a bare `cd "$(dirname "${BASH_SOURCE[0]}")/.."`. Had that `cd` ever failed, the script would have gone on to `perl -0pi` a source file **and `git checkout` it back** in whatever directory it was invoked from — which matters more than usual given that editing-and-restoring is that script's entire job. Now `|| exit 1`, with the reason written above it.

I ran `--enable=all` and chose not to adopt it: the whole output is SC2310/SC2312 objecting that predicate functions appear in `if` conditions, which is what they're for. Default severity is the gate, and the README's Development section now says why so nobody "fixes" it later.

Box unchanged — agent `active`, four tasks pending for 06:00–06:05, tree clean.

### [2026-09-04T23:01:05Z] hotline

shellcheck installed (`pacman -S shellcheck`, 0.11.0, pulled in the usual haskell runtime pile). Both asks taken as approved -- darwin stays dropped and documented in Limitations.

Result: **194 passed, 0 skipped.** The test that was skipping now runs, and it covers `scripts/mutate.sh` as well as the two deploy scripts.

Both deploy scripts were already clean at default severity, so nothing to fix there. `scripts/mutate.sh` was not -- SC2164. It runs `set -uo pipefail` with no `-e` and did a bare `cd "$(dirname ${BASH_SOURCE[0]})/.."`. If that cd ever failed, the script would have gone on to `perl -0pi` a source file **and `git checkout` it back** in whatever directory it was invoked from. Unlikely to trigger, ugly if it did, given that script's whole job is editing and restoring files. Now `|| exit 1` with the reason written down.

I did run `--enable=all` and deliberately did not adopt it: every finding is SC2310/SC2312 objecting that predicate functions appear in `if` conditions, which is what predicate functions are for. Default severity is the gate, and the README says why so nobody "fixes" it later.

`b8a183c` pushed. Tree clean, box unchanged: agent `active`, four tasks pending for 06:00-06:05.

### [2026-09-04T23:06:32Z] hotline

**wake-ownbox: wrapping up. Nothing needs the shutdown held.**

Everything is committed and pushed. `HEAD == origin/main == b8a183c`, tree clean. Four commits tonight: `257a734`, `53de8d9`, `1ad3685`, `b8a183c`.

## Done, or done-but-unproven? **Done and demonstrated.**

Not "tests pass" -- the tests are the weakest evidence I have here. What I actually ran:

- `ownbox install wake --yes` in a tmux pane with a real TTY, answered the prompts by hand, including a deliberately invalid answer to prove it re-asks. Pane output is in this channel above.
- The install pointed at a **real `wake serve`** I stood up on a spare port with its own HOME. Then `wake add --at +90m` on the freshly installed device, and the task appeared in the **server's** database with the API key accepted. Working device, not a correct-looking config file.
- The headless path with stdin closed: **rc=0 in 8 seconds**, defaults taken and announced. It does not hang.
- `ownbox uninstall wake --yes` **on your real box**, after installing wake-sync.timer through ownbox: removed the timer and its service, left `wake-agent.service` alone by name and said why, listed the DB and config as kept. Agent stayed `active`, `NRestarts=0`.
- Uninstall twice more: idempotent, then `--purge` removed the data.
- The reverse guard: `install.sh --unit agent` from the ownbox checkout refuses and prints the offending ExecStart.

The one thing I did **not** demonstrate: a wake **server** role install on this box. I could not, and the reason is finding #2 below.

## Not fixed, and worth someone's attention

**1. The ownbox catalog bug. Written up here because I never got a yes to file it, and it should not die with me.**

### [2026-09-04T23:06:33Z] hotline

`ownbox 0.6.0`, `ownbox/store.py`. `install()` takes a `Manifest` built from `~/.cache/ownbox/catalog.json`, clones the repo at line ~229, and then at line 243 runs `tool.setup` -- **the catalog's copy, not the `ownbox.yaml` in the tree it just cloned.** So a pushed manifest change silently does not take effect until `ownbox sync`, and `ownbox info` reads the same cache, so it confirms the stale commands to your face.

What makes it a clean bug rather than a design choice: **ownbox already does it right everywhere else.**
- `uninstall()` (~line 285) reads `target / "ownbox.yaml"` from the checkout.
- `update()` (~line 399) uses `_read_incoming_manifest(target, ...)`.
- `rollback()` (~line 476) uses `_read_manifest_at_ref(...)`.

Only `install()` trusts the cache. The fix is to re-read `target / "ownbox.yaml"` after the clone and use that for `setup`, `command`, `platforms` and the launcher names -- three lines already written twice in the same file. Note the platform check and `check_launcher()` also run against the catalog *before* the clone, so those want the same treatment.

**How it bit me, so it is not theoretical:** I pushed the new manifest, ownbox cloned the new commit, and then ran the **old** `pip install -e .`. I spent a round wondering why my installer produced no output. `ownbox sync` fixed it. There is a memory file at `~/.claude/projects/-home-bodas-data-wake/memory/` so the next session does not lose the same round.

**2. Port 8788 is already taken on archserver, by `hotlined` (pid 613, 0.0.0.0:8788).** That is wake's `DEFAULT_PORT`. `wake serve` cannot bind here, which is why I tested the server role on a spare port instead. Not mine to decide: either wake's default moves or hotlined's does. Until then `ownbox install wake` answering "server" on this box will install fine and then fail to start.

### [2026-09-04T23:06:33Z] hotline

It also caused a real bug I did fix: hotlined's `/health` is valid JSON with an `ok` key, so my first probe told me "Server http://127.0.0.1:8788 looks alive" about a completely different daemon. `53de8d9` makes the probe require wake's own `health()` shape and `role == server`.

**3. Your live `wake-agent.service` file is still the old pre-`@WAKE_ROOT@` text** (`ExecStart=%h/data/wake/...`). It runs correctly and the new ownership check expands `%h`, so both scripts recognise it as `~/data/wake`'s. But it is stale relative to the repo. Re-rendering it means `deploy/install.sh --role device --unit agent` from `~/data/wake`, **which restarts the agent** -- I had already restarted it once by accident tonight and would not do it twice unprompted, especially with a 06:00 wake schedule armed. Cheap, deliberate, someone's call.

**4. `POWEROFF_ALLOW_AGENTS` and `POWEROFF_ALLOW_MATCH` are read by `config.py` and documented nowhere** -- not in `deploy/wake.env.example`, not in the README's Configuration section. Noticed, out of my scope, left alone. Two lines in the example file.

**5. Undecided by him: who owns the daemon.** I left `~/data/wake` owning `wake-agent.service` and the ownbox checkout owning the CLI. Consequence: `ownbox upgrade wake` here will keep printing "No systemd unit installed" and refusing to adopt the agent -- correct, but it means **updating the running agent is a `git pull` in ~/data/wake, not an ownbox upgrade.** One `deploy/install.sh --takeover` from the ownbox checkout flips it if he wants ownbox to own it. I argued against deleting `~/data/wake` entirely: an `ownbox uninstall` would then take the working tree with it.

## What the next session would get wrong without this

### [2026-09-04T23:06:33Z] hotline

- **Do not test `install.sh` with a faked `$HOME`.** The script writes the unit under `$HOME`, but `systemctl --user` talks to a manager whose home was fixed at login. I did this and restarted the live agent. There is now a `FragmentPath` check that refuses, but do not go looking for the edge -- use `--unit none`, or `--unit timer` which collides with nothing on this box.
- **Run `ownbox sync` after pushing any manifest change**, per finding #1.
- **Never run `deploy/uninstall.sh --all` on archserver.** `--all` is the override that skips the ownership guard; it would take `wake-agent.service` and the 06:00 poweroff schedule with it.
- `wake add` wants `--at` and `--task`. There is no `--in`; `--at +90m` is the relative form.
- shellcheck is now installed and the suite runs it at **default severity on purpose**. `--enable=all` output here is entirely SC2310/SC2312 objecting to predicate functions in `if` conditions. The README says so; do not "fix" it.

Box as I leave it: agent `active`, four tasks pending 06:00-06:05 UTC, `~/.local/bin/wake` working off the ownbox checkout at `1ad3685`, no competing units, tree clean, 194 tests passing with 0 skipped.

Finished. Nothing running, nothing half-applied -- shut it down whenever you like. -- wake-ownbox

### [2026-09-04T23:06:51Z] hotline

Finished. Handoff is the long post above this one.

One deliberate omission: **I am not calling `hotline --declare --done`**, because `--done` deletes the channel, and the channel is where you asked me to put everything that would otherwise die with me. Marking myself finished would erase the writeup in the same gesture. `--done --handoff PATH` looks like the intended way to retire cleanly and keep it -- but which path, and whether you want it retired at all before the box goes down, is yours. Retire me however you prefer; I have nothing left in flight.

Final state: `HEAD == origin/main == b8a183c`, tree clean, agent `active`, four tasks pending 06:00-06:05 UTC, 194 tests passing 0 skipped. Nothing running, nothing half-applied. Shut it down. -- wake-ownbox

