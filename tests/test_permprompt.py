"""Measured against real pane captures, not against invented ones.

The numbers this file prints are the detector's quality claim. A detector that
fires on everything scores perfectly on `blocked/` alone, so the negatives are
the half that matters and they outnumber the positives.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hotline.permprompt import detect

PANES = Path(__file__).parent / "panes"
BLOCKED = sorted((PANES / "blocked").glob("*.txt"))
FINE = sorted((PANES / "fine").glob("*.txt"))
FORGED = sorted((PANES / "forged").glob("*.txt"))


def test_the_corpus_is_actually_there() -> None:
    """A green suite over an empty fixture directory proves nothing.

    This project has a note about exactly that failure: 814 tests passing on a
    media engine nothing ever constructed.
    """
    assert len(BLOCKED) >= 2, "need at least the two real blocked shapes"
    assert len(FINE) >= 5, "negatives must outnumber positives or the score is meaningless"


@pytest.mark.parametrize("path", BLOCKED, ids=lambda p: p.stem)
def test_blocked_panes_are_detected(path: Path) -> None:
    verdict = detect(path.read_text())
    assert verdict, f"missed a real prompt: {verdict.reason}"
    assert len(verdict.options) >= 2
    assert verdict.selected, "no option marked as selected"
    assert verdict.footer


@pytest.mark.parametrize("path", FINE, ids=lambda p: p.stem)
def test_healthy_panes_are_not_detected(path: Path) -> None:
    verdict = detect(path.read_text())
    assert not verdict, f"false positive on {path.name}: {verdict.describe()}"


@pytest.mark.parametrize("path", FORGED, ids=lambda p: p.stem)
def test_forged_panes_match_on_text_and_that_is_expected(path: Path) -> None:
    """Text alone cannot reject a printed copy of a real prompt.

    Asserting the *match* rather than skipping the case keeps the limitation
    honest: if a future change to `detect` starts rejecting these, it is
    rejecting real prompts too, and this test is where that shows up.
    """
    assert detect(path.read_text()), "forged panes are text-identical by construction"


def test_the_question_is_the_question_not_the_chrome() -> None:
    """The trust dialog's nearest line above the options is a link, not a question."""
    trust = detect((PANES / "blocked" / "folder-trust.txt").read_text())
    assert "trust" in trust.question.lower()
    assert trust.question.strip() != "Security guide"


def test_the_triggering_command_survives_into_the_report() -> None:
    """The operator should not have to go and dig for what caused the prompt."""
    verdict = detect((PANES / "blocked" / "bash-permission.txt").read_text())
    assert "fixtureC/.git" in verdict.context


def test_fingerprint_ignores_cursor_movement() -> None:
    """Arrowing between options is not a new prompt to be re-notified about."""
    original = (PANES / "blocked" / "bash-permission.txt").read_text()
    moved = original.replace(" ❯ 1. Yes", "   1. Yes").replace("   3. No", " ❯ 3. No")
    before, after = detect(original), detect(moved)
    assert before and after
    assert before.selected != after.selected, "fixture edit did not move the cursor"
    assert before.fingerprint == after.fingerprint


def test_prose_mentioning_the_footer_is_not_a_footer() -> None:
    """A brief about permission prompts must not read as one."""
    pane = (
        "I am building a watcher for these.\n"
        "The dialog ends with a line reading Esc to cancel, and above it the options.\n"
        "  1. Yes\n  2. No\n"
        "Press Esc to cancel if you disagree with any of that reasoning above.\n"
    )
    assert not detect(pane)


def test_a_single_option_is_not_a_decision() -> None:
    pane = "Something happened.\n ❯ OK\n Esc to cancel\n"
    verdict = detect(pane)
    assert not verdict
    assert "option" in verdict.reason


def test_empty_and_whitespace_panes() -> None:
    assert not detect("")
    assert not detect("\n\n   \n")


