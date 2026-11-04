#!/bin/bash
# Spawn a standalone manager agent that answers directly to Bogdan in its own channel.
# Usage: operator/new-manager.sh NAME DISCORD_MESSAGE_ID "his words"
# Optional env: AGENT_DIR (cwd, default ~/data/NAME), SEED_FILE (replaces the blank-manager seed).
# The message id is his request in #agent-hotline-80; it becomes the warrant.
set -euo pipefail
name=$1 mid=$2 words=$3
dir=${AGENT_DIR:-$HOME/data/$name}
mkdir -p "$dir"
python3 - "$dir" <<'PY'
import json, sys
p = '/home/bodas/.claude.json'; d = json.load(open(p))
d.setdefault('projects', {}).setdefault(sys.argv[1], {})['hasTrustDialogAccepted'] = True
json.dump(d, open(p, 'w'), indent=2)
PY
cd "$HOME/data/hotline"; set -a; . ./.env; set +a
export PATH=$HOME/.claude/bin:$PATH
.venv/bin/python -c "import asyncio, sys; from hotline import tmuxen; asyncio.run(tmuxen.spawn(sys.argv[1], cwd=sys.argv[2], name=sys.argv[1]))" "$name" "$dir"
timeout 60 bash -c "until tmux capture-pane -pt hl-$name | grep -q 'bypass permissions on'; do sleep 2; done"
seed=$(mktemp)
if [ -n "${SEED_FILE:-}" ]; then cp "$SEED_FILE" "$seed"; else
cat > "$seed" <<SEED
From hotline-80 (the operator), on Bogdan's request (Discord $mid, verified): "$words".
You are $name: a standalone manager agent who answers DIRECTLY to Bogdan in your own Discord channel, not to the operator. He will give you your direction there.
1) Register so you get your channel: hotline --declare "$name: new manager for Bogdan, awaiting his direction".
2) Post one short line in your channel that you are ready and ask what he wants (write the text to a file with a QUOTED heredoc, <<'EOF', then hotline-say --file FILE: backticks in a double-quoted shell string EXECUTE).
3) Wait for him. Your CLAUDE.md has his working rules and a "Running as a hotline-native agent" section; read it.
SEED
fi
guild=$(grep ^DISCORD_GUILD_ID .env | cut -d= -f2 | tr -d '"')
hotline --to "$name" --no-wait --warrant "https://discord.com/channels/$guild/1541610683240554527/$mid" "$(cat "$seed")"
rm -f "$seed"
timeout 180 bash -c "until hotline --agents 2>/dev/null | grep -q '^$name '; do sleep 5; done"
hotline --agents | grep "^$name "
