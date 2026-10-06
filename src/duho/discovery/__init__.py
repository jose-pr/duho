"""Command discovery: turn a class, module, import path, or directory into ``Cmd``s.

This module answers "give me the runnable commands living over there" for four
shapes of *there*:

* a :class:`~duho.Cmd` subclass -- already a command, used as-is;
* a command **module** -- a ``.py`` file whose top-level ``main``/``run``/``call``
  is the entrypoint, adapted to the command contract by :class:`ModuleCommand`
  (a plain wrapper -- it does NOT subclass ``types.ModuleType``);
* an **import path** (dotted qualname) or a **filesystem path** -- resolved to a
  module by :class:`CmdBuilder`;
* a **package or directory** -- walked by :func:`discover_commands`, which
  collects both class commands and module commands from every submodule/file.

Two design points worth calling out:

* **Resilience.** :func:`discover_commands` treats a single unimportable or
  unsupported command as skippable, not fatal: ``ImportError`` and
  ``NotImplementedError`` (and subclasses) on one command are logged and skipped
  so the rest still load. A genuinely broken command file (e.g. a
  ``SyntaxError``) is NOT swallowed -- it is a real bug the author wants
  surfaced. See :func:`discover_commands` for the exact caught set and rationale.
* **Injection hook.** :func:`register_command_provider` lets an external package
  teach :class:`CmdBuilder` how to build a command from a directory shape core
  duho does not itself understand (e.g. a directory of numbered step files),
  WITHOUT core duho importing that package. If no provider matches, a directory
  or module is imported normally.

All union annotations are quoted so the module imports cleanly on Python 3.9.
"""

import importlib as _importlib
import inspect as _inspect
import logging as _logging
import os as _os
import sys as _sys
import typing as _ty
from pathlib import Path as _Path
from types import ModuleType as _ModuleType

from .. import _compat as _compat
from ..args import Args as _Args, Cmd as _Cmd, _command_name as _command_name
from ..env import _BARE_DRIVE_RE as _BARE_DRIVE_RE
from ..logging import log_exception as _log_exception
from ..qualname import PythonName as _PythonName

from ._command import (
    _LOGGER,
    _HOOK_LOGGER,
    _ENTRYPOINT_NAMES,
    _LIFECYCLE_NAMES,
    Command,
    is_class_command,
    is_module_command,
    _own_callable,
    _module_entrypoint,
    _resolved_module_name,
    ModuleCommand,
    _noop,
    _init_noop,
)
from ._importing import (
    _unique_module_name,
    _IMPORTED_BY_PATH,
    _import_from_path,
    import_from_path,
)
from ._providers import (
    _PROVIDERS,
    register_command_provider,
    unregister_command_provider,
    _match_provider,
)
from ._builder import (
    CmdBuilder,
)
from ._scan import (
    _iter_class_commands,
    _commands_in_module,
    _importable_spec,
    _looks_like_path,
    _is_empty_source,
    _is_bare_drive_source,
    discover_commands,
    _discover_from_package,
    _discover_from_path,
    _module_inside,
)
from ._entrypoints import (
    _coerce_entry_point_command,
    discover_entry_points,
)

# `_command_name` used to be a byte-for-byte copy of `args._command_name`
# (itself re-derived a THIRD time in `runtime.py` and inlined again in
# `mcp.py`) -- imported directly instead, so there is exactly one definition
# (the own-class-dict rule lives there) shared by every reader that needs
# a command's subcommand name: `args.py` itself, `runtime.py`, `mcp.py`, and
# `presets.LoggingArgs._logger_`.

__all__ = [
    "Command",
    "ModuleCommand",
    "CmdBuilder",
    "register_command_provider",
    "unregister_command_provider",
    "import_from_path",
    "discover_commands",
    "discover_entry_points",
    "is_class_command",
    "is_module_command",
]
