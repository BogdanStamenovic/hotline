"""The once-per-agent "make it a keeper?" question after an agent falls asleep."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from hotline import keeper
from hotline.agents import Registry

NOW = 2_000_000_000.0


class FakeChannels:
    def __init__(self) -> None:
        self.sent: list[tuple[int, dict[str, Any]]] = []

    def send(self, channel_id: int, body: dict[str, Any]) -> dict[str, Any]:
        self.sent.append((channel_id, body))
        return {"id": "555"}


def _agent(sid: str, name: str, channel: int | None = 101) -> None:
    reg = Registry()
    a = reg.declare(sid, name, "x")
    a.channel_id = channel
    reg.save()


def _due(seen: dict[str, float], live: set[str] | None = None) -> list[str]:
    reg = Registry()
    return sorted(a.name for a in keeper.due(reg, live or set(), seen, keeper.prompts_beside(reg.path), now=NOW))


def test_asks_only_about_agents_that_just_fell_asleep() -> None:
    _agent("s1", "fresh")
    _agent("s2", "stale")
    _agent("s3", "awake")
    _agent("s4", "no-channel", channel=None)
    seen = {"fresh": NOW - 600, "stale": NOW - 3 * 86400, "awake": NOW, "no-channel": NOW - 60}
    assert _due(seen, live={"s3"}) == ["fresh"]


def test_never_asks_about_the_operator_keepers_or_the_finished() -> None:
    _agent("s1", "hotline-80")
    _agent("s2", "kept-one")
    _agent("s3", "finished")
    reg = Registry()
    reg.agents["s1"].authority = "sys-admin"
    reg.save()
    reg.complete("s3")
    keeper.teams_beside(reg.path).join("hotline", "kept-one", keeper=True)
    seen = {n: NOW - 60 for n in ("hotline-80", "kept-one", "finished")}
    assert _due(seen) == []


def test_asked_once_and_no_means_never_again() -> None:
    _agent("s1", "data-a")
    reg = Registry()
    prompts = keeper.prompts_beside(reg.path)
    fake = FakeChannels()
    [agent] = keeper.due(reg, set(), {"data-a": NOW - 60}, prompts, now=NOW)
    assert keeper.ask(fake, agent, prompts) == 555
    channel, body = fake.sent[0]
    assert channel == 101
    ids = [b["custom_id"] for b in body["components"][0]["components"]]
    assert ids == ["keeper:yes:data-a", "keeper:no:data-a"]
    assert "make this a keeper" in body["content"]
    # Asked: not asked again, even after it wakes and sleeps once more.
    assert _due({"data-a": NOW - 30}) == []
    prompts.set("data-a", "declined")
    assert prompts.state("data-a") == "declined"
    assert _due({"data-a": NOW - 30}) == []


def test_the_typed_phrase() -> None:
    for yes in ("make this a keeper", "Make it a keeper.", "make this agent a keeper!", "  MAKE THIS A KEEPER  "):
        assert keeper.MAKE_KEEPER.match(yes), yes
    for no in ("should I make this a keeper?", "make this a keeper and also fix the bug", "keeper"):
        assert not keeper.MAKE_KEEPER.match(no), no


def test_keeper_of_its_repo_team(tmp_path: Path) -> None:
    repo = tmp_path / "widget"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _agent("s1", "data-r")
    reg = Registry()
    team = keeper.make_keeper(reg.by_name("data-r"), str(repo), reg.path)
    assert team.lower() == "widget"
    assert "data-r" in keeper.teams_beside(reg.path).keeper_names()


def test_an_agent_outside_any_repo_joins_the_shared_keepers_team(tmp_path: Path) -> None:
    _agent("s1", "data-c1")
    reg = Registry()
    team = keeper.make_keeper(reg.by_name("data-c1"), str(tmp_path), reg.path)
    assert team == keeper.KEEPERS_TEAM
    assert "data-c1" in keeper.teams_beside(reg.path).keeper_names()
    # Which is what retention reads: a keeper is not expired.
    assert "data-c1" in reg.team_members()


def test_a_passing_team_member_is_promoted_in_place() -> None:
    _agent("s1", "worker")
    reg = Registry()
    keeper.teams_beside(reg.path).join("kinreply", "worker", keeper=False)
    assert keeper.make_keeper(reg.by_name("worker"), None, reg.path).lower() == "kinreply"
    assert "worker" in keeper.teams_beside(reg.path).keeper_names()
