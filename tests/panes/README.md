# Pane fixtures

Real `tmux capture-pane -p` output, captured on 2026-09-22 from agents driven
into genuine prompts on this machine. Nothing here is hand-written: a
synthesised prompt would only prove the detector matches what its author
imagined the CLI draws, and the three real shapes turned out to disagree with
each other about wording, numbering and footer text.

| directory | must `detect()` match? | what these are |
|---|---|---|
| `blocked/` | yes | an agent stopped on a prompt only a human can answer |
| `fine/`    | no  | healthy panes, including ones whose text discusses permission prompts |
| `forged/`  | **yes, and that is correct** | a shell that printed a captured prompt |

`forged/` is the honest part. Its contents are byte-for-byte a real prompt, so
no amount of text analysis can reject them and `detect()` is not asked to try.
They are rejected one layer up, by `tmuxen.Pane.is_claude`: a pane whose
foreground process is `cat` or `sleep` is never examined at all. The fixture
exists so that the limit is tested rather than asserted in a README.

`fine/automode-offer.txt` is the interesting negative. It is structurally a
dialog -- cursor, options, "Esc to cancel" footer -- but it is drawn *above a
live input box*, so the session is still reachable and no work is blocked. It
is the case that distinguishes "stopped" from "being nagged".
