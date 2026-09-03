# hotline-admin

The admin surface for [`hotline`](../../README.md), packaged as a plugin
rather than built into core. `hotline`'s CLI discovers it at runtime through
the `hotline.plugins` entry-point group declared in this package's
`pyproject.toml` -- if `hotline-admin` isn't installed, the verbs it owns
report "not available" instead of existing as dead code in every install.

Currently carries one verb: `--grant NAME ROLE <message>`, which records a
standing role (e.g. sys-admin) and the Discord message where a human granted
it, so the delegation is checkable against Discord rather than the machine
vouching for itself.

`--adopt`, `--agents`, `--list` and `--provenance` are *not* here -- they
stayed in `hotline` core in this split. `--provenance` is message
verification, part of the comms trust layer every relay uses, not an admin
action. The others were left in core on the operator's explicit call: moving
a verb into this plugin later is cheap, moving one back after other code has
come to depend on it living here is not, so anything not clearly
authority-granting stayed put.
