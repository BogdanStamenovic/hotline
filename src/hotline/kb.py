"""hotline-kb: file finished research into the Discord knowledge base.

The knowledge base is a Discord *forum* channel (#knowledge-base): one post per
article, each with a title, tags and the link, and a search box over all of them
in the Discord client. Bogdan asked for it on 10-14 so that research results --
mostly claude.ai artifact links that used to live only in an agent's channel,
and died with it -- have one permanent, searchable home.

What belongs here: research that is finished and will not keep changing, and
that someone will want again (a dossier, a benchmark verdict, a decision
record). What does not: progress reports, drafts, anything an agent is still
iterating on. A KB post is a pointer plus a summary, not the content itself.

A local index (`kb.json` in the state dir) maps each URL to its post, so adding
the same link twice points at the existing post instead of duplicating it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .channels import Channels
from .config import load_env, state_dir
from .errors import HotlineError

FORUM_ENV = "HOTLINE_KB_FORUM_ID"
MAX_TAGS = 20  # Discord's limit per forum


def index_file() -> Path:
    return state_dir() / "kb.json"


def _read_index(path: Path) -> dict[str, dict[str, Any]]:
    try:
        return dict(json.loads(path.read_text()))
    except (OSError, ValueError):
        return {}


def _write_index(path: Path, index: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, indent=1, ensure_ascii=False))
    os.replace(tmp, path)


def post_body(title: str, url: str, summary: str, source: str, when: str) -> str:
    return f"**{title}**\n{summary.strip()}\n\n🔗 {url}\n-# filed by {source} · {when}"


class KnowledgeBase:
    def __init__(self, manager: Channels, forum_id: int, index: Path | None = None) -> None:
        self.manager = manager
        self.forum_id = forum_id
        self.index_path = index or index_file()

    def _forum(self) -> dict[str, Any]:
        forum = self.manager._call(f"/channels/{self.forum_id}")
        if not isinstance(forum, dict) or forum.get("type") != 15:
            raise HotlineError(f"channel {self.forum_id} is not a forum channel")
        return forum

    def tag_ids(self, names: list[str], create: bool = True) -> list[str]:
        """Forum tag ids for `names`, creating missing tags while there is room."""
        if not names:
            return []
        forum = self._forum()
        tags = list(forum.get("available_tags") or [])
        by_name = {t["name"].casefold(): t for t in tags}
        missing = [n for n in names if n.casefold() not in by_name]
        if missing and create:
            room = MAX_TAGS - len(tags)
            new = [{"name": n[:20]} for n in missing[:room]]
            if new:
                kept = [{"id": t["id"], "name": t["name"]} for t in tags]
                updated = self.manager._call(
                    f"/channels/{self.forum_id}", "PATCH", {"available_tags": kept + new}
                )
                by_name = {t["name"].casefold(): t for t in updated.get("available_tags", [])}
        # Discord allows at most 5 tags on one post.
        return [by_name[n.casefold()]["id"] for n in names if n.casefold() in by_name][:5]

    def add(
        self, title: str, url: str, summary: str, tags: list[str], source: str
    ) -> tuple[bool, str]:
        """File one article. Returns (created, link to the post)."""
        index = _read_index(self.index_path)
        if url in index:
            return False, index[url]["link"]
        guild = self.manager.guild_id
        body = post_body(title, url, summary, source, time.strftime("%Y-%m-%d"))
        thread = self.manager._call(
            f"/channels/{self.forum_id}/threads",
            "POST",
            {
                "name": title[:100],
                "message": {"content": body[:2000]},
                "applied_tags": self.tag_ids(tags),
            },
        )
        link = f"https://discord.com/channels/{guild}/{thread['id']}"
        index[url] = {
            "title": title,
            "summary": summary,
            "tags": tags,
            "source": source,
            "thread_id": thread["id"],
            "link": link,
            "added": time.time(),
        }
        _write_index(self.index_path, index)
        return True, link


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hotline-kb",
        description="File finished research into the Discord knowledge base (a forum channel).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="file one article (a no-op if its URL is already filed)")
    add.add_argument("--title", required=True, help="what a reader would search for")
    add.add_argument("--url", required=True, help="the artifact (or other) link")
    add.add_argument("--summary", required=True, help="1-3 sentences: what it found")
    add.add_argument("--tags", default="", help="comma-separated, e.g. research,kinreply")
    add.add_argument("--source", default=None, help="who made it (default: $HOTLINE_AGENT)")
    add.add_argument("--dry-run", action="store_true", help="print the post, file nothing")
    find = sub.add_parser("search", help="search filed articles by words")
    find.add_argument("query", nargs="+")
    sub.add_parser("list", help="every filed article")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    load_env()
    if args.command in ("list", "search"):
        kb_index = _read_index(index_file())
        if args.command == "list":
            rows = [{"url": u, **e} for u, e in kb_index.items()]
        else:
            rows = _search(kb_index, " ".join(args.query))
        for row in sorted(rows, key=lambda r: r.get("added", 0), reverse=True):
            print(
                f"{row['title']}  [{', '.join(row.get('tags', []))}]\n  {row['url']}\n  {row['link']}"
            )
        if not rows:
            print(
                "nothing filed matches" if args.command == "search" else "knowledge base is empty"
            )
        return 0

    source = args.source or os.environ.get("HOTLINE_AGENT") or "unknown"
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    if args.dry_run:
        print(f"[{args.title}] tags={tags}")
        print(post_body(args.title, args.url, args.summary, source, time.strftime("%Y-%m-%d")))
        return 0
    token = os.environ.get("HOTLINE_BOT_TOKEN")
    guild = os.environ.get("DISCORD_GUILD_ID")
    forum = os.environ.get(FORUM_ENV)
    if not (token and guild and forum):
        print(
            f"hotline-kb: HOTLINE_BOT_TOKEN, DISCORD_GUILD_ID and {FORUM_ENV} must be set",
            file=sys.stderr,
        )
        return 1
    kb = KnowledgeBase(Channels(token, int(guild)), int(forum))
    try:
        created, link = kb.add(args.title, args.url, args.summary, tags, source)
    except HotlineError as exc:
        print(f"hotline-kb: {exc}", file=sys.stderr)
        return 1
    print(("filed: " if created else "already filed: ") + link)
    return 0


def _search(index: dict[str, dict[str, Any]], query: str) -> list[dict[str, Any]]:
    words = query.casefold().split()
    hits = []
    for url, entry in index.items():
        hay = " ".join(
            [entry.get("title", ""), entry.get("summary", ""), url, *entry.get("tags", [])]
        ).casefold()
        if all(w in hay for w in words):
            hits.append({"url": url, **entry})
    return hits


if __name__ == "__main__":
    raise SystemExit(main())
