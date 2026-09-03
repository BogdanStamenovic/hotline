"""hotline-admin: the admin surface, as a plugin `hotline` core discovers via
the `hotline.plugins` entry-point group rather than importing.

Carries the verbs that act on the *roster* -- `--declare` (register a session
and what it is working on), `--adopt` (take over a finished agent's identity)
and `--grant` (record a standing role and where a human granted it). Who
exists, what they were told to do, and who granted what is the operator's model
of the machine; `hotline` core underneath is the call/page/Discord layer.

`--agents`, `--list` and `--provenance` stayed in core: the first two are
read-only views a running agent uses to orient itself, and `--provenance` is
message verification that every relay depends on, not an admin action.

Core still declares the flags it dispatches here, so an install without this
plugin reports which verb needs it instead of argparse's "unrecognized
arguments". See this package's README for why that shape is nonetheless the
wrong one to deploy on Bogdan's box.
"""

from __future__ import annotations

__version__ = "0.1.0"
