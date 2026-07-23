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

**A real limitation, not silently worked around**: unlike a plain module
command (whose ``args`` parameter genuinely IS an instance of whatever root
class you pass), the RunPath provider builds its OWN ``RunPathCmd`` subclass
per directory (see ``duho.runpath._build_runpath_command``) -- it does not
multiply-inherit a custom root class, so a METHOD declared on
``RunpathAppArgs`` would NOT be callable on the parsed ``rc`` instance (only
its DATA fields propagate, via argparse's namespace, not real class
inheritance). Any shared BEHAVIOR here is therefore a plain module-level
helper function (``format_tag_line(cmd, message)``) taking the instance as
its first argument, rather than a bound method -- verified empirically
against this exact combination before writing it this way.

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
import duho.runpath  # noqa: F401 -- import activates the RunPath provider
from duho import LoggingArgs
from duho.discovery import CmdBuilder
from duho.runpath import RunPathCmd

_RC_DIR = Path(__file__).parent / "rc"


class RunpathAppArgs(LoggingArgs):
    """Global options shared by every runpath_app command.

    Same shape as ``discovery_app.py``'s ``DiscoveryAppArgs`` -- a data
    mixin passed as ``duho.app``'s ``root``. See the module docstring for
    why this example uses a plain function, not a method, for shared
    behavior.
    """

    label: str = "runpath-app"
    "A label steps can read off the shared root (e.g. for a log-line tag)."
    ("--label",)

    dry_run: bool = False
    "Steps may check this and skip side effects (none of these example steps have real ones)."
    ("--dry-run",)


def format_tag_line(cmd: RunPathCmd, message: str) -> str:
    """Format ``message`` tagged with ``cmd.label`` -- a plain function, not
    a method, since ``cmd`` (a provider-built ``RunPathCmd`` subclass) does
    NOT inherit ``RunpathAppArgs``'s methods, only its DATA fields (see the
    module docstring's "real limitation" note)."""
    label = getattr(cmd, "label", "runpath-app")
    return f"[{label}] {message}"


if __name__ == "__main__":
    rc_command = CmdBuilder("rc", _RC_DIR).command
    sys.exit(duho.app(RunpathAppArgs, commands=[rc_command], name="runpath-app"))
