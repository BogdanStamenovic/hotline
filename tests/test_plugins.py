"""The admin plugin boundary: `--adopt`, `--declare` and `--grant`.

These three verbs act on the roster rather than on calls, pages or Discord, so
they live in hotline-admin and core reaches them through the `hotline.plugins`
entry-point group. Core still *declares* the flags, which is the whole point of
the design and the thing worth pinning down: on an install without the plugin
the flag still parses, so the user gets "requires hotline-admin" rather than
argparse's "unrecognized arguments", which would read like a typo.

The absent-plugin case matters more than it looks. Every spawned agent runs
`hotline --adopt <name>` before it can do anything else, so the failure mode of
a missing plugin is every new session starting with no identity -- and the one
thing that must not happen is that failing silently or blaming the agent's own
arguments.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hotline.cli import main

SID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def no_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `hotline` install with no hotline-admin alongside it."""
    monkeypatch.setattr("hotline.cli._plugin_verb", lambda verb: None)


# ---- the plugin is installed (the normal case) ------------------------------


@pytest.mark.parametrize("verb", ["adopt", "declare", "grant"])
def test_the_verb_resolves_into_hotline_admin(verb: str) -> None:
    """Pins the split itself, not just the behaviour. Every test below this one
    would still pass with these implementations sitting in `hotline.cli`, so
    without this the suite could not tell a working plugin boundary from no
    boundary at all."""
    from hotline.cli import _plugin_verb

    impl = _plugin_verb(verb)
    assert impl is not None, f"--{verb} did not resolve; is hotline-admin installed?"
    assert impl.__module__.startswith("hotline_admin"), impl.__module__


def test_declare_then_adopt_go_through_the_plugin(fake_claude: Path, capsys) -> None:
    """Both verbs resolved through the entry point, not imported directly."""
    assert main(["--declare", "a task", "--session-id", SID, "--no-channel"]) == 0
    assert "declared:" in capsys.readouterr().err

    # A second session taking over the first one's identity.
    other = "99999999-8888-7777-6666-555555555555"
    assert main(["--adopt", SID[:8], "--session-id", other]) == 0
    assert "adopted:" in capsys.readouterr().err


def test_adopting_a_name_that_does_not_exist_is_an_error(fake_claude: Path, capsys) -> None:
    """On stderr and exit 1 even though `--quiet` suppresses ordinary logging:
    the caller is a session that cannot proceed without an identity."""
    assert main(["--adopt", "no-such-agent", "--session-id", SID, "--quiet"]) == 1
    assert "no agent called 'no-such-agent'" in capsys.readouterr().err


# ---- the plugin is not installed --------------------------------------------


@pytest.mark.parametrize(
    ("verb", "argv"),
    [
        ("adopt", ["--adopt", "x", "--session-id", SID]),
        ("declare", ["--declare", "t", "--session-id", SID, "--no-channel"]),
        ("grant", ["--grant", "name", "role", "where"]),
    ],
)
def test_an_admin_verb_without_the_plugin_names_what_is_missing(
    verb: str, argv: list[str], fake_claude: Path, no_plugin: None, capsys
) -> None:
    assert main(argv) == 1
    err = capsys.readouterr().err
    # The verb, so a reader with several admin flags in a script knows which
    # one stopped -- and the package name, so the fix is in the message.
    assert f"--{verb}" in err
    assert "hotline-admin" in err


def test_core_verbs_do_not_need_the_plugin(fake_claude: Path, no_plugin: None, capsys) -> None:
    """The split's actual promise: a `hotline` with no admin plugin is still a
    working call/page/Discord tool, not a broken install."""
    assert main(["--agents"]) == 0
    assert main(["--list"]) == 0
