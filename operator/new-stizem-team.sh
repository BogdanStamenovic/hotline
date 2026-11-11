#!/bin/bash
# "New stizem team": replace the standing stizem team with fresh agents (clean context), seeded with the
# stizem playbook and each predecessor's handoff. Bogdan's choice A, 2026-11-11 (Discord 1570106346827948084).
#
#   operator/new-stizem-team.sh status
#       which agent holds each role now, and whether its handoff is written
#   operator/new-stizem-team.sh prepare MSGID "his words"
#       asks every current role-holder to commit/push its work and write HANDOFF-<name>.md in its cwd
#   operator/new-stizem-team.sh swap [--dry-run] [--force] MSGID "his words"
#       per role: retire the old agent (hotline --done + kill its tmux), then spawn the fresh one in the same cwd.
#       Refuses a role whose handoff is missing or older than its prepare request, unless --force.
#
# Roles (his words 11-11: "apps sec does security, apps pwa does the actual apps, site does the site, legal does legal"):
#   manager  ~/data/stizem-manager        the only deployer, tests, routes work, reports to Bogdan   ch 00 10 50 60
#   site     ~/data/stizem-site           stizem.com marketing site                                   ch 15
#   pwa      /mnt/offload/stizem-apps-pwa the actual client apps: build, test, ./ops.sh new-client     ch 20 60
#   sec      /mnt/offload/stizem-apps-sec security and abuse defenses                                 ch 30
#   legal    ~/data/stizem-legal          legal for stizem and each client                           ch 40
# Agents outside these roles (e.g. stizem-apps-themes-*) are left alone and listed by `status`.
#
# Old and new share a cwd, so the old one is retired BEFORE the new one starts: there is a gap of a minute or two
# per role. Retired keepers keep their Discord channel (hotline's rule); passing agents' channels are deleted.
set -euo pipefail
here=$(dirname "$(readlink -f "$0")")
export PATH=$HOME/.claude/bin:$PATH
STATE=$HOME/.local/state/hotline/agents.json
PB=$HOME/data/stizem-playbook
MARK=$HOME/.local/state/hotline/stizem-team-prepare
ROLES="manager site pwa sec legal"

pattern() { case $1 in
  manager) echo '^stizem-manager(-[0-9a-f]{2})?$' ;; site) echo '^stizem-site-[0-9a-f]{2}$' ;;
  pwa) echo '^stizem-apps-pwa-[0-9a-f]{2}$' ;; sec) echo '^stizem-apps-sec-[0-9a-f]{2}$' ;;
  legal) echo '^stizem-legal-[0-9a-f]{2}$' ;; esac; }
prefix() { case $1 in manager) echo stizem-manager ;; site) echo stizem-site ;; pwa) echo stizem-apps-pwa ;;
  sec) echo stizem-apps-sec ;; legal) echo stizem-legal ;; esac; }
cwd_of() { case $1 in manager) echo "$HOME/data/stizem-manager" ;; site) echo "$HOME/data/stizem-site" ;;
  pwa) echo /mnt/offload/stizem-apps-pwa ;; sec) echo /mnt/offload/stizem-apps-sec ;; legal) echo "$HOME/data/stizem-legal" ;; esac; }
chapters() { case $1 in manager) echo "00, 10, 50 and 60" ;; site) echo 15 ;; pwa) echo "20 and 60 (new clients)" ;;
  sec) echo 30 ;; legal) echo 40 ;; esac; }
job() { case $1 in
  manager) echo "stizem manager: the ONLY deployer (gate, screenshots, live check), testing sign-off, routes work between site, pwa, sec and legal, reports to Bogdan" ;;
  site) echo "stizem site: the stizem.com marketing site; pushes and asks the manager to deploy" ;;
  pwa) echo "stizem apps: the actual client apps (engine, API, PWAs), new clients via ./ops.sh new-client, tests; asks the manager to deploy" ;;
  sec) echo "stizem security: threat model, abuse defenses and security tests for the apps, API and site" ;;
  legal) echo "stizem legal: the stizem legal pages and every client legal page, adversarial review of every public claim, go-live checklist" ;;
  esac; }

