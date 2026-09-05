# #agent-track-sched

Archived 2026-09-05 before `hotline --done` deleted the channel.

### 2026-09-05T10:52:30.119000+00:00 — hotline

Started. Baseline suite: 348 passed, 3.08s. Provenance verified.

Two corrections to the brief before I build on it:

1. `wake --db <path>` does NOT exist -- not on the installed wake, not on the dev checkout. Top-level flags are only -v/-q/--version/--config. So hand-testing needs `--config <wake.env>`; I'm checking now what that actually redirects.

2. GAP 2 needs no wake change at all: the INSTALLED wake already has `--then {poweroff}` on `add`. It's only `--every` that's dev-only. Confirmed by diffing `wake add --help` between /home/bodas/.local/bin/wake and ~/data/wake/.venv/bin/wake. Both report version 0.1.0, so version strings are useless for feature detection -- I'll probe the help text for `--every` instead.

### 2026-09-05T10:59:08.275000+00:00 — hotline

INCIDENT, contained -- report it upward. My Sonnet research agent, testing wake in a scratch DB, wrote a task to the LIVE production wake server (192.168.1.8:8791) and the local wake-agent pulled it straight back into the real DB. It noticed, cancelled the task locally and remotely, and verified. I have independently re-checked: `wake list` shows exactly the 4 stopgap rows, all pending, nothing else. Live DB is clean and your 08:00 run is intact.

Root cause, and this is the correction to the brief's testing advice: `WAKE_DB_PATH` alone is NOT isolation. It overrides only DB_PATH; SERVER_URL and ROLE still come from ~/.config/wake/wake.env, so `wake add` autosyncs to the real server. The safe invocation is BOTH:
    WAKE_CONFIG=/nonexistent WAKE_DB_PATH=/tmp/scratch.db wake add ...
(config.py:63-72, cli.py:207-215). That is worth passing to anyone else touching wake.

Four research findings that changed my design:

1. `--then poweroff` already has a presence guard -- it refuses to shut down if anyone is logged in, attached or ssh'd, and if a foreign agent process is running (power.py:96-184). My main worry about this feature is therefore already solved in wake, not something track has to invent.
2. `--then poweroff` arms the RTC for the next owned task BEFORE shutting down. So a slot at 19:00 still wakes the box after the 08:00 slot powered it off.
3. wake refuses `--every` on the rtcwake backend at add time (cli.py:220-228). My slot code was about to pass it to the resume task. Fixing now -- this would have broken every rtcwake assignment.
4. `--at` does NOT accept a bare '06:00'; only epoch, +N, or full ISO. My code already computes an epoch, so no change, but the brief's '--at 06:00 --every 1d' shorthand does not actually work.

### 2026-09-05T10:59:08.643000+00:00 — hotline

DESIGN, written down as asked. The unit that gets scheduled is a TIME, not an assignment. Assignments sharing a wall-clock time share one wake task, which runs them in sequence via a new `track run --slot HH:MM` and powers off once, after the last. Rationale: a per-assignment poweroff cannot be made safe -- the first to finish kills the rest, and giving each its own minute only narrows the race, since a cycle's duration is however long five Sonnet scouts take. Sequencing becomes structural rather than a convention in track-run-all. A slot powers off only if EVERY member opted in; one member that did not keeps the box up, because an idle box is a cheaper mistake than a box that dies under a run.

Progress: models, store, scheduler (--every probe via help text, --then, absolute wall-clock fire times, OnCalendar systemd fallback), slots.py, engine and most of the CLI are written. Advisor written. Suite is red only where old tests hit the new advisor default -- expected, fixing next.

### 2026-09-05T11:10:05.767000+00:00 — hotline

**track** — "a cheap USB-C hub with at least 4 ports"
8 sources checked · 14 listings seen · 14 new

**Budget** · 1,499.00 RSD — USB Hub Orico YSB4-U2 USB-C/Type-A PD @ Gamecentar
  _Slightly pricier than the AVAX but adds PD pass-through charging on the same 4-port count._
  score 0.58 — **17% under** the 1,799.00 RSD that 1 comparable listing ask · new
  <https://www.gamecentar.rs/usb-hub-orico-ysb4-u2-4-port-usb-3-0-type-c-type-a-pd.html>
