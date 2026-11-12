"""A resumed agent in a never-trusted folder must not sit on the trust dialog until the wake times out."""

from hotline import tmuxen
from hotline.tmuxen import trust_choice

ON_NO = """ Accessing workspace:
 /home/bodas/data/voice-score
 Quick safety check: Is this a project you created or one you trust? (Like your
 own code, a well-known open source project, or work from your team).
 ❯ No, exit
   Yes, I trust this folder
 Enter to confirm · Esc to cancel"""

ON_YES = ON_NO.replace(" ❯ No, exit", "   No, exit").replace("   Yes, I trust", " ❯ Yes, I trust")


def test_dialog_on_no_moves_up_first() -> None:
    assert trust_choice(ON_NO) == "Up"


def test_dialog_on_yes_confirms() -> None:
    assert trust_choice(ON_YES) == "Enter"
    assert trust_choice(ON_YES.replace("❯", ">")) == "Enter"


def test_other_panes_are_left_alone() -> None:
    assert trust_choice("❯ \n  ⏵⏵ bypass permissions on") is None
    assert trust_choice("Yes, I trust this folder") is None  # the words alone, no dialog


def test_accept_presses_up_then_enter(monkeypatch) -> None:
    panes = iter([ON_NO, ON_YES])
    sent: list[str] = []
    monkeypatch.setattr(tmuxen, "capture", lambda target, lines=60: next(panes))
    monkeypatch.setattr(tmuxen, "_tmux", lambda *a, check=True: sent.append(a[-1]))
    assert tmuxen.accept_folder_trust("hl-x")
    assert sent == ["Up", "Enter"]


def test_accept_never_presses_enter_on_no(monkeypatch) -> None:
    sent: list[str] = []
    monkeypatch.setattr(tmuxen, "capture", lambda target, lines=60: ON_NO)  # Up did not move it
    monkeypatch.setattr(tmuxen, "_tmux", lambda *a, check=True: sent.append(a[-1]))
    assert not tmuxen.accept_folder_trust("hl-x")
    assert sent == ["Up"]
