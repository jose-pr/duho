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

**A shared global-options root** (``DiscoveryAppArgs``): every command
receives the SAME parsed root instance, so a
field declared here is available to every subcommand without redeclaring it,
and ``args._logger_`` (from ``LoggingArgs``) is one shared, correctly-named
logger every command body logs through -- not a fresh ``print()`` per file.
The root also exercises duho's config/env layering (``_config_``,
``NS(env=...)``) and a few more ``Arg`` helper factories
(``Choice``/``Count``/``Append``) that ``examples/fileinstall.py`` doesn't
already cover (that example is the one for Union/Enum types,
``NS(conflicts=...)``, ``NS(nargs="?")``, and a custom ``UpdateAction``).

``examples/discovery_cmds/`` has no ``__init__.py``, so this is a bare
directory of loose command files:

* ``greet.py`` -- a MODULE command: plain ``register(parser, args)`` +
  ``main(args)`` functions, no class needed. Logs via ``args._logger_``, and
  reads the shared ``args.label``/``args.tags``.
* ``status.py`` -- a MODULE command exercising the full
  ``init``/``main``/``success``/``finally_`` lifecycle
  (:class:`duho.discovery.ModuleCommand`), also logging via ``args._logger_``.
* ``whoami.py`` -- a CLASS command: discovery also picks up a ``Cmd``
  subclass sitting in a loose file, no different from a module command as far
  as the CLI surface is concerned. Logs via ``self._logger_``.

None of these three files import each other or know they're siblings --
``discover_commands`` walks the directory, imports each file under a
synthesized-unique module name (so a loose file can never clobber a stdlib
module of the same name), and classifies what it finds.

Run it::

    python examples/discovery_app.py greet World --shout
    python examples/discovery_app.py -v greet World          # -v: DEBUG logging
    python examples/discovery_app.py --tags a --tags b greet World
    python examples/discovery_app.py --format json greet World
    python examples/discovery_app.py status
    python examples/discovery_app.py whoami

    # config file / env var layering (CLI > env > config > class default):
    # examples/discovery_app.toml ships with `label = "from-config"`.
    python examples/discovery_app.py greet World            # label == "from-config"
    DISCOVERY_APP_LABEL=from-env python examples/discovery_app.py greet World
"""
import sys
from pathlib import Path

import duho
from duho import Append, Arg, Choice, Count, LoggingArgs, NS

_CMDS_DIR = Path(__file__).parent / "discovery_cmds"
_CONFIG_PATH = Path(__file__).parent / "discovery_app.toml"


class DiscoveryAppArgs(LoggingArgs):
    """Global options shared by every discovery_app command.

    A data mixin, not the app root itself: ``duho.app(root=DiscoveryAppArgs,
    ...)`` combines it with ``duho.Cli``-equivalent app-runner behavior.
    Every command's parsed ``args`` IS (or carries) this instance, so its
    fields/``_logger_`` are available everywhere without any command
    redeclaring them.
    """

    #: A TOML/JSON config file, layered under env vars and CLI args -- see
    #: docs/guide/config.md. Top-level keys map to THIS class's fields;
    #: a table named after a subcommand maps to that subcommand's own fields.
    #: duho's own config loader requires the path to exist, so it's resolved
    #: absolute (works regardless of the caller's CWD) and the file genuinely
    #: ships alongside this one.
    _config_ = _CONFIG_PATH

    label: "Arg[str, NS(env='DISCOVERY_APP_LABEL')]" = "discovery-app"
    "A label commands can read off the shared root (e.g. for a log-line tag). Also settable via DISCOVERY_APP_LABEL or discovery_app.toml's `label` key."
    ("--label",)

    tags: "Arg[list, Append()]" = []
    "Repeatable free-form tags -- --tags a --tags b -> ['a', 'b']."
    ("--tags",)

    format: "Arg[str, Choice('text', 'json')]" = "text"
    "Output format commands may honor."
    ("--format",)

    retries: "Arg[int, Count()]" = 0
    "Retry-count knob; repeat the flag to increase (-r -r -r -> 3), same style as -v/-vv."
    ("-r", "--retries")

    def _tag_line_(self, message: str) -> str:
        """Format ``message`` per the shared ``label``/``tags``/``format`` fields.

        A METHOD on the shared root (not just data): every command calls
        ``args._tag_line_(...)`` instead of each reimplementing its own
        tag-formatting logic.
        """
        if self.format == "json":
            import json

            return json.dumps({"label": self.label, "tags": self.tags, "message": message})
        tag_suffix = f" [{','.join(self.tags)}]" if self.tags else ""
        return f"[{self.label}]{tag_suffix} {message}"


if __name__ == "__main__":
    sys.exit(duho.app(DiscoveryAppArgs, source=_CMDS_DIR, name="discovery-app"))
