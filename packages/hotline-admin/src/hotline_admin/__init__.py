"""hotline-admin: the admin surface, as a plugin `hotline` core discovers via
the `hotline.plugins` entry-point group. Currently carries only `--grant`
(recording a standing role) -- `--adopt`, `--agents`, `--list` and
`--provenance` stayed in `hotline` core deliberately (see the split's
decision log); moving a verb out here later is cheap, moving one back in
after code has grown to depend on it living here is not.
"""

from __future__ import annotations

__version__ = "0.1.0"
