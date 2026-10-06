"""Multi-command app runner: wire discovered commands into a runnable app.

This is the driver layer that turns a set of :class:`~duho.discovery.Command`
objects (class commands -- ``Cmd`` subclasses -- and module commands --
:class:`~duho.discovery.ModuleCommand`) into a real subcommand app:

* build a top-level parser for a *root* ``Cmd``/``Args`` (global options);
* add a subparsers tree and register every command under it;
* parse ``argv`` (with ``_passthrough_`` and the nested-help / shared-namespace
  behaviors preserved);
* dispatch exactly one selected command through the lifecycle
  ``init -> main -> success / finally_`` with a shared **context**.

**Composed on the shipped parser, not a parallel one.** The whole point of this
layer is that it reuses duho's existing ``_parser_``/``_initparser_``/``"#cls"``
machinery rather than introducing a second parser class. The four parser
behaviors clients rely on are reproduced on that path:

* **Parent-arg inheritance** -- every subcommand parser is built with argparse
  ``parents=[<root parser>]`` so global/root options appear on each subcommand.
* **Shared namespace** -- class commands already carry the ``"#cls"``
  deepest-selection contract (``_initparser_``), which yields one merged instance
  of the deepest selected class. A module command's parsed instance stays the
  ROOT instance (plus any fields a module ``register`` hook, or its own declared
  ``Args`` class, added directly) -- it is never itself constructed as a duho
  class, unlike a class command.
* **Nested-help suppression** -- the optional two-pass prepass uses the existing
  :func:`duho.parsers.prerun_parse` (``quiet=True``), which detaches the
  subparsers action and silences every terminal action (help, version,
  print-completion, help-agents) and any parse error for the duration of the
  call, restoring all of it before returning. No hand-patching.
* **``register`` hook** -- a module command may define ``register(parser, args)``
  (or the arity-tolerant ``register(parser, args, logger)``) to add arguments
  directly on the argparse object of its subcommand.

* **``_passthrough_``** -- argv after the first literal ``--`` is captured by the
  root parser's patched ``parse_known_args`` and reaches the dispatched command.

All union annotations are quoted so the module imports cleanly on Python 3.9.
No target fan-out / thread pools live here -- a single command is dispatched.
Parallel/fan-out patterns are a documented client wrapper and a future add-on.
"""

import argparse as _argparse
import inspect as _inspect
import logging as _logging
import typing as _ty
from pathlib import Path as _Path

from .. import _compat as _compat
from .. import logging as _duho_logging
from .. import parsers as _parsers
from ..args import (
    Args as _Args,
    Cmd as _Cmd,
    _add_fields as _add_fields,
    _apply_default_layers_one as _apply_default_layers_one,
    _escape_description as _escape_description,
    _escape_help as _escape_help,
    _maybe_await as _maybe_await,
    _maybe_serve_mcp_trigger as _maybe_serve_mcp_trigger,
    _keep_attached_double_dash as _keep_attached_double_dash,
    _patch_parser_for_reorder as _patch_parser_for_reorder,
    _resolve_config_dict as _resolve_config_dict,
    _setup_instance_logging as _setup_instance_logging,
    _stash_layer_state as _stash_layer_state,
    _suppress_inherited_defaults as _suppress_inherited_defaults,
)
from ..discovery import (
    Command as _Command,
    ModuleCommand as _ModuleCommand,
    _command_name as _command_name,
    _noop as _discovery_noop,
    discover_commands as _discover_commands,
    discover_entry_points as _discover_entry_points,
    is_class_command as _is_class_command,
    is_module_command as _is_module_command,
)
from ..logging import log_exception as _log_exception

if _ty.TYPE_CHECKING:  # pragma: no cover - type-checking only
    from ..env import Env as _Env

_LOGGER = _logging.getLogger(__package__)

from ._arity import accepts_positional
from ._run import (
    _reject_coroutine,
    run_command,
)
from ._resolve import (
    _cmds_path_commands,
    _merge_discovered,
    _resolve_commands,
    _full_names,
)
from ._mcpcmd import (
    _resolve_mcp_command_name,
    _existing_command_names,
    _build_mcp_command_class,
)
from ._completioncmd import (
    _resolve_completion_command_name,
    _build_completion_command_class,
)
from ._register import (
    _register_class_command,
    _wants_logger_arg,
    _wants_logger_by_keyword,
    _conflicting_option_strings,
    _module_args_cls,
    _add_module_declared_fields,
    _register_module_command,
)
from ._parser import (
    _build_parser,
    _deregister_subparser,
    _apply_app_config_layers,
    _prepare_app_parser,
)
from ._tree import (
    _register_commands,
    _finalize_command_tree,
)
from ._app import (
    _run_app,
    app,
    _make_post_parse_dispatch,
    _build_app_core,
)

__all__ = ["run_command", "app", "accepts_positional"]
