"""MCP tool surface over a duho CLI (opt-in, standalone module).

Exposes the *same* ``Cmd``/``Cli`` classes that back a duho CLI as **MCP tools**
(Model Context Protocol -- https://modelcontextprotocol.io), with zero
redeclaration. duho already does the hard part: :func:`duho._introspect.get_clsargs`
/ :class:`~duho._introspect.ClsArgDeclaration` plus each field's
:class:`~duho.args.ArgumentBuilder` produce, per field, a type/default/docstring --
exactly the raw material an MCP tool's ``inputSchema`` needs. argparse is just
*one* frontend rendered from those declarations; this module is a second
frontend over the same data, dispatching through :func:`duho.run_command`
verbatim.

**Opt-in / off the core surface**, like ``duho.runpath``/``duho.fanout``/
``duho.scaffold``: core ``duho`` never imports this module, and it is
deliberately **not** on the top-level ``duho.*`` surface. Activate it with
``import duho.mcp`` or ``python -m duho.mcp <app>``.

**Zero-dep stdlib JSON-RPC over stdio** -- no MCP SDK dependency. ``json`` (and
``importlib.metadata``, transitively reachable via ``pkgutil.resolve_name``'s
import machinery for the ``<app>`` CLI arg) are imported **lazily**,
function-local, never at module top -- so ``import duho.mcp`` alone never pays
their cost, mirroring the zero-eager-import contract ``import duho`` already
keeps (see ``tests/test_config_json.py``, ``tests/test_entry_points.py``).

Three layers, thin glue between them:

* :func:`input_schema_for_command` / :func:`json_schema_for_field` -- step 1,
  the ``type -> JSON Schema`` emitter. Standalone and parser-free: it reads a
  command class's own field declarations directly (``cls._getargs_()`` +
  ``duho._introspect.get_clsargs(cls)``), not a built argparse parser.
* :func:`describe_tools` -- step 2. Walks the class's own STATIC
  ``_subcommands_`` tree (built once per root class and cached), reusing the
  same alias-dedup-by-identity pattern as ``duho.agenthelp.describe_parser``,
  so every ``Cmd`` in that tree becomes one MCP tool, namespaced
  ``parent.child`` when nested -- **except** a namespace node whose own
  subcommand is mandatory (``_subcommands_`` always registers a *required*
  ``argparse`` subparsers action): such a node can never itself dispatch
  successfully, so it is not listed as a tool at all; its own fields are
  still merged into every one of its descendants' schemas, and it is still
  reachable through them. Two kinds of tree are served: a ``Cmd``/``Cli``
  class's static ``_subcommands_`` tree (what :func:`serve` and
  ``python -m duho.mcp <app>`` take, so ``<app>`` there must be a
  ``Cmd``/``Cli`` subclass), and a full ``duho.app()`` tree -- class AND
  module commands from ``source=``/``commands=``/``CMDS_PATH`` -- served
  through the ``<NAME>_MCP`` environment trigger, an ``mcp_command``
  subcommand, or :func:`serve_running_app`.
* :func:`call_tool` -- step 3. Resolves a tool name back to its node in that
  same cached tree, synthesizes an argv for its FULL path (its own ancestors'
  fields, in order, with the subcommand name token between each level, then
  its own fields), and dispatches through the tree's single
  shared ROOT parser -- exactly the parser ``duho.main``/``duho.parse`` would
  build and parse, so env/config layering (``NS(env=...)``, ``_config_``),
  root globals, ``_passthrough_``, and ``LoggingArgs``' own ``-v``/``-q``/
  ``--loglevel`` verbosity setup all work over MCP exactly as they do on the
  CLI. Maps the result per the return convention (see below).

**Return convention**: during a call, stdout (and stderr) are
captured. A command returning ``None``/``0`` -> a success result whose one
``text`` content block is the captured stdout (empty string allowed). A
non-zero int -> ``isError: true``, text = captured stdout + a trailing
``"exit code: N"`` line. A JSON-serialisable object/list return -> passed
through as one ``text`` block holding its JSON dump. A command that calls
``sys.exit(...)``/raises ``SystemExit`` at RUN TIME (not during argument
parsing) is mapped the same way as a returned exit code, from ``exc.code``:
``None``/``0`` is success, an int is ``isError`` with an ``"exit code: N"``
line, anything else (e.g. ``sys.exit("message")``) is ``isError`` with that
message -- ``call_tool`` never lets a command's own ``SystemExit`` escape and
end the whole server process. Any OTHER raised exception during dispatch is
mapped to ``isError: true`` with the exception's ``type: message`` text.
``KeyboardInterrupt`` (and any other non-``SystemExit`` ``BaseException``) is
deliberately NOT caught, so it still stops the server.

**Malformed requests are a different kind of failure than a broken command**:
:func:`call_tool` raises :class:`UnknownToolError` for a tool name that does
not resolve (or names a namespace node -- see above), and
:class:`InvalidArgumentsError` when ``arguments`` is not a JSON object, fails
the tool's own ``inputSchema`` (an unknown property, a missing required one,
a value whose JSON type/``enum`` does not match, a numeric value outside its
``minimum``/``maximum``, or a collection over its ``maxItems``/
``maxProperties``), or -- discovered while synthesizing argv, since it
depends on the built parser tree rather than the schema alone -- supplies a
value that cannot be safely encoded at all (an unsafe positional, an unsafe
option value, or a negative counting-flag value; see :func:`call_tool`'s own
"Dispatch-identity guard" section). All of these are
:class:`ValueError` subclasses carrying a JSON-RPC ``.code`` (``-32602``,
"invalid params"); :func:`serve` maps them to a JSON-RPC *error response*,
never a tool result -- the request itself, not the target command, was
invalid. A problem in the dispatched command itself (a raised exception, a
non-zero exit, ``sys.exit``, or an argparse usage error from a value that WAS
schema-valid but the command still rejects) is still a normal tool result
with ``isError: true``.

**Documented v1 limitations**: a custom ``action=``/``type=`` field with no
registered override is passed through as a plain string, verbatim;
``NS(conflicts=...)`` exclusive groups are surfaced only as a note appended to
the tool's description text (no ``oneOf``/``not`` JSON Schema encoding yet); a
field that defaults to ``True`` and declares only short flags (no long flag)
cannot be turned back to ``False`` over MCP (there is no ``--no-<x>`` form to
emit) and raises rather than silently doing the wrong thing; a module command
whose ``register()`` hook adds its own subparsers is listed as that one tool;
the hand-made subparsers are not tools of their own: one is chosen through a
property named after the subparsers' ``dest``, and their own options are not
served; a value equal to
the literal string ``"--"`` is refused as a positional value and for a field
with only a short flag (argparse's own ``--`` end-of-options marker, and duho's
own ``_passthrough_`` split, make it unsafe there -- use the dedicated ``"--"``
array property instead, see :func:`_input_schema_for_node`); it is accepted as
a long-flag option value;
streaming/long-running commands are out of scope -- this is strictly one
request -> one result.

All union annotations are quoted so the module imports cleanly on Python 3.9.
"""