**Mid** · 1,725.99 RSD — Ugreen CM806 USB-C hub 4 porta 3.2 (35583) @ ePonuda.com
  _Cheapest confirmed price of the group, with 2x USB-C 3.2 + 2x USB-A 3.2 ports, though the listing page itself was blocked by the site's anti-bot wall so this is a search-snippet price only._
  score 0.50 — _no comparable found; ranked on price alone_
  <https://www.eponuda.com/usb-hub-cene/ugreen-ugreen-cm806-usb-c-hub-4-porta-3-2-35583-cena-34192652>
**Stretch** · 2,099.00 RSD — USB Hub Onten UC120 USB-C SD/TF 6-in-1 Blue @ Gamecentar
  _Adds SD/TF card slots to the port mix, useful if card reading matters more than raw USB count._
  score 0.50 — _no comparable found; ranked on price alone_ · new
  <https://www.gamecentar.rs/usb-hub-onten-uc120-usb-c-sd-tf-6u1-blue.html>

Also new (7), best first:
• **USB HUB USB-C --> 4x USB 3.2, Bus-Powered, 164924** — price unknown @ ePonuda.com
  _Bus-powered 4-port USB 3.2 hub, likely a rebrand of the Manhattan 164924 listing below, but no price surfaced in the search snippet._
  <https://www.eponuda.com/usb-hub-cene/usb-hub-usb-c--4x-usb-3-2-bus-powered-164924-cena-27339719>
• **Manhattan INT MH 4-Port USB 3.2 Gen 1 Hub, 164924** — price unknown @ ePonuda.com
  _Converts 4x USB-A 3.0 to a single USB-C connector, a well-known budget brand, but no price could be confirmed since the listing page returned a 403._

### 2026-09-05T11:10:06.109000+00:00 — hotline

<https://www.eponuda.com/usb-hub-cene/manhattan-int-mh-4-port-usb-3-2-gen-1-hub-164924-usb-hub-cena-942788>
• **Vention USB C Hub sa 4 USB 3.0 porta 0.15m crni** — price unknown @ ePonuda.com
  _Compact 0.15m cable hub with 4 USB 3.0 ports, plug-and-play across USB 1.1/2.0/3.0, but pricing was not visible in the blocked page._
  <https://www.eponuda.com/usb-hub-cene/vention-usb-c-hub-sa-4-usb-3-0-porta-0-15m-crni-cena-5351677>
…and 4 more.

Cheapest sources so far:
  Gamecentar: from 1,199.00 RSD (median 1,949.00 RSD, 6 listings)
  ePonuda.com: from 1,725.99 RSD (median 1,725.99 RSD, 1 listings)

_scouts: ~$0.34 of model usage at list price (no charge on a Claude subscription)_

### 2026-09-05T11:11:46.720000+00:00 — hotline

**track** — "a cheap mechanical keyboard, used is fine"
6 sources checked · 10 listings seen · 10 new

**Budget** · 1,944.00 RSD — Mala Mehanička RGB Tastatura AOAS M880 @ Lalafo Srbija
  _Explicitly branded as mechanical (AOAS M880) in a compact size, one of the few sub-2000 RSD listings that names the switch type in the title._
  score 0.76 — **35% under** the 3,000.00 RSD that 3 comparable listings ask · listed 5d ago
  <https://www.lalafo.rs/belgrade/ads/mala-mehanika-rgb-tastatura-aoas-m880-mala-mehanika-rgb-tastatura-id-96741770>
**Mid** · 2,400.00 RSD — Gaming mehanička tastatura sa RGB pozadinskim osvetljenjem @ Lalafo Srbija
  _First Niš listing in the batch, explicitly titled mechanical with RGB backlighting._
  score 0.65 — **20% under** the 3,000.00 RSD that 3 comparable listings ask · listed 169d ago
  <https://www.lalafo.rs/nis/ads/gaming-mehanicka-tastatura-sa-rgb-pozadinskim-osvetljenjem-id-109647817>
**Stretch** · 2,500.00 RSD — HP GK100F Mechanical Keyboard, with Blue Switch and Mixed Lighting @ Lalafo Srbija
  _Named brand and model (HP GK100F) with blue switches stated explicitly, giving more certainty about the hardware than the generic-branded listings nearby._
  score 0.50 — _no comparable found; ranked on price alone_ · listed 64d ago
  <https://www.lalafo.rs/novi-sad/ads/hp-gk100f-mechanical-keyboard-with-blue-switch-and-mixed-lighting-id-45092945>

