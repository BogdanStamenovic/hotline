"""Is this pane showing a prompt that only a human can answer?

On 2026-09-22 a build agent (`api-1b`, tmux session `kr3build-01`) hit Claude
Code's dangerous-operation guard mid-build:

    Dangerous rm operation on possibly-empty variable path: "$SP/$1/.git"
    Do you want to proceed?
    > 1. Yes
      2. No

It sat there until a human happened to ask how the build was going. Nothing on
the machine said it was stuck: `hotline --list` reported `waiting`, `tmux ls`
showed a live session, and the process was alive and burning nothing. The agent
cannot answer its own prompt, so it would have waited forever.

That was the second instance of one class. The first was spawned agents wedging
on the folder-trust prompt ("Is this a project you created or one you trust?"),
equally invisible. The class is *an agent blocked on an interactive prompt only
a human or an operator can answer*, and this module recognises it.

**Why the pane, and not the transcript.** `wedge.py` answers the neighbouring
question by reading the transcript, and that approach cannot work here. Measured
on 2026-09-22 against three agents driven into real prompts:

  * the prompt's own text is never written to the transcript -- 0 hits for
    "Do you want to proceed" or for the guard's reason line while the prompt was
    on screen;
  * an agent blocked at the *folder-trust* prompt has no transcript file at all,
    and no session descriptor either, so `hotline --list` does not merely
    mislabel it, it cannot see it.

The pane is the only place the prompt exists. That is the whole justification
for scraping a terminal, which is otherwise a thing to avoid.

**What the transcript does say**, contrary to the note that started this: the
blocked *tool call* is there. The transcript ends on an assistant record holding
a `tool_use` with no matching `tool_result`, and its input carries the exact
command. `trigger.py`'s job is to read that; this module's job is the pane. A
dangling `tool_use` is never evidence on its own -- a twenty-minute build has
one too -- so it only ever enriches a match made here.

**Matching is structural, not lexical.** The word "proceed" appears constantly
in ordinary agent output, and worse, two of the three real prompts do not
contain it at all. The shapes actually observed:

    | prompt            | question                  | options       | footer                          |
    |-------------------|---------------------------|---------------|---------------------------------|
    | Bash permission   | "Do you want to proceed?" | 1./2./3.      | "Esc to cancel · Tab to amend"  |
    | folder trust      | "Quick safety check: ..."  | prose         | "Enter to confirm · Esc to cancel" |
    | auto-mode offer   | "Teach auto mode ...?"     | 1./2./3.      | "Enter to confirm · Esc to cancel" |

Wording, numbering and footer all differ. What they share is a shape: a
selection cursor on one of several sibling option lines, closed by a footer of
short `key to verb` hints.

**The dialog must be the bottom-most thing in the pane.** This is the rule that
does most of the work, and it is what separates a session that is *stopped* from
one that is merely being nagged. A blocking permission prompt is the last thing
drawn. The auto-mode offer is drawn *above a live input box* -- the session is
still accepting input, still reachable by `hotline --to`, and nothing is
stranded. So the auto-mode offer is deliberately NOT a match, even though it is
structurally a dialog, because no work is blocked and escalating on it would
page the operator about a nag that appears on every idle session.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# U+276F is what the CLI actually draws; ">" is what people transcribe it as in
# notes and bug reports, and costs nothing to accept.
_CURSOR = re.compile(r"^(?:❯|›|>)[ \s]")

# A footer segment is a key hint: "Esc to cancel", "Tab to amend", "↑/↓ to
# select". Prose does not look like this, which is the point -- the alternative
# was substring-matching "Esc to cancel", and a pane that merely *mentions* it
# would then match.
_HINT = re.compile(r"^\S{1,12}(?:/\S{1,12})?\s+to\s+\w[\w\s]{0,20}$")

# Every dialog observed offers Escape. Requiring it keeps a stray two-segment
# hint line from standing in for a real footer.
_ESCAPE_HINT = re.compile(r"\besc\b\s+to\s+cancel\b", re.IGNORECASE)

# How far above the footer a cursor line may sit. Generous enough for a long
# option list, tight enough that an unrelated "❯" further up the scrollback
# cannot be adopted as this dialog's cursor.
_CURSOR_WINDOW = 24

# Lines of pane kept above the question, so the escalation shows what command
# triggered the prompt without anybody having to go and look.
CONTEXT_LINES = 14


@dataclass(frozen=True)
class Prompt:
    """What a pane's last few lines say about whether its agent is stuck."""

    blocked: bool
    # The line that poses the question, e.g. "Do you want to proceed?".
    question: str = ""
    # Every choice offered, cursor stripped, in screen order.
    options: tuple[str, ...] = ()
    # The one the cursor currently sits on. Recorded for the report, and
    # deliberately NOT part of `fingerprint` -- arrowing up and down changes it
    # without making this a different prompt.
    selected: str = ""
    footer: str = ""
    # Pane above the question: the tool call, the guard's reason, the spinner.
    context: str = ""
    # Why not blocked, when it is not, so a log line can say something truer
    # than a bare False.
    reason: str = ""

    def __bool__(self) -> bool:
        return self.blocked

    @property
    def fingerprint(self) -> str:
        """A stable id for "this same prompt, still unanswered".

        Keyed on the question and the option texts only. Cursor position moves
        when somebody arrows through the list, and the context above changes as
        a spinner animates -- neither makes it a new prompt, and including
        either would re-notify the operator about a prompt they have already
        been told about.
        """
        payload = "\n".join((self.question, *self.options))
        return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()[:16]

    def describe(self) -> str:
        lines = [self.question or "(no question line)"]
        for opt in self.options:
            lines.append(f"  {'>' if opt == self.selected else ' '} {opt}")
        return "\n".join(lines)


