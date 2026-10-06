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
  reachable through them. Only the static class tree is exposed -- a command
  reached only via ``duho.app``'s dynamic ``source=``/``commands=``/
  ``CMDS_PATH`` resolution, or a module command, is out of scope for v1; the
  ``<app>`` given to :func:`serve`/``python -m duho.mcp`` must be a
  ``Cmd``/``Cli`` subclass.
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
emit) and raises rather than silently doing the wrong thing; a value equal to
the literal string ``"--"`` is refused as a positional value and for a field
with only a short flag (argparse's own ``--`` end-of-options marker, and duho's
own ``_passthrough_`` split, make it unsafe there -- use the dedicated ``"--"``
array property instead, see :func:`_input_schema_for_node`); it is accepted as
a long-flag option value;
streaming/long-running commands are out of scope -- this is strictly one
request -> one result.

All union annotations are quoted so the module imports cleanly on Python 3.9.
"""

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

from . import _compat as _compat
from . import _introspect as _introspect
from . import agenthelp as _agenthelp
from . import parsers as _parsers
from .args import ArgumentBuilder as _ArgumentBuilder
from .args import Cmd as _Cmd
from .args import _apply_layers as _apply_layers
from .args import _ISOFORMAT_FACTORIES as _ISOFORMAT_FACTORIES
from .args import _command_name as _command_name
from .args import _escape_help as _escape_help
from .args import _raw_config_values as _raw_config_values
from .args import _raw_env_values as _raw_env_values
from .args import _resolve_version as _resolve_version
from .args import _setup_instance_logging as _setup_instance_logging
from ._fieldspec import _KVFactory as _KVFactory
from .logging import _STDERR_HANDLER_TAG as _STDERR_HANDLER_TAG
from .logging import log_exception as _log_exception
from .runtime import _build_app_core as _build_app_core
from .runtime import run_command as _run_command

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

_LOGGER = _logging.getLogger(__name__)

_NOT_DEFINED = _introspect.NOT_DEFINED
_NONETYPE = type(None)

#: MCP protocol versions this server understands, newest first. ``initialize``
#: echoes the client's own ``protocolVersion`` when it is one of these;
#: otherwise it answers with the first (newest) entry.
_SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

#: Fallback ``serverInfo.name`` for the ``initialize`` result
#: (:func:`_server_info`), used only when the served app's own resolution
#: somehow comes up empty. The NORMAL case reports the app's own identity
#: instead: ``name`` is the same root tool-name segment
#: ``describe_tools``/``call_tool`` use, and ``version`` is the app's own
#: ``_version_`` when it resolves to a string -- so a host can tell one
#: served APP apart from another, not just one duho release from another.
#: There is deliberately NO duho-version fallback for ``version``:
#: reporting duho's own release as the served app's version is actively
#: misleading (e.g. a served app with no ``_version_`` of its own used to
#: report duho's version as if it were its own), so an app with no
#: resolvable version reports the empty string instead -- see
#: :func:`_server_info`.
_SERVER_NAME = "duho.mcp"

#: JSON Schema ``format`` hint for each ISO-format stdlib type. All three
#: collapse to ``"type": "string"`` -- same as ``pathlib.Path`` -- since JSON
#: Schema has no native date type.
_ISO_FORMAT_NAMES = {
    _datetime.date: "date",
    _datetime.datetime: "date-time",
    _datetime.time: "time",
}

#: The actual lookup used below, built from ``args._ISOFORMAT_FACTORIES``'s
#: own KEYS rather than a second, independently hand-kept type list --
#: adding/removing an ISO type there now surfaces here as a loud ``KeyError``
#: (a missing format name) instead of the MCP schema silently disagreeing
#: with what the CLI itself accepts.
_ISO_FORMATS = {tp: _ISO_FORMAT_NAMES[tp] for tp in _ISOFORMAT_FACTORIES}

#: Scalar Python type -> JSON Schema ``"type"`` name, shared by the Literal
#: branch and the final scalar fallback of :func:`_schema_for_type`.
_JSON_SCALARS = {bool: "boolean", int: "integer", float: "number", str: "string"}

#: Upper bound published (as JSON Schema ``maximum``) and enforced (see
#: :func:`_validate_arguments`) for a counting flag (``-v``/``-q`` style,
#: ``action="count"``) over MCP. An LLM-controlled value has no reason to
#: exceed this -- the CLI itself only ever accumulates one per typed flag --
#: and an unbounded one used to synthesize (and argparse-parse) millions of
#: repeated tokens, stalling the single-threaded stdio server for the
#: duration of one call (a denial of service against every OTHER pending
#: request).
_MAX_COUNT_VALUE = 10

#: Upper bound published (as JSON Schema ``maxItems``/``maxProperties``) and
#: enforced (see :func:`_validate_arguments`) for a ``list``/``set``/``tuple``
#: field's array schema, a ``dict`` field's object schema, and the synthetic
#: ``"--"`` passthrough array (see :func:`_input_schema_for_node`). An
#: LLM-controlled collection has no reason to exceed this -- a bound the
#: server enforces BEFORE synthesizing argv or dispatching keeps a huge
#: client-supplied collection from stalling the single-threaded stdio server
#: (measured: tens of seconds for a six-figure ``dict``/``list`` argument,
#: whatever the underlying cost -- capping the input size makes the cost
#: moot regardless of where it lives).
_MAX_ARRAY_ITEMS = 1000
_MAX_OBJECT_PROPERTIES = 1000


class UnknownToolError(ValueError):
    """Raised by :func:`call_tool` for a tool name that does not resolve to a
    callable node in the root class's tree (including a namespace node whose
    own subcommand is mandatory -- see the module docstring). Maps to a
    JSON-RPC ``-32602`` error response in :func:`serve`, never a tool result.
    """

    code = -32602


class InvalidArgumentsError(ValueError):
    """Raised by :func:`call_tool` when ``arguments`` is not a JSON object, or
    fails the target tool's own ``inputSchema`` (an unknown property, or a
    value whose JSON type does not match). Maps to a JSON-RPC ``-32602`` error
    response in :func:`serve`, never a tool result.
    """

    code = -32602


# --------------------------------------------------------------------------
# Step 1: type -> JSON Schema
# --------------------------------------------------------------------------


def _schema_for_type(tp: object) -> "dict":
    """Map one declared annotation to a JSON Schema type fragment (no title/description).

    Standalone recursive dispatch, mirroring ``duho.args._factory_for``'s own
    branch order but targeting JSON Schema instead of argparse kwargs:

    * ``Literal[...]`` -> ``enum`` (+ ``type`` when every literal shares one
      JSON-representable type; a mixed-type literal is ``enum`` alone). Each
      value is passed through ``duho.agenthelp._jsonable`` first, so a
      ``Literal`` of ``Enum`` members renders by member NAME (matching every
      other Enum-shaped schema here) instead of the raw member object
      (which is not JSON-serialisable at all).
    * an ``Enum`` subclass -> ``{"type": "string", "enum": [member names]}``
      (member NAME, not value -- reuses :func:`duho.agenthelp._enum_members`,
      duho's standing convention).
    * ``list[T]`` -> ``array`` with ``items`` = ``T``'s own schema, capped at
      ``maxItems`` (:data:`_MAX_ARRAY_ITEMS`) -- enforced by
      :func:`_validate_arguments` before dispatch, so an oversized
      LLM-supplied collection is refused as a malformed request rather than
      synthesized into argv.
    * ``set[T]`` -> ``array`` + ``uniqueItems: true`` + the same ``maxItems``.
    * ``tuple[T, ...]`` / bare ``tuple`` -> ``array`` (only the variadic
      homogeneous shape reaches here -- a fixed-length ``tuple[A, B]``
      annotation already raised at ``cls._getargs_()``-build time, before any
      of this module's functions run, so it never needs defensive handling
      here) + the same ``maxItems``.
    * ``dict[str, V]`` / bare ``dict`` -> ``object`` with
      ``additionalProperties`` = ``V``'s own schema, capped at
      ``maxProperties`` (:data:`_MAX_OBJECT_PROPERTIES`), enforced the same way.
    * ``Optional[T]`` / a ``Union`` -> ``None`` is stripped; a single
      remaining member recurses into that member's own schema (no ``anyOf``
      wrapping for the common ``Optional[T]`` case); more than one remaining
      member -> ``{"anyOf": [...]}``. (Required-ness for ``Optional[T]`` is
      handled separately, from the *builder*, in
      :func:`json_schema_for_field` -- this function only ever describes a
      TYPE shape.)
    * ``pathlib.Path`` (or any ``PurePath`` subclass) -> ``"string"`` (as it
      already collapses for argparse).
    * ``datetime.date``/``datetime``/``time`` -> ``"string"`` + a ``format``
      hint (not required by the base type table; a low-risk, easy addition
      since duho already special-cases these three for argparse).
    * ``str``/``int``/``float``/``bool`` -> ``string``/``integer``/``number``/
      ``boolean``.
    * anything else (a custom ``Argument`` type, a plain class with no
      special handling, ...) -> ``"string"`` -- the documented v1 escape
      hatch: passed through as text rather than a silently wrong schema.
    """
    origin = _ty.get_origin(tp)
    args = _ty.get_args(tp)

    if origin is _ty.Literal:
        values = list(args)
        types = {type(v) for v in values}
        schema: "dict" = {"enum": [_agenthelp._jsonable(v) for v in values]}
        if len(types) == 1:
            json_type = _JSON_SCALARS.get(next(iter(types)))
            if json_type:
                schema["type"] = json_type
        return schema

    if isinstance(tp, type) and issubclass(tp, _enum.Enum):
        return {"type": "string", "enum": _agenthelp._enum_members(tp)}

    if origin is list or tp is list:
        elem = args[0] if args else str
        return {
            "type": "array",
            "items": _schema_for_type(elem),
            "maxItems": _MAX_ARRAY_ITEMS,
        }

    if origin is set or tp is set:
        elem = args[0] if args else str
        return {
            "type": "array",
            "items": _schema_for_type(elem),
            "uniqueItems": True,
            "maxItems": _MAX_ARRAY_ITEMS,
        }

    if origin is tuple or tp is tuple:
        elem = args[0] if args else str
        return {
            "type": "array",
            "items": _schema_for_type(elem),
            "maxItems": _MAX_ARRAY_ITEMS,
        }

    if origin is dict or tp is dict:
        val = args[1] if len(args) > 1 else str
        return {
            "type": "object",
            "additionalProperties": _schema_for_type(val),
            "maxProperties": _MAX_OBJECT_PROPERTIES,
        }

    if origin in _compat.UNION_ORIGINS:
        members = [a for a in args if a is not _NONETYPE]
        if len(members) == 1:
            return _schema_for_type(members[0])
        if len(members) > 1:
            return {"anyOf": [_schema_for_type(m) for m in members]}
        return {"type": "string"}

    if isinstance(tp, type) and issubclass(tp, _pathlib.PurePath):
        return {"type": "string"}

    if tp in _ISO_FORMATS:
        return {"type": "string", "format": _ISO_FORMATS[tp]}

    json_type = _JSON_SCALARS.get(tp)
    if json_type:
        return {"type": json_type}

    return {"type": "string"}


def _is_required(builder: "_ArgumentBuilder") -> bool:
    """Whether a field must be supplied (no usable default at all).

    Derived from ``builder._kwargs()`` -- the SAME kwargs
    ``add_to_parser``/``_effective_default_`` use -- rather than
    ``builder.default``/``builder.required`` alone, so the schema's
    required-ness never diverges from what argparse actually enforces:

    * an explicit ``NS(required=True)`` always wins, even when the field also
      carries a default (an unusual but legal combination -- the CLI still
      demands the flag).
    * otherwise a field whose ``_kwargs()`` carries a ``"default"`` (e.g. a
      bare ``flag: bool`` field's implicit ``store_true`` default of
      ``False``) is not required.
    * ``nargs`` of ``"?"``/``"*"`` (an optional positional, or a repeatable
      one) is never required, regardless of how that ``nargs`` was set
      (derived, or an explicit ``NS(nargs=...)`` override).
    * ``nargs="+"`` on a POSITIONAL is always required, even when the field
      also carries a python-level default (e.g. ``NS(nargs="+")`` with
      ``= []``) -- argparse itself demands at least one token for a ``"+"``
      positional regardless of any default, so a schema reporting this as
      optional would let a client omit it and then hit a plain argparse
      usage error on dispatch.
    * a positional with none of the above is a mandatory positional.
    * otherwise, an OPTION's own resolved ``required`` kwarg (argparse's own
      "no default -> required" rule, already computed by ``_kwargs()``,
      including the "member of a conflicts= group is never required" and
      "count/store_const/append_const/store_false get their own resting
      default" cases).
    """
    kwargs = builder._kwargs()
    if kwargs.get("required") is True:
        return True
    if kwargs.get("nargs") == "+" and builder.is_positional:
        return True
    if "default" in kwargs:
        return False
    if kwargs.get("nargs") in ("?", "*"):
        return False
    if builder.is_positional:
        return True
    return bool(kwargs.get("required", False))


def _description_for(
    decl: "_introspect.ClsArgDeclaration | None", builder: "_ArgumentBuilder"
) -> str:
    """The MCP description text for one field.

    An explicit ``NS(help=...)``/``Meta(help=...)`` override wins verbatim
    (MCP text is never argparse-``%``-expanded, so it needs no escaping); the
    OLD code always preferred the field's docstring, which silently ignored
    an explicit override. Falls back to the field's own raw (UNescaped)
    docstring -- ``builder.help`` holds the ``%``-escaped copy of the same
    text when no override was given (escaped for argparse's own
    ``%``-expansion, irrelevant here). ``help=argparse.SUPPRESS`` hides the
    field's description entirely, rather than leaking the literal
    ``"==SUPPRESS=="`` sentinel string.
    """
    raw_help = builder.help
    if callable(raw_help):
        raw_help = raw_help()
    if raw_help is _argparse.SUPPRESS:
        return ""
    docstring = decl.docstring if decl is not None else ""
    auto_derived = _escape_help(docstring or "")
    if raw_help and raw_help != auto_derived:
        return raw_help
    return docstring or raw_help or ""


def json_schema_for_field(
    decl: "_introspect.ClsArgDeclaration | None", builder: "_ArgumentBuilder"
) -> "tuple[dict, bool]":
    """Build ``(json_schema, required)`` for one field from its declaration + builder.

    Consumes the same per-field data ``duho.agenthelp`` collects (a field's
    ``ClsArgDeclaration`` from ``duho._introspect.get_clsargs`` and its
    ``ArgumentBuilder`` from ``cls._getargs_()``) without building a real
    argparse parser -- see the module docstring's "one real piece of work"
    deviation note for why (richer ``list[T]``/``dict[str, V]`` element-type
    fidelity than a stringified ``action`` type would give).

    ``required`` is computed by :func:`_is_required`; when the field is not
    required, an explicit ``"default"`` is set on the schema (the field's real
    default when one exists, else ``None`` for the ``Optional[T]``-with-no-
    default case) so an MCP client always sees what omitting the property
    resolves to. The description text is resolved by :func:`_description_for`.
    """
    tp = decl.type if decl is not None and decl.type is not _NOT_DEFINED else None
    schema = _schema_for_type(tp) if tp is not None else {"type": "string"}

    if builder._kwargs().get("action") == "count":
        # An LLM-controlled count has no reason to exceed this, or to be
        # negative (the CLI itself only ever accumulates upward); see
        # `_MAX_COUNT_VALUE` and `_validate_arguments`'s matching enforcement.
        schema["maximum"] = _MAX_COUNT_VALUE
        schema["minimum"] = 0

    required = _is_required(builder)
    if not required:
        effective_default = builder._effective_default_()
        if effective_default is not _NOT_DEFINED:
            schema["default"] = _agenthelp._jsonable(effective_default)
        else:
            schema.setdefault("default", None)

    help_text = _description_for(decl, builder)
    if help_text:
        schema["description"] = help_text

    return schema, required


def input_schema_for_command(cls: "type[_Cmd]") -> "dict":
    """Assemble a JSON-Schema ``object`` describing ``cls``'s own fields.

    ``properties``/``required``/``additionalProperties: false`` from
    ``cls._getargs_()`` (each field's ``ArgumentBuilder``, in declaration
    order) + ``duho._introspect.get_clsargs(cls)`` (the matching
    ``ClsArgDeclaration``, for the raw annotation + docstring). Only ``cls``'s
    OWN fields -- not an inherited root's, not framework-injected argparse-only
    actions (``--version``/``--help``/``--print-completion``/``--help-agents``,
    none of which have an ``ArgumentBuilder`` behind them, so they're never
    reached by this per-builder walk at all).

    This is the STANDALONE, single-class form (used directly, and by tests);
    the tool specs :func:`describe_tools` actually publishes for a NESTED
    command additionally merge in every ancestor's own fields and drop any
    field satisfiable from the server's environment/config -- see
    :func:`_input_schema_for_node`.
    """
    clsargs = _introspect.get_clsargs(cls)
    properties: "dict" = {}
    required: "list[str]" = []
    for builder in cls._getargs_():
        name = builder.name
        decl = clsargs.get(name)
        schema, is_required = json_schema_for_field(decl, builder)
        properties[name] = schema
        if is_required:
            required.append(name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


# --------------------------------------------------------------------------
# Step 2: describe_tools -- the cached command tree + schema merging
# --------------------------------------------------------------------------


class _Node:
    """One node of a command tree (a static ``_subcommands_`` class tree, or
    an ``app()``-built tree of class AND module commands).

    ``ancestors`` is the tuple of ``_Node`` from the root down to (but not
    including) this node's immediate parent -- empty for the root itself.
    ``cls`` is the duho class behind a CLASS-COMMAND node (via
    ``parser._duho_cls_``); ``None`` for a MODULE-COMMAND node. ``module_command``
    is the :class:`~duho.discovery.ModuleCommand` behind a module-command
    node, else ``None``. ``args_cls`` is a module command's own resolved
    declarative ``Args`` class (``runtime._module_args_cls``), when it
    declared one -- used for a richer schema/argv mapping than the bare
    argparse-actions fallback; ``None`` for a class-command node (its schema
    always comes from ``cls`` itself) or a module command with no declared
    ``Args`` of its own. A node is a module command iff ``module_command`` is
    not ``None``; ``cls`` and ``module_command`` are never both set.

    ``excluded`` is true for a non-root node whose own class/module opted
    out via ``_mcp_ = False`` (see :func:`_walk_tree`), OR that inherited the
    exclusion from an ancestor -- an excluded node's WHOLE subtree is
    excluded too. Checked by :func:`describe_tools` (never listed) and
    :func:`call_tool` (:class:`UnknownToolError`, same as an unknown name --
    no existence disclosed). Always ``False`` for the root itself: the
    root's own ``_mcp_`` keeps its separate, trigger-only meaning
    (:func:`duho.args._maybe_serve_mcp_trigger`), never this one.
    """

    __slots__ = (
        "dotted_name",
        "own_name",
        "parser",
        "cls",
        "ancestors",
        "module_command",
        "args_cls",
        "excluded",
    )

    def __init__(
        self,
        dotted_name,
        own_name,
        parser,
        cls,
        ancestors,
        module_command=None,
        args_cls=None,
        excluded=False,
    ):
        self.dotted_name = dotted_name
        self.own_name = own_name
        self.parser = parser
        self.cls = cls
        self.ancestors = ancestors
        self.module_command = module_command
        self.args_cls = args_cls
        self.excluded = excluded


def _effective_cls(node_or_step: "_Node") -> "_ty.Optional[type]":
    """The class to read field declarations from for one tree node: ``cls``
    for a class command, else its module command's own declared ``args_cls``
    (``None`` when it declared none -- the bare-actions fallback then
    applies)."""
    return node_or_step.cls if node_or_step.cls is not None else node_or_step.args_cls


#: root class -> (root_parser, {dotted_name: _Node}), built once per root
#: class (a naive implementation would rebuild the whole tree on every
#: whole tree). A ``WeakKeyDictionary`` so a throwaway root class (as tests
#: define per test) does not leak for the life of the process.
_TREE_CACHE: "_weakref.WeakKeyDictionary" = _weakref.WeakKeyDictionary()


def _walk_tree(
    root_parser: "_argparse.ArgumentParser",
    root_cls: "_ty.Optional[type]",
    root_name: str,
) -> "dict[str, _Node]":
    """Walk an ALREADY-BUILT (and, for a class tree, already layered) parser
    tree and return ``{dotted_name: _Node}`` -- the shared core both
    :func:`_tree_for` (a static ``_subcommands_`` class tree) and
    :func:`_core_for_app` (an ``app()``-built tree of class AND module
    commands) use.

    A subparser with no ``_duho_cls_`` (see :func:`~duho.args.Args._parser_`,
    which stashes it unconditionally on every class-command node) is checked
    for a module-command marker instead (``parser._defaults.get
    ("_duho_module_command_")``, set by ``runtime._register_module_command``
    via ``set_defaults`` -- readable straight off the ``dict`` before any
    parsing happens, so this needs no dry-run parse of its own) and, when
    present, its own resolved declarative ``args_cls`` (``runtime.
    _register_module_command`` stashes it as ``_duho_module_args_cls_``,
    ``None`` when the module declared none). Neither module commands nor
    class commands ever nest further module commands, but the walk itself
    makes no such assumption -- it simply recurses into whatever subparsers
    :func:`duho.parsers.unique_subcommands` finds.

    Also computes each node's :attr:`_Node.excluded` (per-command
    ``_mcp_ = False`` opt-out): a class node reads its own
    ``cls``'s ``_mcp_`` via plain ``getattr`` (so a subclass of an excluded
    command inherits the exclusion, never needing to redeclare it); a
    module-command node reads ``module_command``'s own ``_mcp_`` attribute
    (mirroring its ``_parsername_`` -- see ``discovery.ModuleCommand``).
    Never applied to the ROOT itself (``ancestors`` empty), whose own
    ``_mcp_`` keeps its separate trigger-only meaning. An excluded node's
    exclusion is inherited by its whole subtree via ``parent_excluded``.
    """
    nodes: "dict[str, _Node]" = {}
    seen: "set" = set()

    def _walk(parser, cls, dotted_parts, own_name, ancestors, parent_excluded=False):
        module_command = None
        args_cls = None
        if cls is None:
            defaults = getattr(parser, "_defaults", None) or {}
            module_command = defaults.get("_duho_module_command_")
            args_cls = getattr(parser, "_duho_module_args_cls_", None)
        is_root = not ancestors
        own_mcp_disabled = False
        if not is_root:
            if cls is not None:
                own_mcp_disabled = not getattr(cls, "_mcp_", True)
            elif module_command is not None:
                own_mcp_disabled = not getattr(module_command, "_mcp_", True)
        excluded = parent_excluded or own_mcp_disabled
        node = _Node(
            ".".join(dotted_parts),
            own_name,
            parser,
            cls,
            ancestors,
            module_command=module_command,
            args_cls=args_cls,
            excluded=excluded,
        )
        nodes[node.dotted_name] = node
        # A dispatch-identity marker (see `call_tool`'s guard against a
        # positional swallowing the literal subcommand-name token this
        # module inserts between chain levels): tag EVERY subparser with the
        # chain of own-names that reaches it. argparse re-runs each level's
        # defaults-installation as parsing descends (root's default is
        # installed first, but a DEEPER subparser's own `set_defaults` still
        # overwrites it once that subparser actually runs), so after a
        # successful parse this dest holds the tuple for the subparser
        # ACTUALLY reached -- not necessarily the one `call_tool` intended.
        parser.set_defaults(_duho_mcp_path_=dotted_parts)
        for canonical, _aliases, subparser in _parsers.unique_subcommands(parser, seen):
            sub_cls = getattr(subparser, "_duho_cls_", None)
            _walk(
                subparser,
                sub_cls,
                dotted_parts + (canonical,),
                canonical,
                ancestors + (node,),
                parent_excluded=excluded,
            )

    _walk(root_parser, root_cls, (root_name,), root_name, ())
    return nodes


def _tree_for(root_cls: "type[_Cmd]") -> "tuple":
    """Return (and cache) ``root_cls``'s ``(root_parser, {dotted_name: _Node})``.

    Builds ``root_cls._parser_()`` exactly once and applies the SAME env/
    config layering pipeline ``duho.main``/``duho.parse`` use
    (``duho.args._apply_layers``) to it exactly once, so every node's
    subparser -- reachable purely because they all share this one parser
    object's tree, built by duho's own static ``_subcommands_`` machinery --
    already carries its own env/config-table slice by the time any
    ``tools/call`` actually parses through it. Real per-call freshness for
    ``NS(env=...)`` is unaffected by this cache: env resolution itself
    happens lazily, inside ``parse_args()``, reading ``os.environ`` live
    every time -- only the loaded config-file CONTENT and the tree's
    structure are cached, matching ``duho.main``'s own one-load-per-process
    behavior.
    """
    cached = _TREE_CACHE.get(root_cls)
    if cached is not None:
        return cached

    root_parser = root_cls._parser_()
    _apply_layers(root_parser, root_cls, config=None)

    nodes = _walk_tree(root_parser, root_cls, root_parser.prog)

    result = (root_parser, nodes)
    _TREE_CACHE[root_cls] = result
    return result


class _ServerCore:
    """One MCP server's resolved ``(root_parser, nodes, dispatch, root_cls)``
    quadruple -- everything :func:`describe_tools`/:func:`call_tool`/
    ``initialize``'s ``serverInfo`` (:func:`_server_info`) need, independent
    of whether the tree came from a class's static ``_subcommands_``
    (:func:`_core_for_class`) or a full ``app()`` build
    (:func:`_core_for_app`). ``dispatch(command, instance)`` performs
    whichever post-parse steps that source normally performs (logging setup,
    ``_env_`` attachment, ...) and finally :func:`duho.runtime.run_command`.
    ``root_cls`` is the concrete root class either builder resolved (never
    ``None`` -- ``app()``'s own bare-root fallback is duho's internal
    ``Args`` class, still a real class), read by :func:`_server_info` for
    ``_version_`` resolution.
    """

    __slots__ = ("root_parser", "nodes", "dispatch", "root_cls")

    def __init__(self, root_parser, nodes, dispatch, root_cls):
        self.root_parser = root_parser
        self.nodes = nodes
        self.dispatch = dispatch
        self.root_cls = root_cls


def _core_for_class(root_cls: "type[_Cmd]") -> "_ServerCore":
    """Build a :class:`_ServerCore` for a class's static ``_subcommands_``
    tree -- the ``serve(root_cls)``/``python -m duho.mcp <app>`` path,
    unchanged from before this module grew ``app()`` support. ``dispatch``
    replicates exactly what :func:`call_tool` used to do inline: set up
    instance logging (always, matching the previous unconditional call), then
    :func:`duho.runtime.run_command`.
    """
    root_parser, nodes = _tree_for(root_cls)

    def _dispatch(command: object, instance: object) -> int:
        _setup_instance_logging(instance, True, root_cls)
        return _run_command(command, instance)

    return _ServerCore(root_parser, nodes, _dispatch, root_cls)


def _core_for_app(root: "type | None" = None, **app_kwargs: object) -> "_ServerCore":
    """Build a :class:`_ServerCore` for a full ``app()`` command tree --
    class AND module commands, from discovered files, ``CMDS_PATH``, entry
    points, or an explicit ``commands=`` list, exactly as ``duho.app`` itself
    would resolve them.

    Built ONCE (``runtime._build_app_core`` runs discovery/parser-build/
    registration/config-thread-down a single time; **not** a re-discovery per
    MCP tool call, matching :func:`_core_for_class`'s own one-build-per-server
    contract), then walked with the same :func:`_walk_tree` core the static
    class-tree path uses -- so module-command nodes are recognized right
    alongside class-command ones. ``**app_kwargs`` accepts every keyword
    :func:`duho.app` itself does (``commands``, ``source``, ``entry_points``,
    ``argv``, ``name``, ``description``, ``env``, ``config``) except
    ``setup_logging``/``dispatch``, which have no meaning for a server that
    dispatches once per MCP tool call rather than once per process.
    """
    parser, root_cls, dispatch = _build_app_core(root, **app_kwargs)
    # The root tool-name segment is the application's name, `parser.prog`.
    nodes = _walk_tree(parser, root_cls, parser.prog)
    return _ServerCore(parser, nodes, dispatch, root_cls)


def _is_namespace_node(parser: "_argparse.ArgumentParser") -> bool:
    """True when ``parser`` owns a MANDATORY subparsers action.

    duho's static ``_subcommands_`` tree always registers
    ``add_subparsers(..., required=True)``, so a ``Cli`` with subcommands can
    never itself dispatch successfully -- calling it always fails with
    argparse's own "the following arguments are required: <command>". Such a
    node is not published as an MCP tool at all (its fields are still merged
    into every descendant's schema by :func:`_input_schema_for_node`).
    """
    subparsers_action = _parsers.find_subparsers(parser)
    if subparsers_action is None:
        return False
    return bool(getattr(subparsers_action, "required", False))


class McpCmd(_Cmd):
    """Serve the CLI currently being dispatched as an MCP server (stdio).

    A ready-made building block: register a
    subclass of this under any name to add a self-serving MCP subcommand to
    a CLI, with zero server code of your own -- ``__call__`` just forwards
    to :func:`serve_running_app`. ``duho.app``'s own opt-in ``_mcp_command_``
    class attribute / ``mcp_command=`` kwarg do exactly this (a dynamically
    named subclass), but any app may register ``McpCmd`` (or a subclass
    adding, say, its own additional flags) under any name it likes, via
    ``_subcommands_``/``commands=``/self-registration -- there is nothing
    ``_mcp_command_`` does that isn't equally reachable by hand.

    A node whose class IS (or subclasses) ``McpCmd`` is never listed as an
    MCP tool, and never callable via ``call_tool`` either (see
    :func:`_is_mcp_command_node`) -- a client asking a live MCP server to
    recursively take over stdio again makes no sense.
    """

    transport: "_ty.Literal['stdio']" = "stdio"
    "MCP transport to serve this CLI over"
    ("--transport",)

    def __call__(self) -> int:
        return serve_running_app(self.transport)


def _is_mcp_command_node(node: "_Node") -> bool:
    """True when ``node``'s class IS (or subclasses) :class:`McpCmd` -- the
    self-serving command an app may register under any name. Checked
    alongside :func:`_is_namespace_node` everywhere a node's callability as
    an MCP tool matters (:func:`describe_tools`/:func:`call_tool`)."""
    return (
        node.cls is not None
        and isinstance(node.cls, type)
        and issubclass(node.cls, McpCmd)
    )


def serve_running_app(transport: str = "stdio") -> int:
    """Serve the CLI currently being dispatched as an MCP server, over
    ``transport`` (currently only ``"stdio"``).

    Reads the app context :func:`duho.args.main`/:func:`duho.runtime.app`
    record in a ``ContextVar`` (``duho._compat._MCP_CONTEXT``) around their
    own dispatch step -- the exact parser/root class/dispatch callable THAT
    invocation already built -- so serving here needs no rediscovery: this
    is the SAME tree a client would see calling any other tool on the same
    running process, just entered from inside a dispatched command (e.g.
    :class:`McpCmd`) instead of the ``<PREFIX>MCP``/``<NAME>_MCP`` env
    trigger.

    Raises ``RuntimeError`` when called outside such a dispatch (a bare
    script that never went through ``duho.main``/``duho.app`` at all has no
    running app context to serve). Raises ``ValueError`` for an unsupported
    ``transport`` -- checked BEFORE consulting the context, so it is
    reported the same way regardless of whether one exists.
    """
    if transport != "stdio":
        raise ValueError(
            "unsupported MCP transport %r (supported: stdio)" % (transport,)
        )
    ctx = _compat._MCP_CONTEXT.get()
    if ctx is None:
        raise RuntimeError(
            "serve_running_app() was called outside a duho.main()/duho.app() "
            "dispatch -- there is no currently-running app context to serve"
        )
    kind = ctx[0]
    if kind == "class":
        core = _core_for_class(ctx[1])
    else:
        _, parser, root_cls, dispatch = ctx
        nodes = _walk_tree(parser, root_cls, parser.prog)
        core = _ServerCore(parser, nodes, dispatch, root_cls)
    return serve(core)


def _drop_layer_satisfied(
    required: "list[str]", cls: type, parser: "_argparse.ArgumentParser"
) -> "list[str]":
    """Fields whose value can come from ``NS(env=...)``/``_config_`` even
    though the MCP call omits them are not required over MCP.

    Unlike a CLI user, an MCP client cannot see the server process's own
    environment or config file -- a required field satisfied by either would
    otherwise be unreachable (``tools/list`` demands it, yet supplying it
    would only ever override, never merely satisfy, the layer).
    """
    if not required:
        return required
    config_table = getattr(parser, "_duho_raw_config_table_", None) or {}
    satisfied = set(_raw_config_values(cls, config_table)) | set(_raw_env_values(cls))
    if not satisfied:
        return required
    return [name for name in required if name not in satisfied]


def _own_dests(parser: "_argparse.ArgumentParser") -> "_ty.Optional[set]":
    """The dest names ``runtime._register_module_command`` stashed as this
    module command's OWN (``_duho_module_own_dests_``) -- ``None`` for
    anything else (a class command, or the root of either tree kind), which
    callers read as "no filtering needed"."""
    return getattr(parser, "_duho_module_own_dests_", None)


def _step_field_names(step: "_Node") -> "list[str]":
    """Every field name ``step`` itself declares, for whichever kind of node
    it is: a class command's/module command's own declared ``Args`` fields
    (:func:`_effective_cls`, filtered to dests actually present on this
    subparser -- ``_add_fields(strict=False)`` silently SKIPS a module's
    declared field that collides with an inherited global, so it must not be
    treated as this step's own field either), or -- when neither exists (a
    bare module command, register()-hook fields or none at all) -- the
    dests :data:`_own_dests` names directly.
    """
    eff_cls = _effective_cls(step)
    if eff_cls is not None:
        own = _own_dests(step.parser)
        names = [b.name for b in eff_cls._getargs_()]
        if own is not None:
            names = [n for n in names if n in own]
        return names
    return sorted(_own_dests(step.parser) or ())


def _schema_for_action(action: "_argparse.Action") -> "tuple[dict, bool]":
    """Best-effort ``(json_schema, required)`` for one bare argparse
    ``Action``, with no duho field declaration behind it at all (a module
    command with no declared ``Args``, its fields added directly by a
    ``register()`` hook, or genuinely none). Mirrors
    ``duho.agenthelp``'s own builder-less fallback (`_describe_option`/
    `_describe_positional`) -- lower fidelity than :func:`json_schema_for_field`
    (no declared-annotation element types, no env/config provenance -- a
    bare action has neither), but enough for a client to call the tool.
    """
    is_positional = not action.option_strings
    choices = getattr(action, "choices", None)
    factory = getattr(action, "type", None)
    scalar = _JSON_SCALARS.get(factory) if isinstance(factory, type) else None
    if choices:
        schema: "dict" = {"type": scalar or "string", "enum": [str(c) for c in choices]}
    elif action.nargs == 0:
        schema = {"type": "boolean"}
    elif isinstance(action, _argparse._AppendAction) or action.nargs in ("*", "+"):
        schema = {
            "type": "array",
            "items": {"type": scalar or "string"},
            "maxItems": _MAX_ARRAY_ITEMS,
        }
    else:
        schema = {"type": scalar or "string"}
    if is_positional:
        required = action.nargs not in ("?", "*")
    else:
        required = bool(getattr(action, "required", False))
    if not required:
        schema.setdefault("default", _agenthelp._jsonable(action.default))
    help_text = action.help
    if help_text and help_text is not _argparse.SUPPRESS:
        schema["description"] = str(help_text).replace("%%", "%")
    return schema, required


def _merge_bare_actions(step: "_Node", properties: "dict") -> "tuple[list, list]":
    """:func:`_input_schema_for_node`'s per-step merge, for a step with
    neither ``cls`` nor ``args_cls`` -- derives fields straight from this
    subparser's OWN actions (:func:`_own_dests`/:func:`_schema_for_action`)
    rather than any duho field declaration."""
    own = _own_dests(step.parser) or set()
    level_names: "list[str]" = []
    level_required: "list[str]" = []
    for action in step.parser._actions:
        if action.dest not in own:
            continue
        schema, is_required = _schema_for_action(action)
        properties[action.dest] = schema
        level_names.append(action.dest)
        if is_required:
            level_required.append(action.dest)
    return level_names, level_required


def _input_schema_for_node(node: "_Node") -> "dict":
    """The full ``inputSchema`` :func:`describe_tools`/:func:`call_tool` use
    for one tree node: ``node``'s own fields merged with every ancestor's own
    fields (Decision -- a nested tool's schema must include its ancestors'
    fields, since MCP has no other way to supply a root/parent global to a
    dispatch that goes through the whole path at once), in root-to-leaf
    order so a field redeclared at a deeper level shadows the shallower one
    (schema shape AND required-ness), with any field satisfiable purely from
    the server's own environment/config dropped from ``required``, plus one
    synthetic ``"--"`` property (an array of strings, never required) for
    the trailing passthrough argv every duho command receives as
    ``_passthrough_`` -- see :func:`call_tool`, which appends it as a
    literal ``--`` token followed by its items at the very end of the
    synthesized argv, exactly where a human-typed CLI invocation would put
    it. Universal (every tool is reachable through the tree's single ROOT
    parser, whose own top-level parse always owns the ``--`` split), so
    every published tool advertises it, not just ones whose own ``__call__``
    happens to read ``self._passthrough_``.
    """
    properties: "dict" = {}
    required: "list[str]" = []
    for step in node.ancestors + (node,):
        eff_cls = _effective_cls(step)
        if eff_cls is None:
            # A bare module command (no declared Args of its own): only ever
            # true for the LEAF (module commands never have descendants), so
            # this branch cannot shadow/be shadowed by anything deeper.
            level_names, level_required = _merge_bare_actions(step, properties)
        else:
            clsargs = _introspect.get_clsargs(eff_cls)
            own = _own_dests(step.parser)
            level_names = []
            level_required = []
            for builder in eff_cls._getargs_():
                name = builder.name
                if own is not None and name not in own:
                    # Skipped at registration (`_add_fields(strict=False)`)
                    # because it collided with an inherited global -- that
                    # ancestor level already contributes this field.
                    continue
                level_names.append(name)
                decl = clsargs.get(name)
                schema, is_required = json_schema_for_field(decl, builder)
                properties[name] = schema
                if is_required:
                    level_required.append(name)
            # NOTE: for a module command, `step.parser` never carries a
            # `_duho_raw_config_table_` (only a class command's subparser
            # does -- `runtime._apply_app_config_layers` applies a module's
            # config slice EAGERLY instead of stashing it for later lookup),
            # so a config-satisfied (but not env-satisfied) module field is
            # still reported required here -- conservative, never a security
            # gap, just occasionally stricter than necessary over MCP.
            level_required = _drop_layer_satisfied(level_required, eff_cls, step.parser)
        for name in level_names:
            if name in required and name not in level_required:
                required.remove(name)
        for name in level_required:
            if name not in required:
                required.append(name)
    properties["--"] = {
        "type": "array",
        "items": {"type": "string"},
        "maxItems": _MAX_ARRAY_ITEMS,
        "default": [],
        "description": (
            "Arguments captured after a literal '--' separator, forwarded "
            "verbatim as the command's own _passthrough_ list."
        ),
    }
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _conflict_note(cls: "_ty.Optional[type]") -> str:
    """A short human-readable note for ``cls``'s ``NS(conflicts=...)`` groups.

    Exclusive groups are surfaced only as tool-description text in v1 (no
    ``oneOf``/``not`` JSON Schema encoding yet). Reuses
    ``duho.agenthelp._conflict_groups`` rather than re-deriving group
    membership. Returns ``""`` when the command declares no conflict groups,
    or (a bare module command with no declared ``Args`` at all) ``cls`` is
    ``None``.
    """
    if cls is None:
        return ""
    builders = {b.name: b for b in cls._getargs_()}
    groups = _agenthelp._conflict_groups(builders)
    if not groups:
        return ""
    parts = []
    for group in groups:
        qualifier = "exactly one required" if group["required"] else "at most one"
        parts.append("%s (%s)" % (", ".join(group["members"]), qualifier))
    return "Mutually exclusive: " + "; ".join(parts) + "."


def _tool_spec(node: "_Node") -> "dict":
    """Build one MCP ``{name, description, inputSchema}`` tool spec for ``node``."""
    description = (node.parser.description or "").replace("%%", "%").strip()
    input_schema = _input_schema_for_node(node)
    note = _conflict_note(_effective_cls(node))
    if note:
        description = (description + "\n\n" + note).strip() if description else note
    return {
        "name": node.dotted_name,
        "description": description,
        "inputSchema": input_schema,
    }


def describe_tools(root_cls: "_ty.Union[type, _ServerCore]") -> "list[dict]":
    """Describe every callable command in ``root_cls``'s tree as MCP tool specs.

    ``root_cls`` is a ``Cmd``/``Cli`` class (the static ``_subcommands_``
    tree path -- unchanged) or a :class:`_ServerCore` (an ``app()``-built
    tree, from :func:`_core_for_app`).

    Each command reached by walking the built parser tree -- the root itself
    (when it can itself be dispatched), and every subcommand, recursively --
    becomes one tool ``{name, description, inputSchema}``: a leaf/root tool is
    named after the application (its root parser's ``prog``, see
    :func:`duho.args._app_name`), a nested one ``parent.child``. A NAMESPACE
    node (one whose own subcommand is mandatory
    -- see :func:`_is_namespace_node`) is skipped: it can never itself
    dispatch successfully, so listing it would only ever waste a client's
    turn on a guaranteed usage error; its own fields are still reachable,
    merged into every one of its descendants' schemas. This applies equally
    to a class command AND a module command (an ``app()``-only concept --
    every ``ModuleCommand`` node is itself always a leaf, never a namespace).
    A node whose class is (or subclasses) :class:`McpCmd` -- the self-serving
    command an ``app()`` may register under any name -- is
    likewise skipped (see :func:`_is_mcp_command_node`): serving one MCP
    session from inside a tool call another MCP session made makes no sense.

    A node opted out via a per-command ``_mcp_ = False`` (see
    :attr:`_Node.excluded`, computed by :func:`_walk_tree`) is skipped too,
    together with its whole subtree -- the ROOT's own ``_mcp_`` is exempt
    (never treated as this kind of exclusion).
    """
    core = root_cls if isinstance(root_cls, _ServerCore) else _core_for_class(root_cls)
    return [
        _tool_spec(node)
        for node in core.nodes.values()
        if not _is_namespace_node(node.parser)
        and not _is_mcp_command_node(node)
        and not node.excluded
    ]


# --------------------------------------------------------------------------
# Step 3: argv synthesis + call_tool
# --------------------------------------------------------------------------


def _long_flag_or_first(builder: "_ArgumentBuilder") -> "tuple[str, bool]":
    """The flag to encode a value under, and whether it is a long flag.

    Prefers the first declared ``--long`` flag (a value can then always be
    attached with ``=``, which argparse never reinterprets); falls back to
    the field's sole flag for a short-flag-only field.
    """
    for flag in builder.flags:
        if flag.startswith("--"):
            return flag, True
    return builder.flags[0], False


def _looks_like_negative_number(token: str, parser: "_argparse.ArgumentParser") -> bool:
    """True when ``token`` is safe as a positional because argparse's OWN
    negative-number exemption would accept it (mirrors
    ``ArgumentParser._negative_number_matcher``/``_has_negative_number_optionals``).
    """
    matcher = getattr(parser, "_negative_number_matcher", None)
    if matcher is None or not matcher.match(token):
        return False
    return not getattr(parser, "_has_negative_number_optionals", None)


def _reject_unsafe_positional(
    token: str,
    parser: "_argparse.ArgumentParser",
    *,
    forbidden: "frozenset" = frozenset(),
) -> None:
    """Refuse a positional token argparse would parse as an option or as the
    ``--`` passthrough separator, rather than silently mis-parsing it or
    letting it leak into ``_passthrough_``. Also refuses a token equal to a
    name in ``forbidden`` -- ``call_tool`` passes the union of THIS level's
    own subcommand names (see :func:`_sibling_names`) and every ANCESTOR
    level's own subcommand names/aliases, so a client cannot set an
    ancestor's own optional/variadic positional field to a value that would
    read as a sibling selector -- AT THAT LEVEL OR ANY SHALLOWER ONE -- once
    appended ahead of it (a security-relevant guard: MCP tool arguments are
    LLM-controlled). Raises :class:`InvalidArgumentsError` rather than a bare
    ``ValueError``: a value that cannot be safely encoded as argv at all is a
    malformed REQUEST, not a command that ran and failed, so ``call_tool``
    lets it propagate as a JSON-RPC error instead of mapping it to a tool
    result's ``isError: true`` (see the module docstring's "Malformed
    requests" note)."""
    if token == "--" or (
        token.startswith("-")
        and token != "-"
        and not _looks_like_negative_number(token, parser)
    ):
        raise InvalidArgumentsError(
            "value %r cannot be passed as a positional argument: it would "
            "be parsed as an option (or the '--' passthrough separator)" % (token,)
        )
    if token in forbidden:
        raise InvalidArgumentsError(
            "value %r cannot be passed as a positional argument at this "
            "level: it collides with a subcommand name at this level or an "
            "ancestor level" % (token,)
        )