Cheapest sources so far:
  Lalafo Srbija: from 500.00 RSD (median 2,195.00 RSD, 10 listings)

_scouts: ~$0.38 of model usage at list price (no charge on a Claude subscription)_

### 2026-09-05T11:14:04.882000+00:00 — hotline

DONE. 69c9a59 pushed and verified on the remote. 420 tests (baseline was 348), ruff and mypy clean.

TESTS -- all five you asked for, plus the two smaller items. You were right that I had shipped d4455c3 with none; that was my next step, not a disagreement.
- next_occurrence: rollover both ways, 00:00, 23:59, and the lead floor
- parse_check_at: normalisation table plus every rejection path
- slots.arm: argv-level assertions, with and without --every, and --every NOT reaching an rtcwake resume task
- feature detection: a fake help text with --every and one without
- the poweroff rule: A wants it, B does not, slot does not power off -- plus a test that a member leaving turns it back ON, since the rule is re-evaluated every arm
I mutation-tested them rather than trusting green: inverting all()->any(), passing --every to rtcwake, hardwiring detection to True, and dropping the lead floor. Each failed exactly its own test and nothing else.

TWO REAL DEFECTS the tests surfaced:
1. test_cli's 'nothing may schedule' fixture only faked schedule_wakeup, but slots go through slots.arm -- so `track add --at` was shelling out to a real `wake add --help` from a unit test. Fixed in the fixture.
2. Bigger: wake's default task timeout is 300s. One research cycle's ceiling is 540s (120 discovery + 240 scout waves + 180 reaper), and twelve real runs on the live DB measured 37-224s. A two-assignment slot at the default would have been KILLED at five minutes with no error saying why. track now passes --timeout, derived from the scouts' own constants and scaled by slot size. Your hand-armed stopgap uses 900s, so you had already hit this.

### 2026-09-05T11:14:05.355000+00:00 — hotline

DST -- I fixed it rather than documenting it away. A slot that fires more than 30 min off its nominal wall-clock time re-anchors to local time, so wake's absolute-epoch recurrence costs one run an hour out, twice a year, instead of six months. Tested, including the short-way-round-midnight case.

--interval / --at are mutually exclusive; --help now says so on both.

ONE THING I ADDED that you did not ask for, because without it the feature was unusable: check_at was only settable at `add`, so his two live assignments (14 and 9 runs, 306 findings) could not join a slot without being deleted and recreated. `track reschedule` fixes that. It routes through the same _resolve_cadence as add, so the cadence rules cannot drift.

VERIFIED LIVE, not just in tests:
- advisor against real claude -p: 4.3s, $0.03, answered 20:30 for a Serbian GPU hunt and named KupujemProdajem and the evening posting pattern as the reason. That is an auditable answer, not a generic one.
- full slot run, firing the exact task string wake would fire: two assignments in sequence, both posted, 2:44 total, exit 0, then re-armed itself to one row for tomorrow with then_do=poweroff intact.
- ownbox copy of track updated to 69c9a59 -- it was on 3d54f31, so a slot task would have hit a track that rejects --slot.
- migration on the live track.db exercised and backed up first; both live assignments intact on the unchanged interval path.
- I dry-ran the actual handover on a COPY of his live DB: both assignments into one 08:00 slot, history preserved, one wake task, timeout 1080s.

I have NOT touched the live wake DB. Your four stopgap rows are pending and untouched. When you want to retire them, this is the whole handover:

  track reschedule 10ee961f --at 08:00 --then-poweroff
  track reschedule e400d473 --at 08:00 --then-poweroff

### 2026-09-05T11:14:05.643000+00:00 — hotline

That arms ONE task, track-slot-0800, replacing all four stopgaps and track-run-all. Do not run it while the stopgaps are armed or tomorrow runs twice.

TWO THINGS ONLY BOGDAN OR YOU CAN DECIDE:
1. The 08:00 slot will now power the box off after the morning run. wake guards it (refuses while anyone is logged in or ssh'd), but confirm that is wanted before arming.
2. track-run-all is superseded by `track run --slot`. I have not deleted it -- it is still what the stopgaps call. Yours to remove once the slot is armed.