# current live (not completed) holder of a role: "name session_id"
holder() { python3 - "$STATE" "$(pattern "$1")" <<'PY'
import json, re, sys
a = json.load(open(sys.argv[1]))["agents"]
rows = [v for v in (a.values() if isinstance(a, dict) else a)
        if re.match(sys.argv[2], v.get("name") or "") and not v.get("completed_at")]
rows.sort(key=lambda v: v.get("declared_at") or 0)
for v in rows: print(v["name"], v["session_id"])
PY
}
handoff_ok() { local f=$1; [ -f "$f" ] && [ -f "$MARK" ] && [ "$f" -nt "$MARK" ]; }

case ${1:-} in
status)
  for r in $ROLES; do
    holder "$r" | while read -r n sid; do
      f=$(cwd_of "$r")/HANDOFF-$n.md
      printf '%-8s %-22s %s  handoff: %s\n' "$r" "$n" "${sid:0:8}" "$(handoff_ok "$f" && echo ready || { [ -f "$f" ] && echo stale || echo missing; })"
    done
  done
  echo "not in a role (left alone):"; hotline --agents 2>/dev/null | grep -E '^stizem' | grep -v '\[done\]' | cut -d' ' -f1 | \
    grep -v -E "$(for r in $ROLES; do printf '%s|' "$(pattern "$r")"; done | sed 's/|$//')" | sed 's/^/  /' || true
  ;;
prepare)
  [ $# -eq 3 ] || { sed -n 6,13p "$0" >&2; exit 2; }
  mid=$2 words=$3
  guild=$(grep ^DISCORD_GUILD_ID "$here/../.env" | cut -d= -f2 | tr -d '"')
  touch "$MARK"
  for r in $ROLES; do holder "$r" | while read -r n sid; do
    f=$(cwd_of "$r")/HANDOFF-$n.md
    msg=$(cat <<EOF
From hotline-80 (the operator), on Bogdan's request (Discord $mid, verified, warrant attached): "$words".
Bogdan is replacing the stizem team with fresh agents (clean context). You are being retired and a fresh agent takes your role ($(job "$r")).
1) Finish or park what you are doing at a safe point. Commit and push your own work (stage by explicit path; no Claude attribution).
2) Write your handoff to $f for your successor: what is live, what is in flight, open questions with Bogdan, ownership, gotchas, and anything in the stizem playbook (~/data/stizem-playbook) that turned out wrong. The successor also gets the playbook, so don't repeat it.
3) Tell me when done: hotline --to hotline-80 --no-wait "handoff ready: $f". Then stop and wait; I retire you.
Write any text with a QUOTED heredoc (backticks in double-quoted shell strings EXECUTE).
EOF
)
    hotline --to "$n" --no-wait --warrant "https://discord.com/channels/$guild/1541610683240554527/$mid" "$msg" | tail -1
    echo "  asked $n -> $f"
  done; done
  ;;