def test_scores(capsys: pytest.CaptureFixture[str]) -> None:
    """Print the confusion matrix, so the claim is a number and not an adjective."""
    tp = sum(1 for p in BLOCKED if detect(p.read_text()))
    fn = len(BLOCKED) - tp
    fp = sum(1 for p in FINE if detect(p.read_text()))
    tn = len(FINE) - fp
    with capsys.disabled():
        print(
            f"\n  detector: {tp} true positives, {fn} false negatives, "
            f"{tn} true negatives, {fp} false positives "
            f"({len(BLOCKED)} blocked / {len(FINE)} healthy real captures)"
        )
    assert fp == 0 and fn == 0


def test_ansi_escapes_break_the_footer() -> None:
    """Pinning a load-bearing assumption: captures must arrive without ANSI.

    `tmuxen.capture` uses `capture-pane -p` without `-e`, which is the only
    reason this holds. Adding `-e` would break every match silently, so this
    test is here to make that a visible decision rather than a discovery.
    """
    clean = " Pick?\n ❯ 1. a\n   2. b\n Esc to cancel\n"
    assert detect(clean)
    assert not detect(clean.replace(" Esc to cancel", " \x1b[2mEsc to cancel\x1b[0m"))


def test_options_must_align_under_the_cursor() -> None:
    """The other load-bearing assumption, pinned for the same reason."""
    aligned = " Pick?\n ❯ 1. a\n   2. b\n Esc to cancel\n"
    assert detect(aligned)
    assert not detect("Pick?\n❯ 1. a\n2. b\nEsc to cancel\n")


def test_a_faithful_quotation_of_a_prompt_is_not_a_prompt() -> None:
    """Prose that reproduces a prompt exactly, cursor and footer and all.

    Found by an adversarial review quoting this project's own incident writeup,
    which renders the cursor as ">". The process gate cannot help here -- an
    agent displaying the file IS a claude pane -- so the detector has to reject
    it, and it does by matching only the glyph the CLI actually draws.
    """
    quoted = (PANES / "fine" / "prose-quoting-a-real-prompt.txt").read_text()
    assert "Do you want to proceed?" in quoted and "Esc to cancel" in quoted
    assert not detect(quoted)


def test_a_wrapped_option_does_not_end_the_option_list() -> None:
    """The CLI hard-wraps long labels; the wrap is not the end of the dialog.

    Before this, a long option that wrapped left the collector with one option
    and it refused to call that a decision -- a false negative whose cause was
    terminal width.
    """
    pane = (
        "Do you want to make this edit to foo.py?\n"
        "❯ 1. Yes, and don't ask again for edits to this file this\n"
        "     session\n"
        "  2. No\n"
        "Esc to cancel · Tab to amend\n"
    )
    verdict = detect(pane)
    assert verdict, verdict.reason
    assert len(verdict.options) == 2
    assert verdict.options[0].endswith("this session"), "the wrap should rejoin"


def test_a_long_option_list_is_still_matched() -> None:
    """_CURSOR_WINDOW is a ceiling on option count, not only a scrollback guard."""
    options = "".join(f"   {i}. option {i}\n" for i in range(2, 31))
    pane = "Pick one?\n ❯ 1. option 1\n" + options + " Esc to cancel\n"
    verdict = detect(pane)
    assert verdict, verdict.reason
    assert len(verdict.options) == 30


def test_the_corpus_spans_a_cli_version_bump() -> None:
    """The silent failure this module cannot alarm on is a CLI shape change.

    The LIMITATIONS section admits there is no alarm for "I have stopped
    recognising prompts". The only defence is a corpus that covers more than
    one release, so a version bump that breaks the shape shows up here rather
    than as a quiet machine. Captured on 2.1.269 and again on 2.1.280.
    """
    versions = set()
    for path in BLOCKED:
        for line in path.read_text().splitlines():
            if "Claude Code v" in line:
                versions.add(line.split("Claude Code v")[1].strip())
    assert len(versions) >= 2, f"corpus only covers {versions or 'one unlabelled version'}"
