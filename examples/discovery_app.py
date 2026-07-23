#!/usr/bin/env python3
"""``duho.discover_commands`` / ``duho.app(source=...)``: regular filepath discovery.

Shows the "commands come from a plain directory of loose ``.py`` files"
discovery path -- the one used when you point ``app(source=...)`` (or
``discover_commands`` directly) at an ordinary directory. Contrast with
``runpath_app.py``, which points at a directory with the SAME shape (a plain
folder of ``.py`` files) but no ``__init__.py`` and ``NN-name.py`` filenames,
which routes it to the RunPath ("rc") provider instead -- the split between
the two is entirely about filenames and the presence/absence of
``__init__.py``, not about calling a different API.

``examples/discovery_cmds/`` has no ``__init__.py``, so this is a bare
directory of loose command files:

* ``greet.py`` -- a MODULE command: plain ``register(parser, args)`` +
  ``main(args)`` functions, no class needed.
* ``status.py`` -- a MODULE command exercising the full
  ``init``/``main``/``success``/``finally_`` lifecycle
  (:class:`duho.discovery.ModuleCommand`).
* ``whoami.py`` -- a CLASS command: discovery also picks up a ``Cmd``
  subclass sitting in a loose file, no different from a module command as far
  as the CLI surface is concerned.

None of these three files import each other or know they're siblings --
``discover_commands`` walks the directory, imports each file under a
synthesized-unique module name (so a loose file can never clobber a stdlib
module of the same name), and classifies what it finds.

Run it::

    python examples/discovery_app.py greet World --shout
    python examples/discovery_app.py status
    python examples/discovery_app.py whoami
"""
import sys
from pathlib import Path

import duho

_CMDS_DIR = Path(__file__).parent / "discovery_cmds"


if __name__ == "__main__":
    sys.exit(duho.app(source=_CMDS_DIR, name="discovery-app"))