def _is_footer(line: str) -> bool:
    text = line.strip()
    if not text or len(text) > 120:
        return False
    # "·" is the CLI's separator; "|" appears in some terminals' rendering.
    segments = [seg.strip() for seg in re.split(r"[·|]", text)]
    if not (1 <= len(segments) <= 5):
        return False
    if not all(seg and _HINT.match(seg) for seg in segments):
        return False
    return any(_ESCAPE_HINT.search(seg) for seg in segments)


def _question_above(before: list[str], *, window: int = 8) -> str:
    """The line that actually poses the question, not merely the nearest one.

    "Do you want to proceed?" sits directly above its options, so the nearest
    non-blank line is right for the permission prompt. The folder-trust dialog
    is not built that way: its question is four lines up and the line adjacent
    to the options is the words "Security guide", a link. Taking the nearest
    line there produces a notification whose subject is a piece of UI chrome.

    So: prefer the closest interrogative within `window` lines, and fall back to
    the nearest non-blank line when the dialog poses no question at all.

    "Contains a question mark" rather than "ends with one", because the CLI hard
    wraps: the trust prompt's question ends the line with "in this" and the next
    line is "folder first.". Requiring a trailing "?" found neither, and settled
    on the link text "Security guide" as the subject of the notification.
    """
    recent = [line.strip() for line in before[-window:] if line.strip()]
    for text in reversed(recent):
        if "?" in text:
            return text
    return recent[-1] if recent else ""


def detect(pane: str, *, context_lines: int = CONTEXT_LINES) -> Prompt:
    """Decide whether `pane` ends in an unanswered interactive prompt.

    Pure text in, verdict out: no tmux, no processes, no clock. That is what
    makes the false-positive rate measurable against a fixture corpus rather
    than argued about.
    """
    lines = [line.rstrip() for line in pane.splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return Prompt(False, reason="pane is empty")

    # 1. The dialog has to be the bottom-most element. A dialog with a live
    #    input box under it is a nag over a reachable session, not a wedge.
    if not _is_footer(lines[-1]):
        return Prompt(False, reason="last line is not a dialog footer")
    footer = lines[-1].strip()

    # 2. Find the selection cursor above it.
    body = lines[:-1]
    cursor_at = None
    glyph = None
    for index in range(len(body) - 1, max(-1, len(body) - 1 - _CURSOR_WINDOW), -1):
        glyph = _CURSOR.match(body[index].lstrip())
        if glyph:
            cursor_at = index
            break
    if cursor_at is None or glyph is None:
        return Prompt(False, reason="dialog footer with no selection cursor above it")

    # 3. Options are the cursor line's siblings: the lines indented to the same
    #    column its text starts at. Collected upward as well as downward,
    #    because the cursor sits wherever it was last moved to -- landing on the
    #    last option used to yield a one-option dialog and no match at all.
    # The match is carried down from the search rather than recomputed. It was
    # an `assert glyph is not None` here, which `python -O` strips -- and the
    # next line would then raise inside the sweep loop instead of returning a
    # verdict. A detector is not a place to rely on assertions staying compiled.
    cursor_line = body[cursor_at]
    content_col = (len(cursor_line) - len(cursor_line.lstrip())) + len(glyph.group(0))

    def _sibling(raw: str) -> bool:
        return bool(raw.strip()) and (len(raw) - len(raw.lstrip())) == content_col

    above: list[str] = []
    for raw in reversed(body[:cursor_at]):
        if not _sibling(raw):
            break
        above.append(raw.strip())
    above.reverse()

    selected = _CURSOR.sub("", cursor_line.lstrip(), count=1).strip()

    below: list[str] = []
    for raw in body[cursor_at + 1 :]:
        if not raw.strip():
            # A blank line inside the block: the trust dialog puts one before
            # its footer. Keep looking rather than closing the list early.
            continue
        if not _sibling(raw):
            break
        below.append(raw.strip())

    options = [*above, selected, *below]

    # 4. One option is a statement, not a choice. Two is a decision somebody has
    #    to make, which is the thing being escalated.
    if len(options) < 2:
        return Prompt(False, reason=f"only {len(options)} option under the cursor")

    question = _question_above(body[:cursor_at])

    start = max(0, cursor_at - context_lines)
    context = "\n".join(body[start:cursor_at]).strip()

    return Prompt(
        blocked=True,
        question=question,
        options=tuple(options),
        selected=selected,
        footer=footer,
        context=context,
        reason=f"{len(options)} options awaiting a choice",
    )
