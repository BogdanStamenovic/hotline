#!/usr/bin/env python3
"""Rebuild a deleted agent channel's thread from that agent's session transcript.

Usage: operator/rebuild-channel.py SESSION_ID NAME > OUT.md

A Discord channel delete takes its scrollback, but the agent's .jsonl transcript
still holds both sides: every message relayed to it from Bogdan (kind=human
provenance), and every post it made with hotline-say (the quoted-heredoc body).
Written 2026-11-27 after `hotline --done` (now removed) deleted #agent-trial2-7f.

Not recovered: direct replies to a relayed message that hotline mirrored on its
own (they never went through hotline-say), and file attachments.
"""
import glob
import json
import os
import re
import sys

sid, name = sys.argv[1], sys.argv[2]
paths = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{sid}.jsonl"))
if not paths:
    sys.exit(f"no transcript for session {sid}")
print(f"# #agent-{name}, rebuilt from the session transcript\n")
print(f"Rebuilt from session {sid}: messages relayed from Discord and posts made with hotline-say. Times are UTC.\n")
n = 0
first = True
for line in open(paths[0]):
    try:
        e = json.loads(line)
    except ValueError:
        continue
    ts = (e.get("timestamp") or "")[:19].replace("T", " ")
    c = (e.get("message") or {}).get("content")
    if e.get("type") == "user":
        texts = [c] if isinstance(c, str) else [x.get("text", "") for x in (c or []) if isinstance(x, dict) and x.get("type") == "text"]
        for t in texts:
            if "hotline-provenance" in t and '"kind":"human"' in t:
                body = t.split("--- message follows ---", 1)[-1].split("This came from another Claude session")[0].strip()
                print(f"**{ts} Bogdan:**\n\n{body}\n")
                n += 1
            elif first and t.strip() and not t.startswith("<") and "hotline-provenance" not in t:
                print(f"**{ts} (seed / first prompt):**\n\n{t[:1500]}\n")
                first = False
                n += 1
    elif e.get("type") == "assistant" and isinstance(c, list):
        for x in c:
            if x.get("type") == "tool_use" and x.get("name") == "Bash":
                cmd = x["input"].get("command", "")
                if "hotline-say" not in cmd:
                    continue
                for _, body in re.findall(r"<<'?(\w+)'?\n(.*?)\n\1\b", cmd, re.S):
                    if "->" in body[:60]:  # a peer message composed in the same call, not a post
                        continue
                    print(f"**{ts} {name}:**\n\n{body.strip()}\n")
                    n += 1
print(f"\n_{n} entries._")
