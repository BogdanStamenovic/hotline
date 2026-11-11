#!/bin/bash
# Spawn a full product team (the "new stizem team" workflow) seeded with the stizem playbook.
# Usage: operator/new-team.sh [--dry-run] [--roles "manager site apps security legal"] TEAM DISCORD_MESSAGE_ID "his words"
#   TEAM: short lowercase name; agents become TEAM-manager, TEAM-site, TEAM-apps, TEAM-security, TEAM-legal
#   DISCORD_MESSAGE_ID: his request in #agent-hotline-80; it becomes every agent's warrant.
# Roles and order come from stizem-manager's playbook report (2026-11-11): the manager is spawned first, and
# nothing goes public before it has a name, domain and owner. The specialists wait for the manager to hand them
# the business details, so spawning them all at once costs nothing while they idle (hotline puts idle agents to sleep).
# --roles "templates" adds the optional templates agent to an existing team.
set -euo pipefail
dry=0 roles="manager site apps security legal"
while [ $# -gt 0 ]; do case $1 in
  --dry-run) dry=1; shift ;;
  --roles) roles=$2; shift 2 ;;
  *) break ;;
esac; done
[ $# -eq 3 ] || { sed -n 3,6p "$0" >&2; exit 2; }
team=$1 mid=$2 words=$3
[[ $team =~ ^[a-z][a-z0-9-]{1,20}$ ]] || { echo "team name must be lowercase letters, digits, dashes" >&2; exit 2; }
PB=$HOME/data/stizem-playbook
[ -f "$PB/PLAYBOOK.md" ] || { echo "no playbook at $PB" >&2; exit 1; }
git -C "$PB" pull -q --ff-only 2>/dev/null || echo "warning: could not update the playbook; using the local copy" >&2
here=$(dirname "$(readlink -f "$0")")
export PATH=$HOME/.claude/bin:$PATH

chapters() { case $1 in
  manager) echo "00, 10, 50 and 60" ;; site) echo "15" ;; apps) echo "20" ;;
  security) echo "30" ;; legal) echo "40" ;; templates) echo "20 (and 15 for the look)" ;;
  *) echo "unknown role $1" >&2; exit 2 ;; esac; }
job() { case $1 in
  manager) echo "manager of the $team team: registration and accounts with the human owner, infra/DNS/mail, the ONLY deployer (gate, screenshots, live check), routes work between the specialists, reports to Bogdan" ;;
  site) echo "$team website: marketing site, copy, screenshots; pushes and asks $team-manager to deploy" ;;
  apps) echo "$team apps: engine, API, client apps, tests, hosting config; asks $team-manager to deploy" ;;
  security) echo "$team security: threat model, abuse defenses, security tests" ;;
  legal) echo "$team legal: every legal document, adversarial review of every public claim, the go-live checklist, decision memos for the owner" ;;
  templates) echo "$team templates: copy the reference app to other visual themes" ;;
  esac; }
start() { case $1 in
  manager) echo "Read PLAYBOOK.md fully, then post in your channel that the team is up and ask Bogdan for the day-one inputs listed in chapter 00/10: the idea, target customer, price, who legally owns it (registration numbers if it exists), the domain, and the spend allowed without asking (default: nothing). The specialists ($team-site, $team-apps, $team-security, $team-legal) are already spawned and waiting for you. Order from the playbook: once a name and domain exist, hand site, apps and legal the details with hotline --to (carry his message link as --warrant); tell security when apps has a skeleton. If he asks for the optional templates agent: operator/new-team.sh --roles templates $team MSGID WORDS, or ask hotline-80." ;;
  security) echo "Read PLAYBOOK.md and your chapter, post one line that you are ready, then WAIT: start when $team-manager tells you apps has a skeleton." ;;
  *) echo "Read PLAYBOOK.md and your chapter, post one line that you are ready, then WAIT for $team-manager to hand you the business details. Do not start building before that." ;;
  esac; }

for role in $roles; do
  name=$team-$role
  seed=$(mktemp)
  cat > "$seed" <<SEED
From hotline-80 (the operator), on Bogdan's request (Discord $mid, verified, warrant attached): "$words".
You are $name, part of a new product team built from the stizem playbook. Your job: $(job "$role").
1) Register: hotline --declare '$name: $(job "$role")' $( [ "$role" = manager ] || echo "--parent $team-manager" )
   then join the team folder: hotline --team $team --join $name
2) The playbook is $PB/PLAYBOOK.md (private repo BogdanStamenovic/stizem-playbook; chapters in $PB/chapters/). Yours: chapter(s) $(chapters "$role"). It is written from stizem, the one worked example: follow the steps, not stizem's specifics. $( [ "$role" = manager ] && echo "You may commit improvements to it (lessons from this team), with a clear message." || echo "Don't edit it; send lessons to $team-manager, who may commit improvements." )
3) $(start "$role")
Reporting: you work on Bogdan's behalf, so you answer to him in your own channel; $( [ "$role" = manager ] && echo "you route work between the specialists." || echo "coordinate with the rest of the team through $team-manager." ) Money, email, contacting people, publishing, and anything legal-binding need Bogdan's own verified yes.
Post with a QUOTED heredoc into a file and hotline-say --file FILE (backticks in double-quoted shell strings EXECUTE). Your CLAUDE.md has his working rules and a "Running as a hotline-native agent" section; read it.
SEED
  if [ $dry = 1 ]; then echo "===== $name (dir ~/data/$team/$role)"; cat "$seed"; rm -f "$seed"; continue; fi
  [ -e "$HOME/data/$team/$role" ] && hotline --agents 2>/dev/null | grep -q "^$name " && { echo "$name already exists, skipped" >&2; rm -f "$seed"; continue; }
  AGENT_DIR=$HOME/data/$team/$role SEED_FILE=$seed bash "$here/new-manager.sh" "$name" "$mid" "$words"
  rm -f "$seed"
done