def _emit_option(
    argv: "list[str]",
    flag: str,
    is_long: bool,
    token: str,
    parser: "_argparse.ArgumentParser",
) -> None:
    """Append one option occurrence for ``token``: a long flag is
    always attached with ``=`` so argparse never reinterprets the value; a
    short-flag-only field refuses a value that looks like another option
    (there is no safe attached form for a short flag) -- also an
    :class:`InvalidArgumentsError`, the same request-level classification as
    :func:`_reject_unsafe_positional`. The literal value ``"--"`` is attached
    like any other, but only when ``parser`` keeps it (every parser duho
    builds does); an unpatched parser could silently drop it, so it is refused."""
    if token == "--" and not getattr(parser, "_duho_keeps_double_dash_", False):
        raise InvalidArgumentsError(
            "value '--' cannot be passed to %s: its parser was not built by "
            "duho and may drop an attached '--' value" % (flag,)
        )
    if is_long:
        argv.append("%s=%s" % (flag, token))
        return
    if token.startswith("-") and token != "-":
        raise InvalidArgumentsError(
            "value %r cannot be passed to %s: it has no long form to attach "
            "the value to safely" % (token, flag)
        )
    argv.extend([flag, token])


def _sibling_names(parser: "_argparse.ArgumentParser") -> "frozenset":
    """Every subcommand name (canonical + alias) registered DIRECTLY on
    ``parser`` -- empty when it has no subparsers action at all. Used to
    refuse a positional value that collides with one of THIS level's own
    choices, or (unioned with every ancestor's own call to this same
    function) an ANCESTOR's own choices -- see :func:`_reject_unsafe_positional`
    and ``call_tool``'s accumulation of ``ancestor_forbidden`` as it walks
    the chain. Scoped to one parser at a time, never the whole tree, so a
    same-named command living elsewhere (a different, unrelated node
    entirely) never triggers it by coincidence."""
    action = _parsers.find_subparsers(parser)
    if action is None:
        return frozenset()
    return frozenset(action.choices or ())