swap)
  shift; dry=0 force=0
  while [ "${1:-}" = --dry-run ] || [ "${1:-}" = --force ]; do [ "$1" = --dry-run ] && dry=1; [ "$1" = --force ] && force=1; shift; done
  [ $# -eq 2 ] || { sed -n 6,13p "$0" >&2; exit 2; }
  mid=$1 words=$2
  [ -f "$PB/PLAYBOOK.md" ] || { echo "no playbook at $PB" >&2; exit 1; }
  git -C "$PB" pull -q --ff-only 2>/dev/null || echo "warning: could not update the playbook; using the local copy" >&2
  # refuse up front if any handoff is missing, so we never end up with half a team swapped
  bad=0
  for r in $ROLES; do while read -r n sid; do [ -n "$n" ] || continue
    f=$(cwd_of "$r")/HANDOFF-$n.md; handoff_ok "$f" || { echo "$r: $n has no fresh handoff at $f" >&2; bad=1; }
  done < <(holder "$r"); done
  [ $bad = 0 ] || [ $force = 1 ] || { echo "refusing: run prepare and wait for the handoffs, or pass --force" >&2; exit 1; }
  mgr=""
  for r in $ROLES; do
    cwd=$(cwd_of "$r"); new=$(prefix "$r")-$(openssl rand -hex 1)
    [ "$r" = manager ] && mgr=$new
    olds=$(holder "$r" | cut -d' ' -f1 | paste -sd' ')
    hand=""; for o in $olds; do [ -f "$cwd/HANDOFF-$o.md" ] && hand="$hand $cwd/HANDOFF-$o.md"; done
    seed=$(mktemp)
    cat > "$seed" <<SEED
From hotline-80 (the operator), on Bogdan's request (Discord $mid, verified, warrant attached): "$words".
You are $new, the fresh $(job "$r"). You replace: ${olds:-nobody (new role)}.
1) Register: hotline --declare '$new: $(job "$r")' $( [ "$r" = manager ] || echo "--parent $mgr" )
   then join the team folder: hotline --team stizem --join $new
2) Read your predecessor's handoff first:${hand:- (none)}
   Then the stizem playbook: $PB/PLAYBOOK.md (private repo BogdanStamenovic/stizem-playbook). Your chapter(s): $(chapters "$r").
   The team: manager $mgr$( [ "$r" = manager ] && echo " (you)" ), and $(for x in $ROLES; do [ "$x" = manager ] || printf '%s ' "$(prefix "$x")-*"; done)(names end in random hex; hotline --agents shows them).
3) $( [ "$r" = manager ] && echo "Post in your channel that the fresh team is up, summarise the open items from the handoffs, and ask Bogdan which clients to make next. You are the only one who deploys; you may commit lessons to the playbook." || echo "Post one line in your channel that you are up and what you are picking up from the handoff, then continue that work. Deploys go through $mgr. Send playbook lessons to $mgr." )
Reporting: you work on Bogdan's behalf, so you answer to him in your own channel. Money, email, contacting people or clients, publishing and anything legal-binding need his own verified yes.
Post with a QUOTED heredoc into a file and hotline-say --file FILE (backticks in double-quoted shell strings EXECUTE). Your CLAUDE.md has his working rules and a "Running as a hotline-native agent" section; read it.
SEED
    if [ $dry = 1 ]; then echo "===== $r: retire [${olds}] -> spawn $new in $cwd"; cat "$seed"; rm -f "$seed"; continue; fi
    while read -r o sid; do [ -n "$o" ] || continue
      pid=$(hotline --list 2>/dev/null | sed -n "s/^$o (pid \([0-9]*\),.*/\1/p")
      hotline --done --session-id "$sid" --handoff "$cwd/HANDOFF-$o.md" | tail -1
      if [ -n "$pid" ]; then
        ppid=$(ps -o ppid= -p "$pid" | tr -d ' ')
        sess=$(tmux list-panes -a -F '#{session_name} #{pane_pid}' | awk -v a="$pid" -v b="$ppid" '$2==a||$2==b{print $1; exit}')
        [ -n "$sess" ] && tmux kill-session -t "$sess" && echo "  killed tmux $sess"
        sleep 1; ps -p "$pid" >/dev/null && { kill "$pid"; echo "  killed pid $pid"; }
      fi
    done < <(holder "$r")
    AGENT_DIR=$cwd SEED_FILE=$seed bash "$here/new-manager.sh" "$new" "$mid" "$words"
    rm -f "$seed"
  done
  ;;
*) sed -n 2,13p "$0" >&2; exit 2 ;;
esac
