"""The agent lifecycle: declare, retask, done, expire.

Deliberately has nothing to do with Discord. The registry decides *what* should
exist and what is due for deletion; actually creating and deleting channels is a
separate job, which is what makes all of this testable without a guild -- and it
had to be, because the bot did not have `MANAGE_CHANNELS` when this was written.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from hotline.agents import DEFAULT_KEEP_DAYS, Registry


@pytest.fixture
def registry(tmp_path: Path) -> Registry:
    return Registry(path=tmp_path / "agents.json")


def test_an_agent_declares_itself(registry: Registry) -> None:
    agent = registry.declare("sid-1", "builder", "rewriting the voice decoder")
    assert agent.task == "rewriting the voice decoder"
    assert agent.done is False
    assert registry.working() == [agent]


def test_the_task_can_be_edited_whenever(registry: Registry) -> None:
    """"its task which it can edit whenever" -- work turns out to be something
    else more often than not."""
    registry.declare("sid-1", "builder", "rewriting the voice decoder")
    agent = registry.retask("sid-1", "actually, fixing the key rotation")
    assert agent is not None and agent.task == "actually, fixing the key rotation"


def test_redeclaring_is_a_retask_not_a_reset(registry: Registry) -> None:
    """A session that reframes its work halfway through is the same agent doing
    the same job -- it must not lose the channel it already owns."""
    first = registry.declare("sid-1", "builder", "one thing")
    first.channel_id = 999
    started = first.declared_at

    again = registry.declare("sid-1", "builder", "another thing")
    assert again.channel_id == 999
    assert again.declared_at == started
    assert again.task == "another thing"


def test_completion_is_explicit(registry: Registry) -> None:
    """A session that merely exited has not finished its work, it has stopped
    doing it. Only `done` completes an agent."""
    registry.declare("sid-1", "builder", "a job")
    assert registry.working() != []
    agent = registry.complete("sid-1", handoff="/tmp/handoff.md")
    assert agent is not None and agent.done
    assert agent.handoff == "/tmp/handoff.md"
    assert registry.working() == []


def test_retention_runs_from_completion_not_creation(registry: Registry) -> None:
    """An agent that ran for a week is exactly the one whose record you want
    afterwards. Dating retention from creation would throw it away first."""
    agent = registry.declare("sid-1", "builder", "a long job")
    agent.declared_at = time.time() - 30 * 86400  # started a month ago
    registry.complete("sid-1")

    assert registry.expired(now=time.time() + 2 * 86400) == []
    expired = registry.expired(now=time.time() + DEFAULT_KEEP_DAYS * 86400 + 1)
    assert [a.session_id for a in expired] == ["sid-1"]


def test_an_agent_still_working_never_expires(registry: Registry) -> None:
    """Nothing here may delete the channel of something still running."""
    agent = registry.declare("sid-1", "builder", "a fortnight of work")
    agent.declared_at = time.time() - 365 * 86400
    assert registry.expired(now=time.time() + 365 * 86400) == []


def test_retention_is_overridable_per_agent(registry: Registry) -> None:
    """"autodelete after 3 days except when stated differently"."""
    registry.declare("sid-1", "keeper", "worth keeping", keep_days=30)
    registry.complete("sid-1")
    assert registry.expired(now=time.time() + 4 * 86400) == []
    assert len(registry.expired(now=time.time() + 31 * 86400)) == 1


def test_an_agent_can_ask_for_no_channel(registry: Registry) -> None:
    """"they should become a channel except if explicitly asked" -- the exception
    is per-agent, because what gets suppressed is one noisy fan-out."""
    registry.declare("sid-1", "loud", "spawns forty subagents", wants_channel=False)
    registry.declare("sid-2", "normal", "ordinary work")
    assert [a.session_id for a in registry.needing_channel()] == ["sid-2"]


def test_subagents_record_their_parent(registry: Registry) -> None:
    registry.declare("sid-1", "parent", "the main job")
    child = registry.declare("sid-2", "child", "a piece of it", parent="parent")
    assert child.parent == "parent"
    assert "subagent of parent" in child.describe()


def test_the_registry_survives_a_restart(tmp_path: Path) -> None:
    """A reboot must not reset the retention clock or orphan a channel, which is
    why this lives in XDG_STATE_HOME and not the /run spool."""
    path = tmp_path / "agents.json"
    first = Registry(path=path)
    agent = first.declare("sid-1", "builder", "a job")
    agent.channel_id = 4242
    first.complete("sid-1", handoff="/tmp/h.md")
    first.save()

    revived = Registry(path=path)
    back = revived.get("sid-1")
    assert back is not None
    assert back.task == "a job"
    assert back.channel_id == 4242
    assert back.done and back.handoff == "/tmp/h.md"


def test_a_record_from_a_newer_version_is_skipped_not_fatal(tmp_path: Path) -> None:
    """Refusing to load the whole registry would silently forget every agent on
    the machine, which is a much worse outcome than dropping one record."""
    path = tmp_path / "agents.json"
    path.write_text(
        '{"agents": [{"session_id": "a", "name": "n", "task": "t", '
        '"from_the_future": 1}, '
        '{"session_id": "b", "name": "m", "task": "u"}]}'
    )
    registry = Registry(path=path)
    assert list(registry.agents) == ["b"]


def test_lookup_by_name_is_case_insensitive(registry: Registry) -> None:
    registry.declare("sid-1", "Voice-Decoder", "a job")
    assert registry.by_name("voice-decoder") is not None
    assert registry.by_name("nope") is None


# ---- asking over Discord or voice, not just from a shell ----------------


def test_agents_is_a_control_word() -> None:
    """Bogdan asks this from his phone, so it belongs in the router rather than
    only in the CLI."""
    from hotline.router import parse_utterance

    for phrase in ["agents", "who is working", "who's working on what", "what is everyone doing"]:
        assert parse_utterance(phrase).action == "agents", phrase


def test_agent_shaped_questions_still_reach_a_session() -> None:
    from hotline.router import parse_utterance

    for phrase in ["agents are hard to name", "who is working on the decoder right now"]:
        assert parse_utterance(phrase).action != "agents", phrase


# ---- adopt: a respawned worker continues an existing agent -----------------
#
# The watchdog restarts a dead worker, and the replacement is the same agent
# doing the same job. Declaring afresh would mint a second channel and leave
# Bogdan reading an orphaned one while `connect <name>` resolved to a corpse.


def test_adopting_moves_the_identity_to_a_new_session(registry: Registry) -> None:
    original = registry.declare("sid-old", "hotline-80", "building the voice path")
    original.channel_id = 4242
    registry.save()

    adopted = registry.adopt("hotline-80", "sid-new")

    assert adopted is not None
    assert adopted.session_id == "sid-new"
    assert adopted.channel_id == 4242, "the channel is the point of adopting"
    assert registry.get("sid-old") is None, "the corpse must stop resolving"
    assert registry.get("sid-new") is adopted


def test_adopting_keeps_the_original_start_time(registry: Registry) -> None:
    """Retention dates from completion, but an adopted agent that later finishes
    should still read as the long-running thing it was, not as newly born."""
    original = registry.declare("sid-old", "hotline-80", "building")
    original.declared_at = 1000.0
    registry.save()

    adopted = registry.adopt("hotline-80", "sid-new")

    assert adopted is not None
    assert adopted.declared_at == 1000.0


def test_adopting_revives_a_finished_agent(registry: Registry) -> None:
    registry.declare("sid-old", "hotline-80", "building")
    registry.complete("sid-old", handoff="/tmp/handoff.md")

    adopted = registry.adopt("hotline-80", "sid-new")

    assert adopted is not None
    assert not adopted.done, "something is alive and has picked the work back up"
    assert adopted in registry.working()


def test_adopting_yourself_is_a_no_op(registry: Registry) -> None:
    """A worker that re-runs its own start-up script must not lose its record."""
    registry.declare("sid-1", "hotline-80", "building")

    adopted = registry.adopt("hotline-80", "sid-1")

    assert adopted is not None
    assert registry.get("sid-1") is adopted
    assert len(registry.agents) == 1


def test_adopting_an_unknown_agent_returns_none(registry: Registry) -> None:
    assert registry.adopt("nobody", "sid-new") is None


def test_adoption_survives_a_reload(registry: Registry, tmp_path: Path) -> None:
    registry.declare("sid-old", "hotline-80", "building")
    registry.adopt("hotline-80", "sid-new")

    reloaded = Registry(path=tmp_path / "agents.json")
    assert reloaded.get("sid-new") is not None
    assert reloaded.get("sid-old") is None


def test_retasking_does_not_rename(registry: Registry) -> None:
    """The name is the identity Bogdan types. `declare` derives its `name` from
    the session, so re-declaring an adopted agent used to rename `hotline-80`
    back to `data-88` and orphan `connect hotline-80` along with it."""
    registry.declare("sid-old", "hotline-80", "building")
    registry.adopt("hotline-80", "sid-new")

    retasked = registry.declare("sid-new", "data-88", "something else now")

    assert retasked.name == "hotline-80"
    assert retasked.task == "something else now"
    assert registry.by_name("hotline-80") is retasked