def _dest_action(
    parser: "_argparse.ArgumentParser", dest: str
) -> "_ty.Optional[_argparse.Action]":
    """The already-built ``argparse.Action`` registered for ``dest`` on
    ``parser``, or ``None``. Reading the REAL parser (built once by
    ``cls._parser_()`` + ``_apply_layers``, see :func:`_tree_for`) is what
    lets :func:`_bool_action_kind` tell a plain ``store_true`` apart from a
    layered field's ``BooleanOptionalAction`` -- recomputing the action from
    ``builder._kwargs()`` alone (with no ``layered=`` argument) silently
    disagreed with what got built whenever the field is env/config-layered
    (``Args._parser_()`` threads ``layered=True`` through at build time; a
    bare ``_kwargs()`` call defaults it to ``False``).
    """
    for action in parser._actions:
        if action.dest == dest:
            return action
    return None


#: `type(action).__name__` -> the bool-flag "kind" `_synthesize_argv` needs,
#: for the two argparse action classes with no public name of their own
#: (`argparse.BooleanOptionalAction` IS public and checked separately via
#: `isinstance`). Both class names have been stable, documented-by-behavior
#: argparse internals for the module's whole history.
_BOOL_ACTION_KINDS = {
    "_StoreTrueAction": "store_true",
    "_StoreFalseAction": "store_false",
}


