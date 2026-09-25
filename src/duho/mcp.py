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
:class:`InvalidArgumentsError` when ``arguments`` is not a JSON object or
fails the tool's own ``inputSchema`` (an unknown property, or a value whose
JSON type does not match). Both are :class:`ValueError` subclasses carrying a
JSON-RPC ``.code`` (``-32602``, "invalid params"); :func:`serve` maps them to
a JSON-RPC *error response*, never a tool result -- the request itself, not
the target command, was invalid. A problem in the dispatched command itself
(a raised exception, a non-zero exit, ``sys.exit``, or an argparse usage
error from a value that WAS schema-valid but the command still rejects) is
still a normal tool result with ``isError: true``.

**Documented v1 limitations**: a custom ``action=``/``type=`` field with no
registered override is passed through as a plain string, verbatim;
``NS(conflicts=...)`` exclusive groups are surfaced only as a note appended to
the tool's description text (no ``oneOf``/``not`` JSON Schema encoding yet); a
field that defaults to ``True`` and declares only short flags (no long flag)
cannot be turned back to ``False`` over MCP (there is no ``--no-<x>`` form to
emit) and raises rather than silently doing the wrong thing; a value equal to
the literal string ``"--"`` is refused (argparse's own ``--`` end-of-options
marker, and duho's own ``_passthrough_`` split, make it unsafe to smuggle
through); streaming/long-running commands are out of scope -- this is
strictly one request -> one result.

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

from . import __version__ as _DUHO_VERSION
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
from .args import _setup_instance_logging as _setup_instance_logging
from .logging import log_exception as _log_exception
from .runtime import run_command as _run_command

