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