def _bool_action_kind(action: "_ty.Optional[_argparse.Action]") -> "_ty.Optional[str]":
    """Classify ``action`` as ``"store_true"``/``"store_false"``/
    ``"boolean_optional"``, or ``None`` for anything else (including
    ``None`` itself, or an explicit non-bool ``action=`` override that
    happens to sit on a ``bool``-typed field, e.g. ``store_const``) -- the
    caller falls through to its OWN, unrelated handling for that case."""
    if action is None:
        return None
    if isinstance(action, _argparse.BooleanOptionalAction):
        return "boolean_optional"
    return _BOOL_ACTION_KINDS.get(type(action).__name__)


def _synthesize_argv(
    cls: type,
    arguments: "dict",
    parser: "_argparse.ArgumentParser",
    *,
    skip: "_ty.Optional[frozenset]" = None,
    ancestor_forbidden: "frozenset" = frozenset(),
) -> "list[str]":
    """Turn a JSON ``arguments`` object into argv for ``cls``'s OWN fields.

    Iterates ``cls._getargs_()`` in declaration order. A field named in
    ``skip`` contributes nothing at all -- ``call_tool`` passes the set of
    field names that are ALSO declared by a DEEPER ancestor in the current
    dispatch chain, so a name redeclared at multiple levels only ever binds
    at the deepest one (its own schema, per :func:`_input_schema_for_node`,
    already only ever describes that same deepest declaration); omit it
    (the default) for a standalone, single-level call. A field absent from
    ``arguments``, or explicitly ``null``, ALSO contributes nothing (JSON
    ``null`` means "not supplied", never the literal string ``"None"``).
    Branches on the field's EFFECTIVE ``argparse`` action, not a re-derived
    guess, so this never drifts from what ``add_to_parser`` itself would
    register:

    * a bare bool flag -- resolved from the REAL action already built on
      ``parser`` (see :func:`_bool_action_kind`), since a re-derived guess
      can disagree for an env/config-LAYERED field: ``store_true`` -> the
      bare flag when ``True``, nothing when ``False`` (there is no CLI
      spelling for ``False`` here, matching the plain CLI's own limit);
      ``store_false`` -> the bare flag when ``False``, nothing when ``True``;
      ``BooleanOptionalAction`` -> the bare flag when ``True``, ``--no-<x>``
      when ``False`` (raising if the field has no long flag to negate) --
      this is what lets an env-layered bool be turned back to ``False``.
    * a counting flag (``-v``/``-q`` style) -> a single bundled short token
      (``-vvv``) for a short-flag-only field, else the long flag repeated
      ``value`` times; capped by ``_MAX_COUNT_VALUE`` at the schema/
      validation layer (:func:`json_schema_for_field`/`_validate_arguments`),
      not here.
    * ``store_const``/``append_const`` -> the bare flag when ``value`` is truthy.
    * a ``nargs="?"`` OPTION given an actual JSON boolean -> the bare flag when
      ``True`` (the option's own ``const``), nothing when ``False``.
    * a ``dict`` field backed by duho's own generic ``KEY=VALUE`` factory
      (:class:`duho._fieldspec._KVFactory`) -> one such token per item,
      repeating the flag. A dict field with a DIFFERENT, custom whole-string
      ``type=`` override (duho's only one is ``LoggingArgs.loglevels``'s
      ``parse_loglevels``, parsing its own ``NAME:LEVEL[,NAME:LEVEL...]``
      grammar from a single token) -> all items joined into ONE such token
      instead -- emitting the generic ``KEY=VALUE`` form here fed a value
      like ``synapp=10`` straight into that grammar and always failed.
    * a ``list``/``set``/``tuple`` field -> one token per element, repeating
      the flag (a positional repeats bare tokens with no flag).
    * anything else (str/int/float/``Literal[True, False]``/Enum/Path/a custom
      ``action=``/``type=`` with no registered override) -> ``str(value)``.

    Every option value is emitted as a single attached ``--flag=value`` token
    (never ``[flag, value]``), so a value starting with ``-`` can never be
    reinterpreted as a different flag; a positional value that would be
    parsed as an option (or the ``--`` passthrough separator), or that
    collides with one of THIS level's own subcommand names OR one named in
    ``ancestor_forbidden`` (security-relevant: MCP tool arguments are
    LLM-controlled -- ``call_tool`` passes every ANCESTOR level's own
    subcommand names/aliases here, since such a value could otherwise be
    swallowed by an ancestor's own optional/variadic positional and
    reinterpreted as ITS subcommand selector once the literal name tokens
    shift -- see :func:`_reject_unsafe_positional`), is refused outright.
    """
    argv: "list[str]" = []
    forbidden = _sibling_names(parser) | ancestor_forbidden
    for builder in cls._getargs_():
        name = builder.name
        if skip is not None and name in skip:
            continue
        if name not in arguments:
            continue
        value = arguments[name]
        if value is None:
            continue

        is_positional = builder.is_positional
        flag = is_long = None
        if not is_positional:
            flag, is_long = _long_flag_or_first(builder)
        action = builder._kwargs().get("action")

        if builder.type is bool and builder.choices is None:
            kind = _bool_action_kind(_dest_action(parser, name))
            if kind is None and builder.is_bare_bool_flag:
                # No matching action found on the parser (should not happen
                # for a bare bool flag) -- fall back to the old heuristic.
                kind = "boolean_optional" if builder.default is True else "store_true"
            if kind is not None:
                if kind == "boolean_optional":
                    if value:
                        argv.append(flag)
                    elif not is_long:
                        raise ValueError(
                            "field %r has no long flag to negate; false "
                            "cannot be expressed over MCP" % (name,)
                        )
                    else:
                        argv.append("--no-" + flag[2:])
                elif kind == "store_false":
                    if not value:
                        argv.append(flag)
                else:  # store_true
                    if value:
                        argv.append(flag)
                continue

        if action == "count":
            count = value if isinstance(value, int) else int(value)
            if count < 0:
                # A malformed-request problem, not a broken command -- see
                # `_reject_unsafe_positional`'s docstring for the same
                # classification. `_validate_arguments` also enforces the
                # published `minimum: 0` before dispatch ever reaches here;
                # this is the defense-in-depth fallback.
                raise InvalidArgumentsError(
                    "field %r (a counting flag) cannot be negative" % (name,)
                )
            if is_long or not count:
                argv.extend([flag] * count)
            else:
                # Bundle a short counting flag into one token (`-vvv`)
                # instead of `count` separate ones.
                argv.append("-" + flag[1:] * count)
            continue

        if action in ("store_const", "append_const"):
            if value:
                argv.append(flag)
            continue

        if builder.nargs == "?" and not is_positional and isinstance(value, bool):
            if value:
                argv.append(flag)
            continue

        if builder.collection is dict:
            if not isinstance(value, dict):
                raise ValueError("field %r expects a JSON object" % (name,))
            if isinstance(builder.type, _KVFactory):
                tokens = ["%s=%s" % (key, val) for key, val in value.items()]
            else:
                tokens = [",".join("%s:%s" % (key, val) for key, val in value.items())]
                if not tokens[0]:
                    tokens = []
            for token in tokens:
                if is_positional:
                    _reject_unsafe_positional(token, parser, forbidden=forbidden)
                    argv.append(token)
                else:
                    _emit_option(argv, flag, is_long, token, parser)
            continue

        if builder.collection in (list, set, tuple):
            if not isinstance(value, (list, tuple, set)):
                raise ValueError("field %r expects a JSON array" % (name,))
            for item in value:
                token = str(item)
                if is_positional:
                    _reject_unsafe_positional(token, parser, forbidden=forbidden)
                    argv.append(token)
                else:
                    _emit_option(argv, flag, is_long, token, parser)
            continue

        token = str(value)
        if is_positional:
            _reject_unsafe_positional(token, parser, forbidden=forbidden)
            argv.append(token)
        else:
            _emit_option(argv, flag, is_long, token, parser)
    return argv


