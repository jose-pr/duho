#!/usr/bin/env python3
"""``duho.runpath``: RunPath ("rc") step-directory discovery, opt-in.

Contrast with ``discovery_app.py``: ``examples/rc/`` is ALSO a bare
directory of ``.py`` files with no ``__init__.py`` -- the SAME shape a
regular-discovery directory has. What routes it to the RunPath provider
instead of normal module-command discovery is entirely filenames: its files
are ``NN-name.py`` steps (``01-check.py``, ``20-provision.py``,
``30-optional-report;!strict.py``), so ``duho.runpath.is_runpath_dir``
recognizes it as a RunPath ("rc") directory and hands it a
:class:`~duho.runpath.RunPathCmd` instead of individual module commands.

**Resolving a RunPath dir needs `CmdBuilder`, not `discover_commands`.**
`discover_commands`/`app(source=...)` (see ``discovery_app.py``) only walks
the TOP-LEVEL ``.py`` files of the directory you point it at -- it does not
descend into subdirectories or consult providers per-entry. A RunPath
directory is resolved as ONE command via
``duho.discovery.CmdBuilder(name, path).command`` (the same seam
`register_command_provider` plugs into), then handed to `app` via
``commands=[...]`` -- exactly the split ``discovery_app.py`` and this file
demonstrate: loose `.py` files -> `discover_commands`; a single RunPath dir
-> `CmdBuilder`.

``examples/rc/`` demonstrates every RunPath feature added on top of
the original ordered-steps runner:

* ``__main__.py`` -- the directory's optional lifecycle: ``init`` runs once
  before any step and returns a ``ctx``; ``success``/``finally_`` run once
  after. ``01-check.py`` is a 2-arg step (``def main(cmd, ctx)``) that
  receives that ``ctx``; ``20-provision.py`` and the report step are 1-arg
  (``def main(cmd)``) and are unaffected -- arity-detected, not guessed.
* ``BEFORE``/``AFTER``/``REQUIRED`` -- ``01-check.py`` declares
  ``BEFORE = ["provision"]``, ``20-provision.py`` declares
  ``REQUIRED = ["check"]`` (a HARD dependency: `check` must run and succeed),
  and ``30-optional-report;!strict.py`` declares ``AFTER = ["provision"]`` (a
  SOFT ordering hint only).
* filename-encoded per-step strict -- ``30-optional-report;!strict.py``'s
  ``;!strict`` token means only THAT step is resilient on failure; every
  other step in this same directory is strict-by-default (no token needed).

**A shared global-options root** (``RunpathAppArgs``, the same idea as
``discovery_app.py``'s ``DiscoveryAppArgs``): its DATA fields (``label``,
``dry_run``) reach ``rc``'s parsed instance via ``duho.app``'s parent-arg
inheritance (every subcommand parser is built with ``parents=[root
parser]``), so ``examples/rc/__main__.py`` and its steps read
``cmd.label``/``cmd.dry_run`` directly off the SAME ``RunPathCmd`` instance
duho built -- no redeclaring those fields per step.

**Sharing more than data: ``register(base=...)``**. Unlike a plain module
command (whose ``args`` parameter genuinely IS an instance of whatever root
class you pass), the RunPath provider builds its OWN ``RunPathCmd`` subclass
per directory (see ``duho.runpath._build_runpath_command``); by default it
does not inherit a custom root class, so only DATA fields propagate onto the
parsed ``rc`` instance (via ``app()``'s ``parents=`` mechanism), not METHODS.
``duho.runpath.register(base=RunpathAppArgs)`` (called once, early, below)
fixes exactly this: every RunPathCmd this module's provider builds afterward
ALSO inherits ``RunpathAppArgs`` for real, so a method like ``_tag_line_``
below is callable on the parsed ``rc`` instance too, not just its data
fields. ``examples/rc/__main__.py`` still falls back to reading ``cmd.label``
directly when ``_tag_line_`` isn't there (so the directory stays runnable
from an entry point that never registered this base), but calling it through
``python examples/runpath_app.py rc`` exercises the real method.

Run it (needs ``import duho.runpath`` to activate the RunPath provider,
already done below)::

    python examples/runpath_app.py rc
    python examples/runpath_app.py --label prod rc
    python examples/runpath_app.py rc --rcopts '!*,provision'
    python examples/runpath_app.py rc --rcopts 'strict'
"""

import sys
from pathlib import Path

import duho
import duho.runpath  # activates the RunPath provider and provides register()
from duho import LoggingArgs
from duho.discovery import CmdBuilder

_RC_DIR = Path(__file__).parent / "rc"


class RunpathAppArgs(LoggingArgs):
    """runpath-app: a demo CLI that runs an ordered step directory as `rc`.

    These options are global -- shared with the `rc` step directory below.
    """

    label: str = "runpath-app"
    "A label steps can read off the shared root (e.g. for a log-line tag)."
    ("--label",)

    dry_run: bool = False
    "Steps may check this and skip side effects (none of these example steps have real ones)."
    ("--dry-run",)

    def _tag_line_(self, message: str) -> str:
        """Format ``message`` tagged with this instance's own ``label`` field.

        A real, inherited METHOD -- reachable on a parsed ``rc`` instance only
        because ``duho.runpath.register(base=RunpathAppArgs)`` (below) makes
        every RunPathCmd this module builds actually inherit this class, not
        just copy its data fields (see the module docstring).
        """
        return f"[{self.label}] {message}"


if __name__ == "__main__":
    # Registered here (not at import time) so importing this module alone --
    # e.g. for its RunpathAppArgs class -- never has the side effect of
    # rebinding every RunPathCmd this process builds.
    duho.runpath.register(base=RunpathAppArgs)
    rc_command = CmdBuilder("rc", _RC_DIR).command
    sys.exit(duho.app(RunpathAppArgs, commands=[rc_command], name="runpath-app"))