from __future__ import annotations

import argparse as _argparse
import contextlib as _contextlib
import datetime as _datetime
import io as _io
import logging as _logging
import enum as _enum
import os as _os
import pathlib as _pathlib
import pkgutil as _pkgutil
import sys as _sys
import typing as _ty
import weakref as _weakref

from .. import _compat as _compat
from .. import _introspect as _introspect
from .. import agenthelp as _agenthelp
from .. import parsers as _parsers
from ..args import ArgumentBuilder as _ArgumentBuilder
from ..args import Cmd as _Cmd
from ..args import _apply_layers as _apply_layers
from ..args import _ISOFORMAT_FACTORIES as _ISOFORMAT_FACTORIES
from ..args import _command_name as _command_name
from ..args import _escape_help as _escape_help
from ..args import _raw_config_values as _raw_config_values
from ..args import _raw_env_values as _raw_env_values
from ..args import _resolve_version as _resolve_version
from ..args import _setup_instance_logging as _setup_instance_logging
from .._fieldspec import _KVFactory as _KVFactory
from ..logging import _STDERR_HANDLER_TAG as _STDERR_HANDLER_TAG
from ..logging import log_exception as _log_exception
from ..runtime import _build_app_core as _build_app_core
from ..runtime import run_command as _run_command

_LOGGER = _logging.getLogger(__package__)

from ._errors import (
    UnknownToolError,
    InvalidArgumentsError,
)
from ._schema import (
    _NOT_DEFINED,
    _NONETYPE,
    _ISO_FORMAT_NAMES,
    _ISO_FORMATS,
    _JSON_SCALARS,
    _MAX_COUNT_VALUE,
    _MAX_ARRAY_ITEMS,
    _MAX_OBJECT_PROPERTIES,
    _schema_for_type,
    _is_required,
    _description_for,
    json_schema_for_field,
    input_schema_for_command,
)
from ._command import (
    McpCmd,
)
from ._tree import (
    _Node,
    _effective_cls,
    _TREE_CACHE,
    _walk_tree,
    _tree_for,
    _ServerCore,
    _core_for_class,
    _core_for_app,
    _is_namespace_node,
    _is_mcp_command_node,
    _drop_layer_satisfied,
    _own_dests,
    _step_field_names,
    _schema_for_action,
    _merge_bare_actions,
    _input_schema_for_node,
    _conflict_note,
    _tool_spec,
    describe_tools,
)
from ._argv import (
    _long_flag_or_first,
    _looks_like_negative_number,
    _reject_unsafe_positional,
    _emit_option,
    _sibling_names,
    _dest_action,
    _BOOL_ACTION_KINDS,
    _bool_action_kind,
    _synthesize_argv,
    _synthesize_argv_from_actions,
    _synthesize_step_argv,
)
from ._call import (
    _SCHEMA_TYPE_CHECKS,
    _matches_schema_type,
    _validate_arguments,
    _text_result,
    _systemexit_result,
    _muted_color,
    _rebound_stderr_logging,
    call_tool,
)
from ._protocol import (
    _SUPPORTED_VERSIONS,
    _SERVER_NAME,
    _error_response,
    _MAX_JSON_NESTING,
    _line_nesting_exceeds,
    _server_info,
    _handle_request,
)
from ._stdio import (
    serve_running_app,
    _write_message,
    _real_stdio_streams,
    serve,
)
from ._cli import (
    _resolve_app,
    main,
)

__all__ = [
    "json_schema_for_field",
    "input_schema_for_command",
    "describe_tools",
    "call_tool",
    "serve",
    "serve_running_app",
    "McpCmd",
    "main",
    "UnknownToolError",
    "InvalidArgumentsError",
]
