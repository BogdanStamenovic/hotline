"""hotline-kb files an article once, as a forum post, and finds it again."""

from __future__ import annotations

from pathlib import Path

from hotline.channels import Channels
from hotline.kb import KnowledgeBase, _read_index, _search


class FakeForum:
    def __init__(self) -> None:
        self.forum = {"id": "7", "type": 15, "available_tags": [{"id": "t1", "name": "research"}]}
        self.posts: list[dict] = []

    def __call__(self, path: str, method: str = "GET", body=None):
        if path == "/channels/7" and method == "GET":
            return self.forum
        if path == "/channels/7" and method == "PATCH":
            tags = []
            for i, t in enumerate(body["available_tags"]):
                tags.append({"id": t.get("id", f"n{i}"), "name": t["name"]})
            self.forum["available_tags"] = tags
            return self.forum
        if path == "/channels/7/threads" and method == "POST":
            self.posts.append(body)
            return {"id": str(100 + len(self.posts))}
        raise AssertionError((method, path))


def test_files_once_with_tags_and_finds_it(monkeypatch, tmp_path: Path) -> None:
    fake = FakeForum()
    monkeypatch.setattr(Channels, "_call", lambda self, p, m="GET", b=None: fake(p, m, b))
    kb = KnowledgeBase(Channels("t", 42), 7, index=tmp_path / "kb.json")

    created, link = kb.add(
        "Halo dossier",
        "https://claude.ai/artifact/H",
        "BLE only.",
        ["research", "hardware"],
        "halo-36",
    )
    again, same = kb.add("Halo dossier (dup)", "https://claude.ai/artifact/H", "x", [], "y")

    assert created and not again and link == same == "https://discord.com/channels/42/101"
    assert len(fake.posts) == 1
    assert fake.posts[0]["applied_tags"] == ["t1", "n1"]
    assert "https://claude.ai/artifact/H" in fake.posts[0]["message"]["content"]
    index = _read_index(tmp_path / "kb.json")
    assert [h["title"] for h in _search(index, "halo ble")] == ["Halo dossier"]
    assert _search(index, "kinreply") == []