def _synthesize_argv_from_actions(
    step: "_Node",
    arguments: "dict",
    *,
    skip: "_ty.Optional[frozenset]" = None,
    ancestor_forbidden: "frozenset" = frozenset(),
) -> "list[str]":
    """:func:`_synthesize_argv`'s counterpart for a bare module command --
    one with no declared ``Args`` (:func:`_effective_cls` is ``None``): maps
    ``arguments`` onto ``step.parser``'s own actions (:func:`_own_dests`)
    directly, with no ``ArgumentBuilder`` behind any of them. Only ever
    called for ``step is node`` itself (a module command is always a leaf).

    Deliberately simpler than :func:`_synthesize_argv` -- there is no
    ``ArgumentBuilder``/``NS(...)`` metadata to consult here, only the
    action's own ``nargs``/``type``/class -- but applies the SAME
    request-level safety checks (:func:`_reject_unsafe_positional`/
    :func:`_emit_option`) for every emitted token.
    """
    argv: "list[str]" = []
    parser = step.parser
    forbidden = _sibling_names(parser) | ancestor_forbidden
    own = _own_dests(parser) or set()
    for action in parser._actions:
        dest = action.dest
        if dest not in own:
            continue
        if skip is not None and dest in skip:
            continue
        if dest not in arguments:
            continue
        value = arguments[dest]
        if value is None:
            continue

        is_positional = not action.option_strings
        flag = action.option_strings[0] if action.option_strings else None
        is_long = bool(flag) and flag.startswith("--")

        if action.nargs == 0:
            # A bare 0-arg action over MCP is a JSON boolean; `store_false`
            # is the only 0-arg action whose "on" state is FALSE.
            truthy = (
                not value if isinstance(action, _argparse._StoreFalseAction) else value
            )
            if truthy:
                argv.append(flag)
            continue

        if isinstance(action, _argparse._AppendAction) or action.nargs in ("*", "+"):
            items = value if isinstance(value, (list, tuple)) else [value]
            for item in items:
                token = str(item)
                if is_positional:
                    _reject_unsafe_positional(token, parser, forbidden=forbidden)
                    argv.append(token)
                else:
                    _emit_option(argv, flag, is_long, token, parser)
            continue

        token = str(value)
        if is_positional:
            _reject_unsafe_positional(token, parser, forbidden=forbidden)
            argv.append(token)
        else:
            _emit_option(argv, flag, is_long, token, parser)
    return argv


def _synthesize_step_argv(
    step: "_Node",
    arguments: "dict",
    *,
    skip: "frozenset",
    ancestor_forbidden: "frozenset",
) -> "list[str]":
    """One chain step's own argv contribution, dispatching to
    :func:`_synthesize_argv` (a real declared class -- a class command, or a
    module command with its own ``Args``) or :func:`_synthesize_argv_from_actions`
    (a bare module command) depending on :func:`_effective_cls`.
    """
    eff_cls = _effective_cls(step)
    if eff_cls is None:
        return _synthesize_argv_from_actions(
            step, arguments, skip=skip, ancestor_forbidden=ancestor_forbidden
        )
    own = _own_dests(step.parser)
    if own is not None:
        # A module command's declared field that collided with an inherited
        # global at registration time was silently skipped -- never emit a
        # token for it here either (see `_input_schema_for_node`'s matching
        # skip).
        skip = skip | {b.name for b in eff_cls._getargs_() if b.name not in own}
    return _synthesize_argv(
        eff_cls,
        arguments,
        step.parser,
        skip=skip,
        ancestor_forbidden=ancestor_forbidden,
    )


