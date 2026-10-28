# operator seeds

`seed-common.txt` is the rules block hotline-80 appends to the seed of every research or worker agent it spawns. Before 2026-10-28 it lived in a session scratch directory under /tmp, and a reboot or compaction would have lost it.

Spawn recipe used by the operator:
1. Pre-trust the cwd: set `projects[cwd].hasTrustDialogAccepted=true` in `~/.claude.json`.
2. Spawn: `cd ~/data/hotline; set -a; . ./.env; set +a; .venv/bin/python -c "import asyncio; from hotline import tmuxen; asyncio.run(tmuxen.spawn(NAME, cwd=DIR, name=NAME))"`
3. Wait for the pane to show the prompt, then send the task-specific seed plus this file: `hotline --to NAME --no-wait --warrant <his Discord message URL> "$(cat seed)"`.
