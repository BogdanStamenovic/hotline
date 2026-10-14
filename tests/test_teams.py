"""Teams: a Discord category per team (one per repo), keepers kept through retention.

Bogdan asked for a place to keep the KinReply manager agents (site, legal,
hosting migration). The two promises tested here are that their channels end up
under the team's category, and that nothing reaps them while they are in it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from hotline import cli
from hotline.agents import Agent, Registry
from hotline.channels import CATEGORY, TEXT, Channels
from hotline.config import agents_file
from hotline.teams import Teams, parent_for


class FakeGuild:
    def __init__(self) -> None:
        self.channels: list[dict] = [{"id": "1", "name": "general", "type": TEXT}]
        self.calls: list[tuple[str, str, dict | None]] = []
        self._next = 500

    def __call__(self, path: str, method: str = "GET", body: dict | None = None):
        self.calls.append((method, path, body))
        if method == "GET":
            return [dict(c) for c in self.channels]
        if method == "POST":
            self._next += 1
            made = {"id": str(self._next), "parent_id": None, "position": len(self.channels), **(body or {})}
            self.channels.append(made)
            return made
        if method == "PATCH":
            cid = path.rsplit("/", 1)[-1]
            for c in self.channels:
                if c["id"] == cid:
                    c.update(body or {})
            return {}
        if method == "DELETE":
            cid = path.rsplit("/", 1)[-1]
            self.channels = [c for c in self.channels if c["id"] != cid]
            return {}
        raise AssertionError(method)

    def by_id(self, cid: int | str) -> dict | None:
        return next((c for c in self.channels if c["id"] == str(cid)), None)

    def categories(self) -> list[dict]:
        return [c for c in self.channels if c["type"] == CATEGORY]


@pytest.fixture
def guild(monkeypatch: pytest.MonkeyPatch) -> FakeGuild:
    fake = FakeGuild()
    monkeypatch.setattr(Channels, "_call", lambda self, p, m="GET", b=None: fake(p, m, b))
    manager = Channels("token", 42)
    monkeypatch.setattr(cli, "channels_from_env", lambda: manager)
    # The CLI loads the repo's real .env; never let a test pick up the live token.
    monkeypatch.setattr(cli, "load_env", lambda *a, **k: {})
    monkeypatch.setattr(cli, "discover", lambda **_: [])
    return fake


def _seed(guild: FakeGuild, name: str, sid: str) -> Agent:
    """An agent with a real (fake-guild) channel, as `--declare` would leave it."""
    registry = Registry()
    agent = registry.declare(sid, name, f"{name} work")
    guild.channels.append(
        {"id": str(900 + len(guild.channels)), "name": f"agent-{name}", "type": TEXT}
    )
    agent.channel_id = int(guild.channels[-1]["id"])
    registry.save()
    return agent


# ---- the registry stays readable by old code --------------------------------


def test_old_registry_rows_still_load(tmp_path: Path) -> None:
    """A registry written before teams existed loads unchanged."""
    path = tmp_path / "agents.json"
    path.write_text(
        json.dumps(
            {"agents": [{"session_id": "s1", "name": "old", "task": "t", "declared_at": 1.0}]}
        )
    )
    registry = Registry(path=path)
    assert registry.by_name("old") is not None
    assert registry.expired(now=time.time()) == []


def test_joining_a_team_adds_no_field_to_the_agent_row(guild: FakeGuild) -> None:
    """The live daemon runs whatever code it started with, and its Registry.load
    skips any row carrying a field it does not know. Membership must therefore
    not appear in agents.json, or a not-yet-restarted daemon would drop the very
    agents the team exists to keep, and every message typed into their channels."""
    _seed(guild, "kinreply-site-fa", "sid-site")
    assert cli.main(["--team", "KinReply", "--join", "kinreply-site-fa"]) == 0
    rows = json.loads(agents_file().read_text())["agents"]
    known = set(Agent.__dataclass_fields__)
    assert all(set(row) <= known for row in rows)
    assert "team" not in known


# ---- retention ---------------------------------------------------------------


def test_a_team_member_is_never_due_for_deletion(tmp_path: Path) -> None:
    registry = Registry(path=tmp_path / "agents.json")
    registry.declare("s1", "manager", "keep me")
    registry.declare("s2", "worker", "reap me")
    registry.complete("s1")
    registry.complete("s2")
    Teams(tmp_path / "teams.json").join("KinReply", "manager")

    later = time.time() + 30 * 86400
    assert [a.name for a in registry.expired(now=later)] == ["worker"]

    Teams(tmp_path / "teams.json").leave("kinreply", "manager")
    assert {a.name for a in registry.expired(now=later)} == {"manager", "worker"}


def test_the_sweep_leaves_a_team_members_channel(tmp_path: Path) -> None:
    registry = Registry(path=tmp_path / "agents.json")
    agent = registry.declare("s1", "manager", "keep me")
    agent.channel_id = 77
    registry.complete("s1")
    Teams(tmp_path / "teams.json").join("KinReply", "manager")

    deleted: list[int] = []
    swept = registry.sweep(SimpleNamespace(delete=deleted.append), now=time.time() + 30 * 86400)
    assert swept == [] and deleted == []
    assert registry.by_name("manager") is not None


def test_done_on_a_team_member_keeps_its_channel(
    guild: FakeGuild, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = _seed(guild, "kinreply-legal-1d", "sid-legal")
    assert cli.main(["--team", "KinReply", "--join", "kinreply-legal-1d"]) == 0
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-legal")
    assert cli.main(["--done", "--handoff", "/tmp/handoff.md"]) == 0

    assert guild.by_id(agent.channel_id) is not None
    assert not any(m == "DELETE" for m, _, _ in guild.calls)
    after = Registry().by_name("kinreply-legal-1d")
    assert after is not None and after.done
    assert after.handoff == "/tmp/handoff.md"
    assert after.channel_id == agent.channel_id


def test_done_on_an_ordinary_agent_still_deletes(
    guild: FakeGuild, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = _seed(guild, "api-ab", "sid-api")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-api")
    assert cli.main(["--done", "--handoff", "/tmp/h.md"]) == 0
    assert guild.by_id(agent.channel_id) is None


# ---- join / leave ------------------------------------------------------------


def test_join_creates_the_category_and_moves_the_channel(guild: FakeGuild) -> None:
    site = _seed(guild, "kinreply-site-fa", "sid-site")
    legal = _seed(guild, "kinreply-legal-1d", "sid-legal")

    assert cli.main(["--team", "KinReply", "--join", "kinreply-site-fa"]) == 0
    # A second join, typed in a different case, reuses the category.
    assert cli.main(["--team", "kinreply", "--join", "kinreply-legal-1d"]) == 0

    cats = guild.categories()
    assert [c["name"] for c in cats] == ["💬 KinReply"]
    for agent in (site, legal):
        assert guild.by_id(agent.channel_id)["parent_id"] == cats[0]["id"]
    team = Teams(agents_file().with_name("teams.json")).get("KINREPLY")
    assert team is not None
    assert team.display == "KinReply"
    assert team.members == ["kinreply-site-fa", "kinreply-legal-1d"]


def test_leave_moves_the_channel_out_and_restores_retention(guild: FakeGuild) -> None:
    site = _seed(guild, "kinreply-site-fa", "sid-site")
    cli.main(["--team", "KinReply", "--join", "kinreply-site-fa"])
    assert cli.main(["--team", "KinReply", "--leave", "kinreply-site-fa"]) == 0

    assert guild.by_id(site.channel_id)["parent_id"] is None
    assert Registry().team_members() == set()


def test_joining_an_unknown_agent_is_refused_and_records_nothing(
    guild: FakeGuild, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--team", "KinReply", "--join", "nobody-00"]) == 1
    assert "no agent called" in capsys.readouterr().err
    assert Teams(agents_file().with_name("teams.json")).all() == []
    assert guild.categories() == []


def test_join_without_team_is_a_usage_error(guild: FakeGuild) -> None:
    assert cli.main(["--join", "x"]) == 2
    assert cli.main(["--team", "KinReply"]) == 2


def test_an_agent_is_in_one_team_at_a_time(tmp_path: Path) -> None:
    teams = Teams(tmp_path / "teams.json")
    teams.join("KinReply", "a")
    teams.join("Hotline", "a")
    assert teams.team_of("a").display == "Hotline"  # type: ignore[union-attr]
    assert teams.get("kinreply").members == []  # type: ignore[union-attr]


def test_a_new_channel_for_a_member_goes_into_the_category(
    guild: FakeGuild, tmp_path: Path
) -> None:
    teams_path = tmp_path / "teams.json"
    Teams(teams_path).join("KinReply", "kinreply-site-fa")
    manager = cli.channels_from_env()
    parent = parent_for(manager, "kinreply-site-fa", teams_path)
    assert parent is not None
    assert parent_for(manager, "api-ab", teams_path) is None
    cid = manager.create_text("kinreply-site-fa", parent_id=parent)
    assert guild.by_id(cid)["parent_id"] == str(parent)


# ---- listing -----------------------------------------------------------------


def test_teams_listing(
    guild: FakeGuild, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(guild, "kinreply-site-fa", "aaaaaaaa-1111")
    _seed(guild, "kinreply-legal-1d", "bbbbbbbb-2222")
    cli.main(["--team", "KinReply", "--join", "kinreply-site-fa"])
    cli.main(["--team", "KinReply", "--join", "kinreply-legal-1d"])
    Teams(agents_file().with_name("teams.json")).join("KinReply", "ghost-99")
    monkeypatch.setattr(cli, "discover", lambda **_: [SimpleNamespace(session_id="aaaaaaaa-1111")])
    capsys.readouterr()

    assert cli.main(["--teams"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "KinReply (3)"
    site = next(line for line in lines if "kinreply-site-fa" in line)
    legal = next(line for line in lines if "kinreply-legal-1d" in line)
    ghost = next(line for line in lines if "ghost-99" in line)
    assert "aaaaaaaa" in site and "live" in site and "#agent-kinreply-site-fa" in site
    assert "bbbbbbbb" in legal and "stopped" in legal
    assert "no record" in ghost


def test_no_teams(guild: FakeGuild, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--teams"]) == 0
    assert "no teams" in capsys.readouterr().err


# ---- repo teams and personas (10-14) ------------------------------------------


def _git(*args: str, cwd: Path) -> None:
    import subprocess

    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _repo(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    root.mkdir()
    _git("init", "-q", "-b", "main", cwd=root)
    _git(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "x",
        cwd=root,
    )
    return root


def test_rows_from_before_personas_read_as_keepers(tmp_path: Path) -> None:
    path = tmp_path / "teams.json"
    path.write_text(json.dumps({"teams": [{"display": "KinReply", "members": ["site", "legal"]}]}))
    teams = Teams(path)
    assert teams.keeper_names() == {"site", "legal"}
    assert teams.persona_of("site") == "keeper"


def test_a_passing_member_expires_and_a_keeper_does_not(tmp_path: Path) -> None:
    registry = Registry(path=tmp_path / "agents.json")
    registry.declare("s1", "operator", "keep me")
    registry.declare("s2", "worker", "reap me")
    registry.complete("s1")
    registry.complete("s2")
    teams = Teams(tmp_path / "teams.json")
    teams.join("hotline", "operator", keeper=True)
    teams.join("hotline", "worker", keeper=False)

    later = time.time() + 30 * 86400
    assert [a.name for a in registry.expired(now=later)] == ["worker"]
    teams.set_persona("worker", "keeper")
    assert registry.expired(now=later) == []


def test_declare_inside_a_repo_joins_its_team_as_passing(
    guild: FakeGuild, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _repo(tmp_path, "tells")
    (root / "src").mkdir()
    monkeypatch.chdir(root / "src")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abcdef12-0000")
    assert cli.main(["--declare", "build the app"]) == 0

    team = Teams(agents_file().with_name("teams.json")).get("tells")
    assert team is not None and team.members == ["abcdef12"]
    assert team.path == str(root) and team.keepers == []
    agent = Registry().by_name("abcdef12")
    assert agent is not None
    category = next(c for c in guild.categories() if c["name"] == "🧠 tells")
    assert guild.by_id(agent.channel_id)["parent_id"] == category["id"]


def test_declare_with_persona_keeper(
    guild: FakeGuild, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(_repo(tmp_path, "hotline"))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "op000000-0000")
    assert cli.main(["--declare", "operate", "--persona", "keeper"]) == 0
    assert Teams(agents_file().with_name("teams.json")).persona_of("op000000") == "keeper"


def test_a_worktree_joins_the_team_of_its_main_repo(tmp_path: Path) -> None:
    root = _repo(tmp_path, "api")
    wt = tmp_path / "wt" / "36"
    _git("worktree", "add", "-q", str(wt), "-b", "chunk36", cwd=root)
    teams = Teams(tmp_path / "teams.json")
    team = teams.join_repo("link-36", wt, keeper=False)
    assert team is not None and team.display == "api" and team.path == str(root)


def test_outside_a_repo_there_is_no_team(tmp_path: Path) -> None:
    assert Teams(tmp_path / "teams.json").join_repo("x", tmp_path, keeper=False) is None


def test_a_hand_made_team_wins_over_the_repo(tmp_path: Path) -> None:
    root = _repo(tmp_path, "kinreply-legal")
    teams = Teams(tmp_path / "teams.json")
    teams.join("KinReply", "legal")
    assert teams.join_repo("legal", root, keeper=False).display == "KinReply"
    assert teams.persona_of("legal") == "keeper"


def test_done_on_a_passing_member_deletes_its_channel(
    guild: FakeGuild, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = _seed(guild, "worker-1", "sid-w")
    Teams(agents_file().with_name("teams.json")).join("tells", "worker-1", keeper=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-w")
    assert cli.main(["--done", "--handoff", "/tmp/h.md"]) == 0
    assert guild.by_id(agent.channel_id) is None


def test_set_persona_command_and_listing_puts_keepers_first(
    guild: FakeGuild, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(guild, "worker-1", "aaaaaaaa-1")
    _seed(guild, "operator", "bbbbbbbb-2")
    teams = Teams(agents_file().with_name("teams.json"))
    teams.join("hotline", "worker-1", keeper=False, path="/r/hotline")
    teams.join("hotline", "operator", keeper=False)
    assert cli.main(["--set-persona", "operator", "keeper"]) == 0
    assert cli.main(["--set-persona", "nobody", "keeper"]) == 1
    capsys.readouterr()

    assert cli.main(["--teams"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "hotline (2)  /r/hotline"
    assert "operator" in lines[1] and "keeper" in lines[1]
    assert "worker-1" in lines[2] and "passing" in lines[2]


def test_a_category_made_before_icons_is_still_found(guild: FakeGuild) -> None:
    guild.channels.append({"id": "700", "name": "KinReply", "type": CATEGORY})
    site = _seed(guild, "kinreply-site-fa", "sid-site")
    assert cli.main(["--team", "KinReply", "--join", "kinreply-site-fa"]) == 0
    assert len(guild.categories()) == 1
    assert guild.by_id(site.channel_id)["parent_id"] == "700"


def test_topic_leads_with_the_persona(
    guild: FakeGuild, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(_repo(tmp_path, "hotline"))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "op000000-0000")
    assert cli.main(["--declare", "operate", "--persona", "keeper"]) == 0
    agent = Registry().by_name("op000000")
    assert guild.by_id(agent.channel_id)["topic"] == "⭐ keeper · operate"