_SCHEMA_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, (list, tuple)),
    "object": lambda v: isinstance(v, dict),
}


def _matches_schema_type(value: object, expected: object) -> bool:
    types = expected if isinstance(expected, list) else [expected]
    return any(_SCHEMA_TYPE_CHECKS.get(t, lambda v: True)(value) for t in types)


def _validate_arguments(schema: "dict", arguments: "dict") -> None:
    """Reject ``arguments`` against ``schema`` before any argv is
    synthesized or anything is dispatched. Checked, each against every
    supplied argument (not stopping at the first problem found):

    * an unknown property (the schema always declares
      ``additionalProperties: false``);
    * a value whose JSON type does not match its property's declared
      ``type`` -- e.g. the string ``"false"`` for a boolean field, which
      used to be truthy and silently turn the flag ON;
    * a MISSING property named in ``schema["required"]`` (JSON ``null`` for
      a required property counts as missing -- see the module docstring's
      "null means not supplied" convention);
    * a value outside its property's ``enum`` (``Literal``/``Enum`` fields);
    * a numeric value over its property's ``maximum``, or under its
      ``minimum`` (currently only a counting flag publishes either; see
      ``_MAX_COUNT_VALUE``, and ``json_schema_for_field``'s ``minimum: 0``);
    * an array over its property's ``maxItems``, or an object over its
      ``maxProperties`` (``_MAX_ARRAY_ITEMS``/``_MAX_OBJECT_PROPERTIES`` --
      published for every ``list``/``set``/``tuple``/``dict`` field and the
      synthetic ``"--"`` passthrough array, so an oversized LLM-supplied
      collection is refused here rather than synthesized into argv and
      dispatched);
    * a non-string item in an array property whose own ``items`` schema
      declares ``"type": "string"`` (currently only ``"--"``, since its
      items are fed straight into argv).

    A value that IS schema-valid but still cannot be safely turned into argv
    (an unsafe positional, an unsafe option value, a negative count) is a
    DIFFERENT, later check -- raised directly by :func:`_synthesize_argv`/
    :func:`_reject_unsafe_positional`/:func:`_emit_option` as this
    same :class:`InvalidArgumentsError`, since it depends on the built
    parser tree (subcommand names, aliases), not just the JSON schema this
    function checks against.
    """
    properties = schema.get("properties", {})
    required = schema.get("required", ())
    errors = []
    for key in arguments:
        if key not in properties:
            errors.append("unknown argument %r" % (key,))
    for key in required:
        if key not in arguments or arguments[key] is None:
            errors.append("missing required argument %r" % (key,))
    for key, value in arguments.items():
        prop = properties.get(key)
        if prop is None or value is None:
            continue
        expected = prop.get("type")
        if expected and not _matches_schema_type(value, expected):
            errors.append(
                "argument %r: expected %s, got %s"
                % (key, expected, type(value).__name__)
            )
            continue
        enum = prop.get("enum")
        if enum is not None and value not in enum:
            errors.append("argument %r: %r is not one of %r" % (key, value, enum))
            continue
        maximum = prop.get("maximum")
        if (
            maximum is not None
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value > maximum
        ):
            errors.append("argument %r: %r exceeds maximum %r" % (key, value, maximum))
        minimum = prop.get("minimum")
        if (
            minimum is not None
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value < minimum
        ):
            errors.append("argument %r: %r is below minimum %r" % (key, value, minimum))
        max_items = prop.get("maxItems")
        if (
            max_items is not None
            and isinstance(value, (list, tuple))
            and len(value) > max_items
        ):
            errors.append(
                "argument %r: has %d items, exceeds maxItems %r"
                % (key, len(value), max_items)
            )
        max_properties = prop.get("maxProperties")
        if (
            max_properties is not None
            and isinstance(value, dict)
            and len(value) > max_properties
        ):
            errors.append(
                "argument %r: has %d properties, exceeds maxProperties %r"
                % (key, len(value), max_properties)
            )
        items_schema = prop.get("items")
        if (
            items_schema
            and items_schema.get("type") == "string"
            and isinstance(value, (list, tuple))
            and any(not isinstance(item, str) for item in value)
        ):
            errors.append("argument %r: every item must be a string" % (key,))
    if errors:
        raise InvalidArgumentsError("; ".join(errors))


def _text_result(text: str, *, is_error: bool = False) -> "dict":
    result: "dict" = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return result


def _systemexit_result(exc: SystemExit, stdout_text: str, stderr_text: str) -> "dict":
    """Map a command's own run-time ``SystemExit`` to a tool result:
    ``None``/``0`` -> success; an int -> ``isError`` with an
    ``"exit code: N"`` trailer; anything else (e.g. ``sys.exit("message")``)
    -> ``isError`` with that message -- the same convention Python's own
    interpreter uses for an uncaught ``SystemExit``."""
    code = exc.code
    if code is None or code == 0:
        return _text_result(stdout_text)
    trailing = "exit code: %d" % code if isinstance(code, int) else str(code)
    parts = [part for part in (stdout_text, stderr_text.strip(), trailing) if part]
    return _text_result("\n".join(parts), is_error=True)


@_contextlib.contextmanager
def _muted_color(parsers: "_ty.Iterable[_argparse.ArgumentParser]"):
    """Temporarily force ``parser.color = False`` on every parser in
    ``parsers`` that has the attribute (argparse's native color, Python
    3.14+): a usage/error string captured as MCP tool output must be plain
    text regardless of the server process's own TTY/``FORCE_COLOR`` state
    , since it is machine-read by the client, not displayed in a
    terminal. Restored afterwards, on any exit."""
    saved = [(p, p.color) for p in parsers if hasattr(p, "color")]
    for p, _old in saved:
        p.color = False
    try:
        yield
    finally:
        for p, old in saved:
            p.color = old


@_contextlib.contextmanager
def _rebound_stderr_logging(active_stream: object, idle_stream: object):
    """Temporarily repoint duho's own stderr log handler (see
    ``duho.logging.init_stderr_logging``) at ``active_stream`` for the
    duration of one ``call_tool`` dispatch, restoring it to ``idle_stream``
    on exit.

    ``init_stderr_logging`` is deliberately idempotent -- a repeat call never
    adds a second handler -- which means it also never RE-POINTS the one it
    already installed. The first ever MCP call creates it bound to that
    call's own captured stderr (correct, since it's built while THAT
    capture is active); every call after that finds the handler already
    there and leaves it bound to the FIRST call's now-dead capture object,
    so a command's own logging output silently vanishes, and so does any
    server-side error logged BETWEEN calls (nothing ever reads that stream
    again). Rebinding here -- to this call's capture while it runs, and back
    to the server's real idle stream (``idle_stream``, the process's actual
    stderr as it stood before any call ever ran) once it returns -- fixes
    both. The handler list is read fresh both before AND after ``yield`` so
    a handler created DURING this very call (the first-ever-call case) is
    also reset to ``idle_stream`` on exit, not left on ``active_stream``.
    Only ever touches a handler carrying duho's own tag, never one a host
    application added itself.
    """
    root_logger = _logging.getLogger()

    def _tagged():
        return [
            h
            for h in root_logger.handlers
            if getattr(h, _STDERR_HANDLER_TAG, False) and hasattr(h, "setStream")
        ]

    for handler in _tagged():
        handler.setStream(active_stream)
    try:
        yield
    finally:
        for handler in _tagged():
            handler.setStream(idle_stream)