__all__ = [
    "json_schema_for_field",
    "input_schema_for_command",
    "describe_tools",
    "call_tool",
    "serve",
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

#: ``serverInfo.name``/``version`` reported in the ``initialize`` result.
#: ``version`` is duho's own version (the implementation actually running),
#: not a placeholder -- a host cannot otherwise tell one duho release apart
#: from another.
_SERVER_NAME = "duho.mcp"
_SERVER_VERSION = _DUHO_VERSION

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
    * ``list[T]`` -> ``array`` with ``items`` = ``T``'s own schema.
    * ``set[T]`` -> ``array`` + ``uniqueItems: true``.
    * ``tuple[T, ...]`` / bare ``tuple`` -> ``array`` (only the variadic
      homogeneous shape reaches here -- a fixed-length ``tuple[A, B]``
      annotation already raised at ``cls._getargs_()``-build time, before any
      of this module's functions run, so it never needs defensive handling
      here).
    * ``dict[str, V]`` / bare ``dict`` -> ``object`` with
      ``additionalProperties`` = ``V``'s own schema.
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
      hint (not required by the plan's type table; a low-risk, easy addition
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
        return {"type": "array", "items": _schema_for_type(elem)}

    if origin is set or tp is set:
        elem = args[0] if args else str
        return {"type": "array", "items": _schema_for_type(elem), "uniqueItems": True}

    if origin is tuple or tp is tuple:
        elem = args[0] if args else str
        return {"type": "array", "items": _schema_for_type(elem)}

    if origin is dict or tp is dict:
        val = args[1] if len(args) > 1 else str
        return {"type": "object", "additionalProperties": _schema_for_type(val)}

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
    """One node of a root class's cached command tree.

    ``ancestors`` is the tuple of ``_Node`` from the root down to (but not
    including) this node's immediate parent -- empty for the root itself.
    """

    __slots__ = ("dotted_name", "own_name", "parser", "cls", "ancestors")

    def __init__(self, dotted_name, own_name, parser, cls, ancestors):
        self.dotted_name = dotted_name
        self.own_name = own_name
        self.parser = parser
        self.cls = cls
        self.ancestors = ancestors


#: root class -> (root_parser, {dotted_name: _Node}), built once per root
#: class (a naive implementation would rebuild the whole tree on every
#: whole tree). A ``WeakKeyDictionary`` so a throwaway root class (as tests
#: define per test) does not leak for the life of the process.
_TREE_CACHE: "_weakref.WeakKeyDictionary" = _weakref.WeakKeyDictionary()


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

    nodes: "dict[str, _Node]" = {}
    seen: "set" = set()

    def _walk(parser, cls, dotted_parts, own_name, ancestors):
        node = _Node(".".join(dotted_parts), own_name, parser, cls, ancestors)
        nodes[node.dotted_name] = node
        for canonical, _aliases, subparser in _parsers.unique_subcommands(parser, seen):
            sub_cls = getattr(subparser, "_duho_cls_", None)
            _walk(
                subparser,
                sub_cls,
                dotted_parts + (canonical,),
                canonical,
                ancestors + (node,),
            )

    root_name = _command_name(root_cls)
    _walk(root_parser, root_cls, (root_name,), root_name, ())

    result = (root_parser, nodes)
    _TREE_CACHE[root_cls] = result
    return result


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


def _input_schema_for_node(node: "_Node") -> "dict":
    """The full ``inputSchema`` :func:`describe_tools`/:func:`call_tool` use
    for one tree node: ``node``'s own fields merged with every ancestor's own
    fields (Decision -- a nested tool's schema must include its ancestors'
    fields, since MCP has no other way to supply a root/parent global to a
    dispatch that goes through the whole path at once), in root-to-leaf
    order so a field redeclared at a deeper level shadows the shallower one
    (schema shape AND required-ness), with any field satisfiable purely from
    the server's own environment/config dropped from ``required``.
    """
    properties: "dict" = {}
    required: "list[str]" = []
    for step in node.ancestors + (node,):
        clsargs = _introspect.get_clsargs(step.cls)
        level_names: "list[str]" = []
        level_required: "list[str]" = []
        for builder in step.cls._getargs_():
            name = builder.name
            level_names.append(name)
            decl = clsargs.get(name)
            schema, is_required = json_schema_for_field(decl, builder)
            properties[name] = schema
            if is_required:
                level_required.append(name)
        level_required = _drop_layer_satisfied(level_required, step.cls, step.parser)
        for name in level_names:
            if name in required and name not in level_required:
                required.remove(name)
        for name in level_required:
            if name not in required:
                required.append(name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _conflict_note(cls: "type[_Cmd]") -> str:
    """A short human-readable note for ``cls``'s ``NS(conflicts=...)`` groups.

    Exclusive groups are surfaced only as tool-description text in v1 (no
    ``oneOf``/``not`` JSON Schema encoding yet). Reuses
    ``duho.agenthelp._conflict_groups`` rather than re-deriving group
    membership. Returns ``""`` when the command declares no conflict groups.
    """
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
    note = _conflict_note(node.cls)
    if note:
        description = (description + "\n\n" + note).strip() if description else note
    return {
        "name": node.dotted_name,
        "description": description,
        "inputSchema": input_schema,
    }


def describe_tools(root_cls: "type[_Cmd]") -> "list[dict]":
    """Describe every callable command in ``root_cls``'s tree as MCP tool specs.

    Each ``Cmd`` reached by walking the built parser tree -- the root itself
    (when it can itself be dispatched), and every ``_subcommands_`` node,
    recursively -- becomes one tool ``{name, description, inputSchema}``: a
    leaf/root tool is named after its own ``_parsername_``/class name, a
    nested one ``parent.child``. A NAMESPACE node (one whose own subcommand is
    mandatory -- see :func:`_is_namespace_node`) is skipped: it can never
    itself dispatch successfully, so listing it would only ever waste a
    client's turn on a guaranteed usage error; its own fields are still
    reachable, merged into every one of its descendants' schemas.
    """
    _root_parser, nodes = _tree_for(root_cls)
    return [
        _tool_spec(node)
        for node in nodes.values()
        if not _is_namespace_node(node.parser)
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


def _reject_unsafe_positional(token: str, parser: "_argparse.ArgumentParser") -> None:
    """Refuse a positional token argparse would parse as an option or as the
    ``--`` passthrough separator, rather than silently mis-parsing it or
    letting it leak into ``_passthrough_``."""
    if token == "--" or (
        token.startswith("-")
        and token != "-"
        and not _looks_like_negative_number(token, parser)
    ):
        raise ValueError(
            "value %r cannot be passed as a positional argument: it would "
            "be parsed as an option (or the '--' passthrough separator)" % (token,)
        )


def _reject_unsafe_value(token: str, flag: str) -> None:
    """Refuse a value equal to the literal string ``"--"``: some argparse
    versions strip a bare ``--`` from an attached ``--flag=--`` value."""
    if token == "--":
        raise ValueError(
            "value '--' cannot be passed to %s (argparse may strip a bare "
            "'--' from an attached option value)" % (flag,)
        )


def _emit_option(argv: "list[str]", flag: str, is_long: bool, token: str) -> None:
    """Append one option occurrence for ``token``: a long flag is
    always attached with ``=`` so argparse never reinterprets the value; a
    short-flag-only field refuses a value that looks like another option
    (there is no safe attached form for a short flag)."""
    _reject_unsafe_value(token, flag)
    if is_long:
        argv.append("%s=%s" % (flag, token))
        return
    if token.startswith("-") and token != "-":
        raise ValueError(
            "value %r cannot be passed to %s: it has no long form to attach "
            "the value to safely" % (token, flag)
        )
    argv.extend([flag, token])


def _synthesize_argv(
    cls: type, arguments: "dict", parser: "_argparse.ArgumentParser"
) -> "list[str]":
    """Turn a JSON ``arguments`` object into argv for ``cls``'s OWN fields.

    Iterates ``cls._getargs_()`` in declaration order. A field absent from
    ``arguments``, or explicitly ``null``, contributes nothing (JSON
    ``null`` means "not supplied", never the literal string ``"None"``).
    Branches on the field's EFFECTIVE ``argparse`` action
    (``builder._kwargs()["action"]``), not a re-derived guess, so this never
    drifts from what ``add_to_parser`` itself would register:

    * a bare bool flag (``store_true``/``store_false``/``BooleanOptionalAction``)
      -> ``True`` emits the bare flag; ``False`` emits ``--no-<flag>`` when the
      field defaults to ``True`` (raising if the field has no long flag to
      negate), else is omitted entirely.
    * a counting flag (``-v``/``-q`` style) -> the flag repeated ``value`` times.
    * ``store_const``/``append_const`` -> the bare flag when ``value`` is truthy.
    * a ``nargs="?"`` OPTION given an actual JSON boolean -> the bare flag when
      ``True`` (the option's own ``const``), nothing when ``False``.
    * a ``dict`` field -> one ``KEY=VALUE`` token per item, repeating the flag.
    * a ``list``/``set``/``tuple`` field -> one token per element, repeating
      the flag (a positional repeats bare tokens with no flag).
    * anything else (str/int/float/``Literal[True, False]``/Enum/Path/a custom
      ``action=``/``type=`` with no registered override) -> ``str(value)``.

    Every option value is emitted as a single attached ``--flag=value`` token
    (never ``[flag, value]``), so a value starting with ``-`` can never be
    reinterpreted as a different flag; a positional value that would be
    parsed as an option (or the ``--`` passthrough separator) is refused
    outright, since there is no safe way to escape it.
    """
    argv: "list[str]" = []
    for builder in cls._getargs_():
        name = builder.name
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

        if builder.is_bare_bool_flag:
            if value:
                argv.append(flag)
            elif builder.default is True:
                if not is_long:
                    raise ValueError(
                        "field %r defaults to True and has no long flag to "
                        "negate; false cannot be expressed over MCP" % (name,)
                    )
                argv.append("--no-" + flag[2:])
            continue

        if action == "count":
            count = value if isinstance(value, int) else int(value)
            if count < 0:
                raise ValueError(
                    "field %r (a counting flag) cannot be negative" % (name,)
                )
            argv.extend([flag] * count)
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
            for key, val in value.items():
                token = "%s=%s" % (key, val)
                if is_positional:
                    _reject_unsafe_positional(token, parser)
                    argv.append(token)
                else:
                    _emit_option(argv, flag, is_long, token)
            continue

        if builder.collection in (list, set, tuple):
            if not isinstance(value, (list, tuple, set)):
                raise ValueError("field %r expects a JSON array" % (name,))
            for item in value:
                token = str(item)
                if is_positional:
                    _reject_unsafe_positional(token, parser)
                    argv.append(token)
                else:
                    _emit_option(argv, flag, is_long, token)
            continue

        token = str(value)
        if is_positional:
            _reject_unsafe_positional(token, parser)
            argv.append(token)
        else:
            _emit_option(argv, flag, is_long, token)
    return argv


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
    """Reject ``arguments`` against ``schema``: an unknown property (the
    schema always declares ``additionalProperties: false``) or a value whose
    JSON type does not match its property's declared ``type`` -- e.g. the
    string ``"false"`` for a boolean field, which used to be truthy and
    silently turn the flag ON. Raises :class:`InvalidArgumentsError` naming
    every problem found, rather than stopping at the first one.
    """
    properties = schema.get("properties", {})
    errors = []
    for key in arguments:
        if key not in properties:
            errors.append("unknown argument %r" % (key,))
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


def call_tool(root_cls: "type[_Cmd]", name: object, arguments: object) -> "dict":
    """Dispatch one MCP ``tools/call`` against ``root_cls``'s tree.

    Resolves ``name`` to a node in the cached tree (:func:`_tree_for`),
    raising :class:`UnknownToolError` for a name that is not in the tree, or
    that names a namespace node (see :func:`_is_namespace_node`) -- neither
    can ever be dispatched. Raises :class:`InvalidArgumentsError` when
    ``arguments`` is not a JSON object (``None`` is treated as ``{}``) or
    fails the tool's own merged ``inputSchema`` (:func:`_validate_arguments`).
    Both are request-level problems, mapped by :func:`serve` to a JSON-RPC
    error response rather than a tool result.

    Otherwise: synthesizes one argv per level of the tool's ancestry chain
    (root first) via :func:`_synthesize_argv`, with the next level's own
    subcommand name token in between, and parses the WHOLE THING through the
    tree's single shared ROOT parser -- exactly as ``duho.main``/``duho.parse``
    would for the equivalent CLI invocation, so env/config layering, root
    globals, ``_passthrough_``, and (via :func:`duho.args._setup_instance_logging`)
    ``LoggingArgs`` verbosity setup all reach the dispatched command. Captures
    stdout AND stderr during dispatch, and replaces ``sys.stdin`` with an
    empty stream for its duration (a command honoring '-' = stdin
    must not be able to read the MCP client's next request off the real
    stdin).

    **Return convention**: ``run_command`` returns ``0`` for a
    ``None``/``0`` command return, an int for a non-zero return, or the raw
    object/list when the command returned one. Mapped here: ``0`` -> success,
    one text block of captured stdout; a non-zero int -> ``isError: true``,
    captured stdout + a trailing ``"exit code: N"`` line; anything else ->
    success, one text block holding its JSON dump. A ``SystemExit`` from
    argument PARSING (bad/missing argument) -> ``isError: true`` with the
    captured stderr text. A ``SystemExit`` raised by the command's OWN run
    time code is mapped by :func:`_systemexit_result` instead of escaping and
    killing the server. Any OTHER raised exception during dispatch ->
    ``isError: true`` with the exception's ``type: message`` text.
    ``KeyboardInterrupt`` is not caught and still propagates.
    """
    root_parser, nodes = _tree_for(root_cls)
    node = nodes.get(name) if isinstance(name, str) else None
    if node is None or _is_namespace_node(node.parser):
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
    try:
        argv: "list[str]" = []
        for i, step in enumerate(chain):
            argv.extend(_synthesize_argv(step.cls, arguments, step.parser))
            if i + 1 < len(chain):
                argv.append(chain[i + 1].own_name)
    except ValueError as exc:
        return _text_result(str(exc), is_error=True)

    out = _io.StringIO()
    err = _io.StringIO()
    all_parsers = [root_parser] + [n.parser for n in nodes.values()]
    try:
        with _contextlib.redirect_stdout(out), _contextlib.redirect_stderr(err):
            old_stdin = _sys.stdin
            _sys.stdin = _io.StringIO("")
            try:
                with _muted_color(all_parsers):
                    try:
                        instance = root_parser.parse_args(argv)
                    except SystemExit as exc:
                        message = err.getvalue().strip() or (
                            "argument error (exit code %r)" % (exc.code,)
                        )
                        return _text_result(message, is_error=True)
                    _setup_instance_logging(instance, True, root_cls)
                    try:
                        result = _run_command(node.cls, instance)
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
        text = "%s\n%s" % (stdout_text, trailing) if stdout_text else trailing
        return _text_result(text, is_error=True)

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


def _write_message(stream: object, message: "dict") -> None:
    import json

    # `ensure_ascii=True` (never False): the OUTPUT stream's own encoding is
    # not always known to be UTF-8-safe (an injected stream, or a
    # not-yet-reconfigured real stdout), so every non-ASCII character is
    # escaped to a plain-ASCII `\uXXXX` sequence -- still valid JSON, and
    # correct for any text stream whatsoever.
    stream.write(json.dumps(message, ensure_ascii=True) + "\n")
    flush = getattr(stream, "flush", None)
    if callable(flush):
        flush()


def _error_response(req_id: object, code: int, message: str) -> "dict":
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _handle_request(root_cls: "type[_Cmd]", request: object) -> "dict | None":
    """Dispatch one decoded JSON-RPC request; return the response dict, or ``None``.

    ``None`` means "no response" -- either the request was a **notification**
    (no ``id`` key at all; JSON-RPC forbids replying to one), the
    ``notifications/initialized`` notification specifically, or a malformed
    envelope that also happened to carry no ``id``.

    Every value pulled out of ``request`` is type-checked before use, so a
    well-formed JSON document that is not a well-formed JSON-RPC REQUEST
    (a bare scalar, a batch array, a non-object ``params``, a non-string
    ``name``, a non-object ``arguments``) gets a proper JSON-RPC error
    response instead of an uncaught ``AttributeError``/``TypeError`` that
    would otherwise propagate out of :func:`serve` and end the process
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
            "serverInfo": {"name": _SERVER_NAME, "version": _SERVER_VERSION},
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

    Duplicates the CURRENT fd 0/1 as fresh UTF-8/LF-normalised text streams
    for the protocol itself, then points the process's real fd 1 at fd 2
    (stderr) and fd 0 at the null device for the rest of the server's life,
    and rebinds ``sys.stdout``/``sys.stdin`` to match. This is what makes
    the module docstring's "one broken command never crashes the whole
    server loop" promise hold even against code the command doesn't control:

    * a subprocess the command spawns WITHOUT capturing its own output
      inherits fd 1 -- now stderr, not the protocol pipe -- instead of
      injecting non-JSON lines into the stream a client is trying to parse;
    * a command honoring the "'-' = stdin" convention, or any low-level
      ``os.read(0, ...)``, gets an immediate EOF on the (now devnull) fd 0
      instead of consuming the client's NEXT request.

    Only used when :func:`serve` is called with neither ``stdin`` nor
    ``stdout`` injected (the real ``python -m duho.mcp <app>`` path); a test
    driving ``serve`` over ``io.StringIO`` is unaffected.
    """
    _sys.stdout.flush()
    proto_in_fd = _os.dup(0)
    proto_out_fd = _os.dup(1)
    devnull_fd = _os.open(_os.devnull, _os.O_RDONLY)
    _os.dup2(devnull_fd, 0)
    _os.close(devnull_fd)
    _os.dup2(2, 1)

    stream_in = _os.fdopen(proto_in_fd, "r", encoding="utf-8", newline="\n")
    stream_out = _os.fdopen(proto_out_fd, "w", encoding="utf-8", newline="\n")
    _sys.stdin = _os.fdopen(_os.dup(0), "r", encoding="utf-8")
    _sys.stdout = _sys.stderr
    return stream_in, stream_out


def serve(
    root_cls: "type[_Cmd]",
    *,
    stdin: "_ty.Optional[_ty.TextIO]" = None,
    stdout: "_ty.Optional[_ty.TextIO]" = None,
) -> int:
    """Run the stdio JSON-RPC loop for ``root_cls`` until stdin closes (EOF).

    Reads newline-delimited JSON-RPC 2.0 request lines from ``stdin`` (real
    stdio, isolated per :func:`_real_stdio_streams`, when neither ``stdin``
    nor ``stdout`` is given), dispatches each via :func:`_handle_request`, and
    writes any response line to ``stdout``, flushed every time. A line that
    fails to parse as JSON gets a ``-32700`` parse-error response (``id:
    null`` -- the malformed line's own id, if any, is unrecoverable). An
    unexpected exception from :func:`_handle_request` itself (which should
    never happen, given its own internal error handling, but must never end
    the server if it somehow does) becomes a ``-32603`` response instead of
    propagating. Blank lines are skipped. Returns ``0`` when ``stdin``
    reaches EOF (there is no separate MCP "shutdown" method to wait for).
    ``stdin``/``stdout`` are injectable so tests can drive the loop over
    in-memory streams instead of real pipes.
    """
    import json

    if stdin is None and stdout is None:
        stream_in, stream_out = _real_stdio_streams()
    else:
        stream_in = stdin if stdin is not None else _sys.stdin
        stream_out = stdout if stdout is not None else _sys.stdout

    for line in stream_in:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            _write_message(stream_out, _error_response(None, -32700, "parse error"))
            continue
        try:
            response = _handle_request(root_cls, request)
        except Exception as exc:  # noqa: BLE001 - the loop itself must never die
            _log_exception(
                _LOGGER,
                "serve() request handling raised: %s: %s",
                type(exc).__name__,
                exc,
            )
            fallback_id = request.get("id") if isinstance(request, dict) else None
            response = _error_response(
                fallback_id,
                -32603,
                "internal error: %s: %s" % (type(exc).__name__, exc),
            )
        if response is not None:
            _write_message(stream_out, response)
    return 0


def main(argv: "_ty.Sequence[str] | None" = None) -> int:
    """``python -m duho.mcp <app>`` entry point: resolve ``<app>`` and run :func:`serve`.

    ``<app>`` is a dotted qualname to a ``Cmd``/``Cli`` subclass (see
    :func:`_resolve_app`). No arguments prints a usage line to stderr and
    returns ``2``; ``-h``/``--help`` prints the same usage line and returns
    ``0`` (previously treated as an ``<app>`` spec and reported as
    unresolvable). Prints a one-line error to stderr and returns a non-zero
    exit code if ``<app>`` does not resolve; otherwise runs the stdio loop
    against real stdin/stdout and returns its exit code.
    """
    args = list(argv) if argv is not None else _sys.argv[1:]
    if not args:
        print("usage: python -m duho.mcp <app>", file=_sys.stderr)
        return 2
    if args[0] in ("-h", "--help"):
        print("usage: python -m duho.mcp <app>", file=_sys.stderr)
        return 0
    try:
        root_cls = _resolve_app(args[0])
    except Exception as exc:  # noqa: BLE001 - report, don't traceback, a bad app spec
        print(
            "duho.mcp: could not resolve app %r: %s" % (args[0], exc), file=_sys.stderr
        )
        return 1
    return serve(root_cls)


if __name__ == "__main__":
    _sys.exit(main())