def call_tool(
    root_cls: "_ty.Union[type, _ServerCore]", name: object, arguments: object
) -> "dict":
    """Dispatch one MCP ``tools/call`` against ``root_cls``'s tree.

    ``root_cls`` is a ``Cmd``/``Cli`` class (the static ``_subcommands_``
    tree path -- unchanged) or a :class:`_ServerCore` (an ``app()``-built
    tree, from :func:`_core_for_app`) -- see :func:`describe_tools`.

    Resolves ``name`` to a node in the tree, raising
    :class:`UnknownToolError` for a name that is not in the tree, that names
    a namespace node (see :func:`_is_namespace_node`), or that names a node
    excluded via a per-command ``_mcp_ = False`` (:attr:`_Node.excluded`) --
    the same error either way, so an excluded command's existence is never
    disclosed to a caller probing for it. Raises :class:`InvalidArgumentsError` when
    ``arguments`` is not a JSON object (``None`` is treated as ``{}``), fails
    the tool's own merged ``inputSchema`` (:func:`_validate_arguments`), or
    (discovered while synthesizing argv, since it depends on the built
    parser tree rather than the JSON schema alone) supplies a value that
    cannot be safely encoded at all -- an unsafe positional, an unsafe
    option value, or a negative counting-flag value (see
    :func:`_synthesize_argv`/:func:`_reject_unsafe_positional`/
    :func:`_emit_option`). All of these are request-level problems,
    mapped by :func:`serve` to a JSON-RPC error response rather than a tool
    result.

    Otherwise: synthesizes one argv per level of the tool's ancestry chain
    (root first) via :func:`_synthesize_argv`, with the next level's own
    subcommand name token in between, then -- when the JSON arguments carry
    a ``"--"`` array -- one literal ``--`` token followed by its items at
    the very end (see :func:`_input_schema_for_node`'s synthetic property),
    and parses the WHOLE THING through the tree's single shared ROOT parser
    -- exactly as ``duho.main``/``duho.parse`` would for the equivalent CLI
    invocation, so env/config layering, root globals, ``_passthrough_``, and
    (via :func:`duho.args._setup_instance_logging`) ``LoggingArgs``
    verbosity setup all reach the dispatched command. Captures stdout AND
    stderr during dispatch, and replaces ``sys.stdin`` with an empty stream
    for its duration (a command honoring '-' = stdin must not be able to
    read the MCP client's next request off the real stdin).

    **Dispatch-identity guard** (security-relevant: MCP tool arguments are
    LLM-controlled): an ancestor's own optional/variadic positional field can
    -- when a client omits it -- still absorb the LITERAL subcommand-name
    token this function inserts between levels, shifting a LATER token into
    that ancestor's own subparsers action and dispatching a DIFFERENT
    sibling than the one named by ``name`` (argparse itself has always
    allowed this; it is normally harmless on a real, human-typed CLI, but
    not when the argv comes from an LLM). The SAME class can also be reached
    from more than one place in the tree (shared between two parents, or
    both nested and top-level), so a class-identity check alone cannot tell
    a hijack from a legitimate dispatch. After a successful parse, the
    result is used ONLY when BOTH the intended command was actually selected
    (``type(instance) is node.cls`` for a class command, or the popped
    ``_duho_module_command_`` marker ``is node.module_command`` for a module
    command) AND the ``_duho_mcp_path_`` tag :func:`_walk_tree` attaches to
    every subparser (via ``set_defaults``, overwritten by the DEEPEST
    subparser actually reached as parsing descends) equals the tool's own
    chain of names exactly -- anything else (including a namespace class
    picked up mid-chain, or the right command reached through the WRONG
    chain) is treated as a dispatch failure, mapped to ``isError: true``, and
    the command is NEVER run. :func:`_synthesize_argv`/
    :func:`_synthesize_argv_from_actions` additionally refuse
    outright (before parsing, as an :class:`InvalidArgumentsError`) a value
    explicitly supplied FOR a field at any level that collides with a
    subcommand name/alias registered at THAT level or any ANCESTOR level.

    **Return convention**: ``run_command`` returns ``0`` for a
    ``None``/``0`` command return, an int for a non-zero return, or the raw
    object/list when the command returned one. Mapped here: ``0`` -> success,
    one text block of captured stdout; a non-zero int -> ``isError: true``,
    captured stdout + captured stderr + a trailing ``"exit code: N"`` line;
    anything else -> success, one text block holding its JSON dump. A
    ``SystemExit`` from argument PARSING (bad/missing argument) ->
    ``isError: true`` with the captured stderr text. A ``SystemExit`` raised
    by the command's OWN run time code is mapped by :func:`_systemexit_result`
    instead of escaping and killing the server. Any OTHER raised exception
    during dispatch -> ``isError: true`` with the exception's ``type:
    message`` text. ``KeyboardInterrupt`` is not caught and still propagates.
    """
    core = root_cls if isinstance(root_cls, _ServerCore) else _core_for_class(root_cls)
    root_parser, nodes = core.root_parser, core.nodes
    node = nodes.get(name) if isinstance(name, str) else None
    if (
        node is None
        or _is_namespace_node(node.parser)
        or _is_mcp_command_node(node)
        or node.excluded
    ):
        raise UnknownToolError("unknown tool: %r" % (name,))

    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise InvalidArgumentsError(
            "arguments must be a JSON object, got %s" % (type(arguments).__name__,)
        )

    schema = _input_schema_for_node(node)
    _validate_arguments(schema, arguments)

    chain = node.ancestors + (node,)
    expected_path = tuple(step.own_name for step in chain)
    # A field name declared at several levels of the chain binds ONLY at the
    # deepest one (matches the merged schema, `_input_schema_for_node`) -- an
    # ancestor's own same-named field must never ALSO pick up the value
    # (security-relevant: a shared JSON `arguments` dict is otherwise a way
    # for a leaf's own field to flip an unrelated ancestor flag it never
    # named, e.g. a hidden `--force`).
    field_owner: "dict[str, int]" = {}
    for i, step in enumerate(chain):
        for fname in _step_field_names(step):
            field_owner[fname] = i
    try:
        argv: "list[str]" = []
        ancestor_forbidden: "frozenset" = frozenset()
        for i, step in enumerate(chain):
            shadowed = frozenset(
                fname for fname, owner in field_owner.items() if owner != i
            )
            argv.extend(
                _synthesize_step_argv(
                    step,
                    arguments,
                    skip=shadowed,
                    ancestor_forbidden=ancestor_forbidden,
                )
            )
            ancestor_forbidden = ancestor_forbidden | _sibling_names(step.parser)
            if i + 1 < len(chain):
                argv.append(chain[i + 1].own_name)
        passthrough = arguments.get("--") or None
        if passthrough:
            argv.append("--")
            argv.extend(passthrough)
    except InvalidArgumentsError:
        raise
    except ValueError as exc:
        return _text_result(str(exc), is_error=True)

    out = _io.StringIO()
    err = _io.StringIO()
    all_parsers = [root_parser] + [n.parser for n in nodes.values()]
    real_stderr = _sys.stderr
    try:
        with _contextlib.redirect_stdout(out), _contextlib.redirect_stderr(err):
            old_stdin = _sys.stdin
            _sys.stdin = _io.StringIO("")
            try:
                with (
                    _muted_color(all_parsers),
                    _rebound_stderr_logging(err, real_stderr),
                ):
                    try:
                        instance = root_parser.parse_args(argv)
                    except SystemExit as exc:
                        message = err.getvalue().strip() or (
                            "argument error (exit code %r)" % (exc.code,)
                        )
                        return _text_result(message, is_error=True)
                    actual_path = getattr(instance, "_duho_mcp_path_", None)
                    # Popped (not merely peeked), mirroring `runtime._run_app`'s
                    # own contract: framework bookkeeping never lingers in
                    # `vars(instance)` where a module command's own `main`
                    # would otherwise see it.
                    dispatched_module_command = vars(instance).pop(
                        "_duho_module_command_", None
                    )
                    if node.module_command is not None:
                        identity_ok = dispatched_module_command is node.module_command
                        target: object = node.module_command
                    else:
                        identity_ok = type(instance) is node.cls
                        target = node.cls
                    if not identity_ok or actual_path != expected_path:
                        actual_desc = (
                            ".".join(actual_path)
                            if isinstance(actual_path, tuple)
                            else type(instance).__name__
                        )
                        return _text_result(
                            "tool %r did not resolve to the requested command "
                            "(dispatched %r instead); refusing to run it"
                            % (name, actual_desc),
                            is_error=True,
                        )
                    try:
                        result = core.dispatch(target, instance)
                    except SystemExit as exc:
                        return _systemexit_result(exc, out.getvalue(), err.getvalue())
            finally:
                _sys.stdin = old_stdin
    except (
        Exception
    ) as exc:  # noqa: BLE001 - one broken command must not crash the server
        # The client only ever sees "Type: message"; the stack that says WHERE
        # the command broke exists nowhere else, so log it server-side too
        # (traceback under DUHO_TRACEBACK=1).
        _log_exception(
            _LOGGER,
            "tool %r raised: %s: %s",
            name,
            type(exc).__name__,
            exc,
        )
        return _text_result("%s: %s" % (type(exc).__name__, exc), is_error=True)

    stdout_text = out.getvalue()

    if isinstance(result, int):
        if result == 0:
            return _text_result(stdout_text)
        trailing = "exit code: %d" % result
        parts = [
            part for part in (stdout_text, err.getvalue().strip(), trailing) if part
        ]
        return _text_result("\n".join(parts), is_error=True)

    import json

    return _text_result(json.dumps(result, indent=2, ensure_ascii=False, default=str))


# --------------------------------------------------------------------------
# Step 4: stdio JSON-RPC server
# --------------------------------------------------------------------------


def _resolve_app(spec: str) -> "type[_Cmd]":
    """Resolve the ``<app>`` CLI argument (a dotted qualname) to a root ``Cmd``/``Cli`` class.

    Uses the stdlib ``pkgutil.resolve_name`` (3.9+): it accepts BOTH the
    ``module.sub:ClassName`` colon syntax (the same convention this project's
    own entry-point tests/``discover_entry_points`` use) and the legacy
    dotted ``module.sub.ClassName`` form (progressively importing shorter
    prefixes as a module, the remainder as attribute access). This -- not
    ``discovery.CmdBuilder`` -- resolves the app, because ``CmdBuilder``
    always yields a ``Command`` (wrapping any module source in a
    ``ModuleCommand``), never the raw class :func:`describe_tools`/
    :func:`call_tool` need. Only a class's own STATIC ``_subcommands_`` tree
    is exposed over MCP in v1 -- ``<app>`` must be a ``Cmd``/``Cli`` subclass;
    a module command, or a command only reachable via ``duho.app``'s dynamic
    resolution, is out of scope.
    """
    obj = _pkgutil.resolve_name(spec)
    if not (isinstance(obj, type) and issubclass(obj, _Cmd)):
        raise TypeError(
            "%r does not resolve to a duho Cmd/Cli class (got %r)" % (spec, obj)
        )
    return obj


def _write_message(stream: object, message: "dict | list") -> None:
    import json

    # `message` is a `list` only for a JSON-RPC *batch* reply (one combined
    # array of response objects, see `serve`); a single response is always
    # a `dict`.
    #
    # `ensure_ascii=True` (never False): the OUTPUT stream's own encoding is
    # not always known to be UTF-8-safe (an injected stream, or a
    # not-yet-reconfigured real stdout), so every non-ASCII character is
    # escaped to a plain-ASCII `\uXXXX` sequence -- still valid JSON, and
    # correct for any text stream whatsoever.
    #
    # `json.dumps` itself can fail on a pathological response -- a `result`
    # holding an unserializable object, or a deeply nested structure echoed
    # back from the request (e.g. its own `id`) that overflows the C
    # recursion limit `RecursionError` guards. Either way this must still
    # produce SOME reply line rather than raise out of `serve`'s loop (which
    # would end the server for every other in-flight/future request), so a
    # failure here falls back to a minimal, always-serializable error
    # response instead of the original message.
    try:
        text = json.dumps(message, ensure_ascii=True)
    except Exception:
        text = json.dumps(
            _error_response(
                None, -32603, "internal error: failed to serialise response"
            ),
            ensure_ascii=True,
        )
    stream.write(text + "\n")
    flush = getattr(stream, "flush", None)
    if callable(flush):
        flush()


def _error_response(req_id: object, code: int, message: str) -> "dict":
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


#: Cap on JSON bracket nesting a single request LINE may contain, checked by
#: :func:`_line_nesting_exceeds` before the line is ever handed to
#: `json.loads`. `json`'s C decoder (and `json.dumps` re-encoding a value
#: parsed that deep) recurses once per nesting level, so an attacker-supplied
#: line of ``"[" * N + "]" * N`` raises an uncaught `RecursionError` well
#: below any depth a legitimate MCP request needs -- N in the low thousands
#: on CPython's default recursion limit, fewer on a build with a smaller
#: C stack. 64 is far beyond any real tool-call payload's own nesting while
#: leaving a wide margin under that limit.
_MAX_JSON_NESTING = 64


def _line_nesting_exceeds(line: str, limit: int) -> bool:
    """Return whether `line`'s ``{``/``[`` nesting depth, OUTSIDE any JSON
    string literal, exceeds `limit` -- a cheap, non-recursive scan run
    BEFORE `json.loads` ever sees the line, so a pathologically deep
    array/object is rejected before any recursive parsing of it begins
    (rather than caught only after `json.loads` itself has already
    recursed to the point of raising `RecursionError`, see `serve`).

    A close bracket for an opening this scan never saw (an otherwise
    malformed line) is ignored here -- `json.loads` still rejects the line
    on its own merits; this scan's only job is bounding nesting DEPTH.
    """
    depth = 0
    in_string = False
    escape = False
    for ch in line:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{" or ch == "[":
            depth += 1
            if depth > limit:
                return True
        elif ch == "}" or ch == "]":
            if depth > 0:
                depth -= 1
    return False


def _server_info(root_cls: "_ty.Union[type, _ServerCore]") -> "dict":
    """``serverInfo`` for the ``initialize`` response.

    ``name`` is the same resolution ``describe_tools``/``call_tool`` use for
    the root tool-name segment (``core.root_parser.prog``, the application's
    name -- see :func:`duho.args._app_name`). ``version`` is the app's own
    ``_version_``
    (:func:`duho.args._resolve_version` -- a plain ``str``, the ``AUTO``
    sentinel resolved via ``importlib.metadata``, or a class-level
    ``__version__`` fallback) when it resolves to a string, else the empty
    string -- duho's own version is NEVER reported as the served
    app's version. The MCP ``Implementation`` type requires ``version`` to be
    a string, so the field is still always present; a served app with no
    resolvable version of its own simply reports it empty rather than
    fabricating one (and rather than silently reporting duho's, which used to
    read as "this app's version is 0.6.2" for an app that never said so).
    """
    core = root_cls if isinstance(root_cls, _ServerCore) else _core_for_class(root_cls)
    name = core.root_parser.prog
    version = _resolve_version(core.root_cls)
    return {
        # `prog` is always a real, non-empty string in every reachable
        # path here; the `_SERVER_NAME` fallback exists only so this stays
        # defensively correct rather than reporting an empty name.
        "name": name if name else _SERVER_NAME,
        "version": version if isinstance(version, str) else "",
    }


def _handle_request(root_cls: "type[_Cmd]", request: object) -> "dict | None":
    """Dispatch one decoded JSON-RPC request; return the response dict, or ``None``.

    ``None`` means "no response" -- either the request was a **notification**
    (no ``id`` key at all; JSON-RPC forbids replying to one), the
    ``notifications/initialized`` notification specifically, or a malformed
    envelope that also happened to carry no ``id``.

    Handles exactly ONE request object -- a batch (a JSON array of request
    objects) is recognized and fanned out by :func:`serve` itself, one call
    to this function per element, before this function ever sees it.

    Every value pulled out of ``request`` is type-checked before use, so a
    well-formed JSON document that is not a well-formed JSON-RPC REQUEST
    (a bare scalar, a non-object ``params``, a non-string ``name``, a
    non-object ``arguments``) gets a proper JSON-RPC error response instead
    of an uncaught ``AttributeError``/``TypeError`` that would otherwise
    propagate out of :func:`serve` and end the process
    : ``-32600`` for a malformed request/params shape, ``-32601`` for
    an unrecognised method, ``-32602`` for a call naming an unknown tool or
    supplying invalid arguments (:class:`UnknownToolError`/
    :class:`InvalidArgumentsError`), ``-32603`` as a last resort for any
    other exception raised while actually serving ``tools/list``/
    ``tools/call`` (which must never happen, but must never take the whole
    server down either if it somehow does).
    """
    if not isinstance(request, dict):
        return _error_response(None, -32600, "invalid request: expected a JSON object")

    method = request.get("method")
    has_id = "id" in request
    req_id = request.get("id")

    if not isinstance(method, str):
        return (
            _error_response(
                req_id, -32600, "invalid request: 'method' must be a string"
            )
            if has_id
            else None
        )

    params = request.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return (
            _error_response(req_id, -32602, "invalid params: expected an object")
            if has_id
            else None
        )

    if method == "initialize":
        requested = params.get("protocolVersion")
        negotiated = (
            requested if requested in _SUPPORTED_VERSIONS else _SUPPORTED_VERSIONS[0]
        )
        result = {
            "protocolVersion": negotiated,
            "capabilities": {"tools": {}},
            "serverInfo": _server_info(root_cls),
        }
    elif method in ("notifications/initialized", "initialized"):
        return None
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        try:
            result = {"tools": describe_tools(root_cls)}
        except Exception as exc:  # noqa: BLE001 - see docstring
            _log_exception(
                _LOGGER, "tools/list raised: %s: %s", type(exc).__name__, exc
            )
            return (
                _error_response(
                    req_id, -32603, "internal error: %s: %s" % (type(exc).__name__, exc)
                )
                if has_id
                else None
            )
    elif method == "tools/call":
        tool_name = params.get("name")
        tool_arguments = params.get("arguments")
        try:
            result = call_tool(root_cls, tool_name, tool_arguments)
        except (UnknownToolError, InvalidArgumentsError) as exc:
            return _error_response(req_id, exc.code, str(exc)) if has_id else None
        except Exception as exc:  # noqa: BLE001 - see docstring
            _log_exception(
                _LOGGER,
                "tools/call %r raised: %s: %s",
                tool_name,
                type(exc).__name__,
                exc,
            )
            return (
                _error_response(
                    req_id, -32603, "internal error: %s: %s" % (type(exc).__name__, exc)
                )
                if has_id
                else None
            )
    elif method in ("shutdown", "exit"):
        result = None
    elif not has_id:
        return None  # an unrecognised notification: nothing to reply to
    else:
        return _error_response(req_id, -32601, "method not found: %r" % (method,))

    if not has_id:
        return None  # a notification for a method we do handle: still no reply
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _real_stdio_streams() -> "tuple":
    """Take ownership of the real stdio fds for the JSON-RPC protocol channel,
    and isolate fd 0/1 from anything a dispatched command does.

    Duplicates the CURRENT fd 0/1 for the protocol itself -- the OUTPUT side
    as a fresh UTF-8/LF-normalised text stream, the INPUT side as a BINARY
    stream (see :func:`serve`, which decodes it one line at a time so a
    single malformed line can be rejected without losing the rest of the
    session) -- then points the process's real fd 1 at fd 2 (stderr) and
    fd 0 at the null device for the rest of the server's life, and rebinds
    ``sys.stdout``/``sys.stdin`` to match. This is what makes the module
    docstring's "one broken command never crashes the whole server loop"
    promise hold even against code the command doesn't control:

    * a subprocess the command spawns WITHOUT capturing its own output
      inherits fd 1 -- now stderr, not the protocol pipe -- instead of
      injecting non-JSON lines into the stream a client is trying to parse;
    * a command honoring the "'-' = stdin" convention, or any low-level
      ``os.read(0, ...)``, gets an immediate EOF on the (now devnull) fd 0
      instead of consuming the client's NEXT request.

    Called directly by :func:`main` -- **before** it resolves ``<app>`` --
    so the takeover is already in effect for the whole rest of the process's
    life by the time anything imports the caller's code (see :func:`main`'s
    docstring for why the ordering matters). Also reachable as
    :func:`serve`'s own fallback when a caller invokes it directly with
    neither ``stdin`` nor ``stdout`` injected; a test driving ``serve`` over
    ``io.StringIO`` is unaffected either way.
    """
    _sys.stdout.flush()
    proto_in_fd = _os.dup(0)
    proto_out_fd = _os.dup(1)
    devnull_fd = _os.open(_os.devnull, _os.O_RDONLY)
    _os.dup2(devnull_fd, 0)
    _os.close(devnull_fd)
    _os.dup2(2, 1)

    stream_in = _os.fdopen(proto_in_fd, "rb")
    stream_out = _os.fdopen(proto_out_fd, "w", encoding="utf-8", newline="\n")
    _sys.stdin = _os.fdopen(_os.dup(0), "r", encoding="utf-8")
    _sys.stdout = _sys.stderr
    return stream_in, stream_out


def serve(
    root_cls: "_ty.Union[type, _ServerCore]",
    *,
    stdin: "_ty.Optional[_ty.TextIO]" = None,
    stdout: "_ty.Optional[_ty.TextIO]" = None,
) -> int:
    """Run the stdio JSON-RPC loop for ``root_cls`` until stdin closes (EOF).

    ``root_cls`` is a ``Cmd``/``Cli`` class (the static ``_subcommands_``
    tree path) or a :class:`_ServerCore` (an ``app()``-built tree, from
    :func:`_core_for_app`, or the one :func:`serve_running_app` builds from
    the currently-dispatching app's own context) -- forwarded opaquely to
    :func:`_handle_request`, which in turn forwards it to
    :func:`describe_tools`/:func:`call_tool` (both already accept either
    shape -- see :func:`describe_tools`).

    Reads newline-delimited JSON-RPC 2.0 request lines from ``stdin`` (real
    stdio, isolated per :func:`_real_stdio_streams`, when neither ``stdin``
    nor ``stdout`` is given -- BINARY there, so one line's invalid UTF-8
    bytes cannot kill the whole server, see below), dispatches each via
    :func:`_handle_request`, and writes any response line to ``stdout``,
    flushed every time. A line that is not valid UTF-8 (only possible on the
    real-stdio path; an injected text ``stdin`` is decoded already) gets a
    ``-32700`` parse-error response and the loop continues -- a client
    reconnecting or retrying is not required. A line that decodes but fails
    to parse as JSON gets the same ``-32700`` (``id: null`` in both cases --
    the malformed line's own id, if any, is unrecoverable), as does a line
    whose ``{``/``[`` nesting exceeds :data:`_MAX_JSON_NESTING` -- rejected
    BEFORE ``json.loads`` ever parses it, since parsing (or later
    re-encoding) a pathologically deep structure would otherwise raise an
    uncaught ``RecursionError`` and end the loop. A JSON ARRAY
    (a JSON-RPC 2.0 *batch*) is dispatched element by element; every non-
    notification element's response is collected into ONE reply array
    (never sent at all if the batch was all notifications, per spec), and an
    EMPTY batch array gets its own ``-32600``. An unexpected exception from
    :func:`_handle_request` itself (which should never happen, given its own
    internal error handling, but must never end the server if it somehow
    does) becomes a ``-32603`` response instead of propagating. Blank lines
    are skipped. Returns ``0`` when ``stdin`` reaches EOF (there is no
    separate MCP "shutdown" method to wait for). ``stdin``/``stdout`` are
    injectable so tests can drive the loop over in-memory TEXT streams
    instead of real pipes.
    """
    import json

    if stdin is None and stdout is None:
        stream_in, stream_out = _real_stdio_streams()
    else:
        stream_in = stdin if stdin is not None else _sys.stdin
        stream_out = stdout if stdout is not None else _sys.stdout

    def _safe_handle(request):
        """`_handle_request`, with any unexpected exception mapped to a
        `-32603` response instead of propagating and ending the loop."""
        try:
            return _handle_request(root_cls, request)
        except Exception as exc:  # noqa: BLE001 - the loop itself must never die
            _log_exception(
                _LOGGER,
                "serve() request handling raised: %s: %s",
                type(exc).__name__,
                exc,
            )
            fallback_id = request.get("id") if isinstance(request, dict) else None
            return _error_response(
                fallback_id,
                -32603,
                "internal error: %s: %s" % (type(exc).__name__, exc),
            )

    for raw_line in stream_in:
        if isinstance(raw_line, bytes):
            try:
                line = raw_line.decode("utf-8")
            except UnicodeDecodeError as exc:
                _write_message(
                    stream_out,
                    _error_response(
                        None, -32700, "parse error: invalid utf-8 (%s)" % exc
                    ),
                )
                continue
        else:
            line = raw_line
        line = line.strip()
        if not line:
            continue
        if _line_nesting_exceeds(line, _MAX_JSON_NESTING):
            _write_message(
                stream_out,
                _error_response(None, -32700, "parse error: JSON nesting too deep"),
            )
            continue
        try:
            request = json.loads(line)
        except (ValueError, RecursionError):
            # `ValueError` is `json.loads`'s own documented failure
            # (`JSONDecodeError` is a `ValueError` subclass); `RecursionError`
            # is not one, and without the nesting check above a deeply
            # nested line would otherwise raise it uncaught here, ending the
            # whole `serve` loop -- kept as a second layer of defense in case
            # some other line shape ever reaches the C decoder's own
            # recursion limit despite that check.
            _write_message(stream_out, _error_response(None, -32700, "parse error"))
            continue

        if isinstance(request, list):
            if not request:
                _write_message(
                    stream_out,
                    _error_response(None, -32600, "invalid request: empty batch"),
                )
                continue
            responses = [
                response
                for response in (_safe_handle(item) for item in request)
                if response is not None
            ]
            if responses:
                _write_message(stream_out, responses)
            continue

        response = _safe_handle(request)
        if response is not None:
            _write_message(stream_out, response)
    return 0


def main(argv: "_ty.Sequence[str] | None" = None) -> int:
    """``python -m duho.mcp <app>`` entry point: resolve ``<app>`` and run :func:`serve`.

    ``<app>`` is a dotted qualname to a ``Cmd``/``Cli`` subclass (see
    :func:`_resolve_app`). No arguments prints a usage line to stderr and
    returns ``2``; ``-h``/``--help`` prints the same usage line and returns
    ``0`` (previously treated as an ``<app>`` spec and reported as
    unresolvable) -- neither of these touches stdio at all. Otherwise, takes
    over the real stdio fds for the protocol channel via
    :func:`_real_stdio_streams` **before** resolving ``<app>`` (importing
    it), and never restores them in between: resolution can write to the
    ORIGINAL fd 1 directly -- a module-level ``print``, ``os.write(1, ...)``,
    a C extension, a background thread started at import time that keeps
    writing after import returns -- and every one of those writes now lands
    on the real fd 2 (stderr) for the rest of the process's life, because
    ``_real_stdio_streams`` already repointed fd 1 there before resolution
    ever ran. An earlier version resolved ``<app>`` through a SEPARATE,
    temporary fd-1-to-fd-2 redirect that RESTORED fd 1 to the original pipe
    immediately after import finished, then only isolated stdio once
    :func:`serve` started -- a window between those two steps during which a
    thread STILL RUNNING from import (daemon or otherwise) could write
    straight into the client-facing pipe ahead of the first protocol
    response. Prints a one-line error to stderr and returns a non-zero exit
    code if ``<app>`` does not resolve (stdio has already been taken over by
    then, but the process exits right after, so nothing depends on restoring
    it); otherwise runs the stdio loop against the already-captured protocol
    streams and returns its exit code.
    """
    args = list(argv) if argv is not None else _sys.argv[1:]
    if not args:
        _compat.write_human("usage: python -m duho.mcp <app>\n", _sys.stderr)
        return 2
    if args[0] in ("-h", "--help"):
        _compat.write_human("usage: python -m duho.mcp <app>\n", _sys.stderr)
        return 0
    stream_in, stream_out = _real_stdio_streams()
    try:
        root_cls = _resolve_app(args[0])
    except Exception as exc:  # noqa: BLE001 - report, don't traceback, a bad app spec
        # `args[0]`/`exc` can both carry arbitrary (env- or user-supplied)
        # text -- `write_human`, not a raw `print(..., file=sys.stderr)`,
        # so a non-ASCII app spec or exception message can't raise even on
        # a stderr this module cannot assume is UTF-8.
        _compat.write_human(
            "duho.mcp: could not resolve app %r: %s\n" % (args[0], exc), _sys.stderr
        )
        return 1
    return serve(root_cls, stdin=stream_in, stdout=stream_out)


if __name__ == "__main__":
    _sys.exit(main())
