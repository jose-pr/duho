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

**Zero-dep stdlib JSON-RPC over stdio** (Decision 1 of the owning plan) -- no MCP
SDK dependency. ``json`` (and ``importlib.metadata``, transitively reachable via
``pkgutil.resolve_name``'s import machinery for the ``<app>`` CLI arg) are
imported **lazily**, function-local, never at module top -- so ``import
duho.mcp`` alone never pays their cost, mirroring the zero-eager-import
contract ``import duho`` already keeps (see ``tests/test_config_json.py``,
``tests/test_entry_points.py``).

Three layers, thin glue between them:

* :func:`input_schema_for_command` / :func:`json_schema_for_field` -- step 1,
  the ``type -> JSON Schema`` emitter. Standalone and parser-free: it reads a
  command class's own field declarations directly (``cls._getargs_()`` +
  ``duho._introspect.get_clsargs(cls)``), not a built argparse parser.
* :func:`describe_tools` -- step 2. Walks the *built* parser tree (reusing
  the same alias-dedup-by-identity pattern as ``duho.agenthelp.describe_parser``)
  so every ``Cmd`` in a ``_subcommands_`` tree -- root included -- becomes one
  MCP tool, namespaced ``parent.child`` when nested.
* :func:`call_tool` -- step 3. Resolves a tool name back to its class,
  synthesizes an argv from the JSON ``arguments`` (Decision 3: JSON property ->
  ``--flag value``; a repeatable field -> a repeated flag; a positional -> a
  bare token in declared order), reuses the class's own ``_parser_()`` +
  :func:`duho.run_command` to dispatch, and maps the result per Decision 4
  (see below).

**Return convention** (the one new contract this module adds -- Decision 4):
during a call, stdout is captured. A command returning ``None``/``0`` -> a
success result whose one ``text`` content block is the captured stdout (empty
string allowed). A non-zero int -> ``isError: true``, text = captured stdout +
a trailing ``"exit code: N"`` line. A JSON-serialisable object/list return ->
passed through as one ``text`` block holding its JSON dump (structured
content-block mapping is a later refinement). An exception raised during
dispatch (not covered by the plan's Decision 4, which only specifies return
values) is mapped to ``isError: true`` with the exception's ``type: message``
text -- the uncontroversial, robustness-first choice so one broken command
never crashes the whole JSON-RPC server loop.

**Documented v1 limitations** (Decision 3/6/7, spelled out rather than silently
wrong): a custom ``action=``/``type=`` field with no registered override is
passed through as a plain string, verbatim; ``NS(conflicts=...)`` exclusive
groups are surfaced only as a note appended to the tool's description text
(no ``oneOf``/``not`` JSON Schema encoding yet); a *module* command (no duho
class behind its subparser -- no ``ArgumentBuilder``/``ClsArgDeclaration``
metadata) gets a best-effort schema from its own argparse actions alone, and
calling one is refused with a clear error (module-command dispatch through MCP
is not implemented in v1); streaming/long-running commands are out of scope --
this is strictly one request -> one result.

All union annotations are quoted so the module imports cleanly on Python 3.9.
"""

import argparse as _argparse
import datetime as _datetime
import enum as _enum
import pathlib as _pathlib
import sys as _sys
import typing as _ty

from . import _compat as _compat
from . import _introspect as _introspect
from . import agenthelp as _agenthelp
from .args import Cmd as _Cmd
from .runtime import run_command as _run_command

__all__ = [
    "json_schema_for_field",
    "input_schema_for_command",
    "describe_tools",
    "call_tool",
    "serve",
    "main",
]

_LOGGER_NAME = "duho"

_NOT_DEFINED = _introspect.NOT_DEFINED
_NONETYPE = type(None)

#: MCP protocol version this server declares in ``initialize`` when the client
#: does not pin one worth echoing back (v1 always echoes the client's own
#: ``protocolVersion`` when present -- see :func:`_handle_request`).
_PROTOCOL_VERSION = "2024-11-05"

#: ``serverInfo.name``/``version`` reported in the ``initialize`` result.
_SERVER_NAME = "duho.mcp"
_SERVER_VERSION = "1"

#: ISO-format stdlib types (mirrors ``args._ISOFORMAT_FACTORIES``) mapped to a
#: JSON Schema ``format`` hint. All three collapse to ``"type": "string"`` --
#: same as ``pathlib.Path`` -- since JSON Schema has no native date type.
_ISO_FORMATS = {
    _datetime.date: "date",
    _datetime.datetime: "date-time",
    _datetime.time: "time",
}


# --------------------------------------------------------------------------
# Step 1: type -> JSON Schema
# --------------------------------------------------------------------------


def _schema_for_type(tp: object) -> "dict":
    """Map one declared annotation to a JSON Schema type fragment (no title/description).

    Standalone recursive dispatch, mirroring ``duho.args._factory_for``'s own
    branch order but targeting JSON Schema instead of argparse kwargs:

    * ``Literal[...]`` -> ``enum`` (+ ``type`` when every literal shares one
      JSON-representable type; a mixed-type literal is ``enum`` alone).
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
      hatch (Decision 3): passed through as text rather than a silently wrong
      schema.
    """
    origin = _ty.get_origin(tp)
    args = _ty.get_args(tp)

    if origin is _ty.Literal:
        values = list(args)
        types = {type(v) for v in values}
        schema: "dict" = {"enum": values}
        if types == {str}:
            schema["type"] = "string"
        elif types == {bool}:
            schema["type"] = "boolean"
        elif types == {int}:
            schema["type"] = "integer"
        elif types == {float}:
            schema["type"] = "number"
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

    if tp is bool:
        return {"type": "boolean"}
    if tp is int:
        return {"type": "integer"}
    if tp is float:
        return {"type": "number"}
    if tp is str:
        return {"type": "string"}

    return {"type": "string"}


def _is_required(builder: object) -> bool:
    """Whether a field must be supplied (no usable default at all).

    Uses ``builder._effective_default_()`` -- not the raw ``builder.default``
    -- so it agrees with what argparse would actually leave the field at when
    absent (e.g. a bare ``flag: bool`` field with no explicit ``= False``
    still resolves to a ``store_true`` default of ``False``, never required,
    even though ``builder.default`` itself is duho's ``NOT_DEFINED`` sentinel
    before that normalisation). Falls back to ``builder.required`` for the one
    case ``_effective_default_()`` can't see: an ``Optional[T]`` field with NO
    explicit default has no ``default=`` kwarg at all (argparse leaves it at
    its own implicit ``None``), but ``ArgumentBuilder._argbuilder_`` already
    marks such a field ``required=False`` explicitly -- exactly the signal
    needed to honor the plan's "``Optional[T]``/``Union`` -> omit from
    required" rule.
    """
    if builder._effective_default_() is not _NOT_DEFINED:
        return False
    if builder.required is False:
        return False
    return True


def json_schema_for_field(
    decl: "_introspect.ClsArgDeclaration | None", builder: object
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
    resolves to. The field's docstring (preferred) or ``builder.help`` becomes
    the schema's ``"description"`` when non-empty.
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

    help_text = ""
    if decl is not None and decl.docstring:
        help_text = decl.docstring
    else:
        raw_help = getattr(builder, "help", "") or ""
        help_text = raw_help() if callable(raw_help) else raw_help
    if help_text:
        schema["description"] = help_text

    return schema, required


def input_schema_for_command(cls: type) -> "dict":
    """Assemble a JSON-Schema ``object`` describing ``cls``'s own fields.

    ``properties``/``required``/``additionalProperties: false`` from
    ``cls._getargs_()`` (each field's ``ArgumentBuilder``, in declaration
    order) + ``duho._introspect.get_clsargs(cls)`` (the matching
    ``ClsArgDeclaration``, for the raw annotation + docstring). Only ``cls``'s
    OWN fields -- not an inherited root's, not framework-injected argparse-only
    actions (``--version``/``--help``/``--print-completion``/``--help-agents``,
    none of which have an ``ArgumentBuilder`` behind them, so they're never
    reached by this per-builder walk at all) -- per Decision 6, "CLI-only
    concepts simply don't cross over".
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
# Step 2: describe_tools
# --------------------------------------------------------------------------


def _iter_subcommands(parser: "_argparse.ArgumentParser", _seen: "set") -> "_ty.Iterator[tuple]":
    """Yield ``(canonical_name, subparser)`` once per DISTINCT subcommand of ``parser``.

    Mirrors ``duho.agenthelp.describe_parser``'s alias-dedup-by-identity
    exactly: argparse registers every alias as an extra ``choices`` key
    pointing at the SAME subparser object, so entries are grouped by
    ``id(subparser)`` and each is yielded exactly once, under its canonical
    name (the subcommand class's own ``_parsername_`` if it is one of the
    registered names, else the first-seen key -- same tie-break
    ``describe_parser`` uses). ``_seen`` is the caller's running set of
    already-yielded subparser ids, threaded through recursive calls so a
    subparser reached twice (should not normally happen, but mirrors the
    defensive guard in ``describe_parser``) is not described twice.
    """
    subparsers_action = None
    for action in parser._actions:
        if isinstance(action, _argparse._SubParsersAction):
            subparsers_action = action
            break
    if subparsers_action is None:
        return

    grouped: "dict" = {}
    order: "list" = []
    for choice_name, subparser in (subparsers_action.choices or {}).items():
        key = id(subparser)
        if key not in grouped:
            grouped[key] = {"parser": subparser, "names": []}
            order.append(key)
        grouped[key]["names"].append(choice_name)

    for key in order:
        if key in _seen:
            continue
        _seen.add(key)
        entry = grouped[key]
        subparser = entry["parser"]
        names = entry["names"]
        sub_cls = getattr(subparser, "_duho_cls_", None)
        canonical = getattr(sub_cls, "_parsername_", None) if sub_cls else None
        if canonical not in names:
            canonical = names[0]
        yield canonical, subparser


def _walk_command_tree(root_cls: type) -> "_ty.Iterator[tuple]":
    """Yield ``(dotted_name, parser, cls)`` for every node in ``root_cls``'s tree.

    ``dotted_name`` is the MCP tool name: the root's own ``_parsername_``/class
    name, or ``parent.child`` (recursing) for a nested subcommand. ``cls`` is
    the duho class behind that node's parser (``parser._duho_cls_``, ALWAYS
    stashed by ``Args._initparser_`` -> ``_install_agent_help`` for every
    parser build, root or subcommand, independent of whether agent-help itself
    is opted into -- see the module docstring's Known-Facts cross-reference),
    or ``None`` for a subparser with no duho class behind it (a module
    command, or a bare argparse parser). Builds ``root_cls._parser_()`` once
    per call.
    """
    parser = root_cls._parser_()
    root_name = getattr(root_cls, "_parsername_", None) or root_cls.__name__
    seen: "set" = set()

    def _walk(node_parser, dotted_parts):
        cls = getattr(node_parser, "_duho_cls_", None)
        yield ".".join(dotted_parts), node_parser, cls
        for canonical, subparser in _iter_subcommands(node_parser, seen):
            for item in _walk(subparser, dotted_parts + (canonical,)):
                yield item

    for item in _walk(parser, (root_name,)):
        yield item


def _conflict_note(cls: type) -> str:
    """A short human-readable note for ``cls``'s ``NS(conflicts=...)`` groups.

    Decision 6: exclusive groups are surfaced only as tool-description text in
    v1 (no ``oneOf``/``not`` JSON Schema encoding yet). Reuses
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


def _input_schema_from_parser(parser: "_argparse.ArgumentParser") -> "dict":
    """Best-effort ``inputSchema`` for a subparser with no duho class behind it.

    A module command's arguments are added directly via ``parser.add_argument``
    in its ``register`` hook -- no ``ArgumentBuilder``/``ClsArgDeclaration``
    metadata exists for them at all. This walks the parser's OWN actions with
    plain heuristics (``choices`` -> a string enum, ``nargs == 0`` -> boolean,
    a repeatable/``append`` action -> an array, else the action's ``type=``
    factory if it is a recognised builtin, else string) -- deliberately less
    rich than :func:`input_schema_for_command`, and documented as a v1
    limitation (module-command *calling* is refused entirely -- see
    :func:`call_tool` -- this schema exists so ``tools/list`` can still
    describe every node in the tree without crashing).
    """
    properties: "dict" = {}
    required: "list[str]" = []
    for action in parser._actions:
        if isinstance(action, _argparse._SubParsersAction):
            continue
        if isinstance(action, (_argparse._HelpAction, _argparse._VersionAction)):
            continue
        dest = action.dest
        if not dest or dest == _argparse.SUPPRESS or dest == "command":
            continue

        choices = getattr(action, "choices", None)
        if choices:
            schema: "dict" = {"type": "string", "enum": [str(c) for c in choices]}
        elif action.nargs == 0:
            schema = {"type": "boolean"}
        else:
            factory = getattr(action, "type", None)
            schema = {"type": _SIMPLE_TYPE_NAMES.get(factory, "string")}

        if action.nargs in ("*", "+") or isinstance(action, _argparse._AppendAction):
            schema = {"type": "array", "items": schema}

        if action.help:
            schema["description"] = action.help

        default = action.default
        if default is not _argparse.SUPPRESS and default is not None:
            schema["default"] = _agenthelp._jsonable(default)

        properties[dest] = schema
        is_positional = not action.option_strings
        if getattr(action, "required", False) or (
            is_positional and action.nargs not in ("?", "*")
        ):
            required.append(dest)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


_SIMPLE_TYPE_NAMES = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _tool_spec(name: str, parser: "_argparse.ArgumentParser", cls: "type | None") -> "dict":
    """Build one MCP ``{name, description, inputSchema}`` tool spec for a node."""
    description = (parser.description or "").replace("%%", "%").strip()
    if cls is not None:
        input_schema = input_schema_for_command(cls)
        note = _conflict_note(cls)
        if note:
            description = (description + "\n\n" + note).strip() if description else note
    else:
        input_schema = _input_schema_from_parser(parser)
    return {"name": name, "description": description, "inputSchema": input_schema}


def describe_tools(root_cls: type) -> "list[dict]":
    """Describe every command in ``root_cls``'s tree as a list of MCP tool specs.

    Each ``Cmd`` reached by walking the built parser tree -- the root itself,
    AND every ``_subcommands_`` node, recursively -- becomes one tool
    ``{name, description, inputSchema}``: a leaf/root tool is named after its
    own ``_parsername_``/class name, a nested one ``parent.child`` (step 2).
    Building a tool for every level (not just leaves) matches the plan's "each
    ``Cmd`` -> one MCP tool" literally; a non-leaf node whose ``__call__`` was
    never overridden (the common case for an app-root ``Cli`` that only exists
    to hold subcommands) simply surfaces ``Cmd``'s own ``NotImplementedError``
    as an ``isError`` result if actually called (see :func:`call_tool`) --
    arguably informative (distinguishes "this is a namespace" from "this is a
    leaf action") rather than a bug.
    """
    return [_tool_spec(name, parser, cls) for name, parser, cls in _walk_command_tree(root_cls)]


# --------------------------------------------------------------------------
# Step 3: return convention + call_tool
# --------------------------------------------------------------------------


def _negate_flag(flag: str) -> str:
    """Best-effort ``--no-<x>`` negation of a long flag (for ``BooleanOptionalAction``)."""
    if flag.startswith("--"):
        return "--no-" + flag[2:]
    return flag  # pragma: no cover - short flags have no negated form


def _synthesize_argv(cls: type, arguments: "dict") -> "list[str]":
    """Turn a JSON ``arguments`` object into argv for ``cls``'s own parser (Decision 3).

    Iterates ``cls._getargs_()`` in declaration order (options and positionals
    interleaved exactly as declared -- argparse tolerates an option anywhere
    relative to positionals, and positionals keep their relative order to each
    other this way, so no separate "positionals last" pass is needed). A field
    absent from ``arguments`` contributes nothing (argparse's own
    default/required handling then applies unchanged). Per field:

    * a plain ``bool`` (``store_true``/``BooleanOptionalAction``) -> ``True``
      emits the bare flag; ``False`` emits ``--no-<flag>`` when the field's
      default is ``True`` (``BooleanOptionalAction``), else is omitted
      entirely (absence already means ``False`` for a ``store_true`` field);
    * a ``dict`` field (``collection is dict``) -> one ``KEY=VALUE`` token
      per item, repeating the flag (``UpdateAction`` merges them);
    * a ``list``/``set``/``tuple`` field -> one token per element, repeating
      the flag (a positional repeats bare tokens with no flag);
    * anything else (str/int/float/Enum/Literal/Path/a custom ``action=``/
      ``type=`` with no registered override) -> ``str(value)``, the documented
      v1 escape hatch (Decision 3) -- passed through verbatim rather than
      guessing a serialisation.
    """
    argv: "list[str]" = []
    for builder in cls._getargs_():
        name = builder.name
        if name not in arguments:
            continue
        value = arguments[name]
        flags = builder.flags
        is_positional = len(flags) == 1 and not flags[0].startswith("-")
        flag = None if is_positional else flags[-1]

        if builder.type is bool and not builder.action and builder.collection is None:
            if value:
                argv.append(flag)
            elif builder.default is True:
                argv.append(_negate_flag(flag))
            continue

        if builder.collection is dict:
            items = value.items() if isinstance(value, dict) else ()
            for key, val in items:
                token = "%s=%s" % (key, val)
                argv.append(token) if is_positional else argv.extend([flag, token])
            continue

        if builder.collection in (list, set, tuple):
            seq = value if isinstance(value, (list, tuple, set)) else [value]
            for item in seq:
                token = str(item)
                argv.append(token) if is_positional else argv.extend([flag, token])
            continue

        token = str(value)
        argv.append(token) if is_positional else argv.extend([flag, token])
    return argv


def _text_result(text: str, *, is_error: bool = False) -> "dict":
    result: "dict" = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return result


def call_tool(root_cls: type, name: str, arguments: "dict | None") -> "dict":
    """Dispatch one MCP ``tools/call`` against ``root_cls``'s tree; return an MCP result.

    Resolves ``name`` to a target node (:func:`_walk_command_tree`), refusing
    an unknown name or a module-command node (no duho class -> not supported
    for CALLING in v1, only for listing) with ``isError: true``. Otherwise:
    synthesizes argv from ``arguments`` (:func:`_synthesize_argv`), parses it
    through the target class's OWN fresh parser (``cls._parser_()`` -- a
    ``Cmd``/``Cli`` subclass's parser is fully self-contained regardless of
    whether it is normally reached as a root or a subcommand, exactly the same
    property ``duho.parse``/``duho.print_completion`` already rely on), and
    dispatches via ``duho.run_command`` while capturing stdout AND stderr.

    **Return convention (Decision 4)**: ``run_command`` returns ``0`` for a
    ``None``/``0`` command return, an int for a non-zero return, or the raw
    object/list when the command returned one (``run_command`` only coerces
    ``None`` -> ``0``; anything else -- including a dict/list -- passes
    through unchanged). Mapped here: ``0`` -> success, one text block of
    captured stdout; a non-zero int -> ``isError: true``, captured stdout +
    a trailing ``"exit code: N"`` line; anything else -> success, one text
    block holding its JSON dump (structured content-block mapping is a later
    refinement). A ``SystemExit`` from argument parsing (bad/missing argument)
    -> ``isError: true`` with the captured stderr text (argparse's own usage
    error). Any OTHER raised exception during dispatch (a case Decision 4 does
    not cover -- it only specifies return values) -> ``isError: true`` with
    the exception's ``type: message`` text, so one broken command never
    crashes the server loop.
    """
    import contextlib
    import io

    nodes = {name_: cls for name_, _parser, cls in _walk_command_tree(root_cls)}
    if name not in nodes:
        return _text_result("unknown tool: %r" % (name,), is_error=True)
    cls = nodes[name]
    if cls is None:
        return _text_result(
            "tool %r is a module command; duho.mcp v1 only supports calling "
            "Cmd/Cli class commands (it can still be listed via tools/list)"
            % (name,),
            is_error=True,
        )

    target_parser = cls._parser_()
    argv = _synthesize_argv(cls, arguments or {})

    out = io.StringIO()
    err = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                instance = target_parser.parse_args(argv)
            except SystemExit as exc:
                message = err.getvalue().strip() or (
                    "argument error (exit code %r)" % (exc.code,)
                )
                return _text_result(message, is_error=True)
            result = _run_command(cls, instance)
    except Exception as exc:  # noqa: BLE001 - one broken command must not crash the server
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


def _resolve_app(spec: str) -> type:
    """Resolve the ``<app>`` CLI argument (a dotted qualname) to a root ``Cmd``/``Cli`` class.

    Uses the stdlib ``pkgutil.resolve_name`` (3.9+, imported lazily): it
    accepts BOTH the ``module.sub:ClassName`` colon syntax (the same
    convention this project's own entry-point tests/``discover_entry_points``
    use) and the legacy dotted ``module.sub.ClassName`` form (progressively
    importing shorter prefixes as a module, the remainder as attribute
    access). See the plan's "Deviations recorded during execution" note for
    why this -- not ``discovery.CmdBuilder`` -- resolves the app: ``CmdBuilder``
    always yields a ``Command`` (wrapping any module source in a
    ``ModuleCommand``), never the raw class this module needs to call
    :func:`describe_tools`/:func:`call_tool` on.
    """
    import pkgutil

    obj = pkgutil.resolve_name(spec)
    if not (isinstance(obj, type) and issubclass(obj, _Cmd)):
        raise TypeError(
            "%r does not resolve to a duho Cmd/Cli class (got %r)" % (spec, obj)
        )
    return obj


def _write_message(stream: object, message: "dict") -> None:
    import json

    stream.write(json.dumps(message, ensure_ascii=False) + "\n")
    flush = getattr(stream, "flush", None)
    if callable(flush):
        flush()


def _handle_request(root_cls: type, request: "dict") -> "dict | None":
    """Dispatch one decoded JSON-RPC request; return the response dict, or ``None``.

    ``None`` means "no response" -- either the request was a **notification**
    (no ``id`` key at all; JSON-RPC forbids replying to one) or it was the
    ``notifications/initialized`` notification specifically. See the
    ``## Protocol notes`` section of the owning plan for the exact
    method/field shapes implemented here.
    """
    method = request.get("method")
    has_id = "id" in request
    req_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        result = {
            "protocolVersion": params.get("protocolVersion", _PROTOCOL_VERSION),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": _SERVER_NAME, "version": _SERVER_VERSION},
        }
    elif method in ("notifications/initialized", "initialized"):
        return None
    elif method == "tools/list":
        result = {"tools": describe_tools(root_cls)}
    elif method == "tools/call":
        result = call_tool(root_cls, params.get("name"), params.get("arguments"))
    elif method in ("shutdown", "exit"):
        result = None
    elif not has_id:
        return None  # an unrecognised notification: nothing to reply to
    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": "method not found: %r" % (method,)},
        }

    if not has_id:
        return None  # a notification for a method we do handle: still no reply
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def serve(root_cls: type, *, stdin: object = None, stdout: object = None) -> int:
    """Run the stdio JSON-RPC loop for ``root_cls`` until stdin closes (EOF).

    Reads newline-delimited JSON-RPC 2.0 request lines from ``stdin`` (default
    ``sys.stdin``), dispatches each via :func:`_handle_request`, and writes any
    response line to ``stdout`` (default ``sys.stdout``), flushed every time.
    A line that fails to parse as JSON gets a ``-32700`` parse-error response
    (``id: null`` -- the malformed line's own id, if any, is unrecoverable).
    Blank lines are skipped. Returns ``0`` when ``stdin`` reaches EOF (there is
    no separate MCP "shutdown" method to wait for -- see the owning
    plan's protocol notes). ``stdin``/``stdout`` are injectable so tests can drive the loop
    over in-memory streams instead of real pipes.
    """
    import json

    stream_in = stdin if stdin is not None else _sys.stdin
    stream_out = stdout if stdout is not None else _sys.stdout

    for line in stream_in:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            _write_message(
                stream_out,
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}},
            )
            continue
        response = _handle_request(root_cls, request)
        if response is not None:
            _write_message(stream_out, response)
    return 0


def main(argv: "_ty.Sequence[str] | None" = None) -> int:
    """``python -m duho.mcp <app>`` entry point: resolve ``<app>`` and run :func:`serve`.

    ``<app>`` is a dotted qualname to a ``Cmd``/``Cli`` subclass (see
    :func:`_resolve_app`). Prints a one-line error to stderr and returns a
    non-zero exit code if it does not resolve; otherwise runs the stdio loop
    against real stdin/stdout and returns its exit code.
    """
    args = list(argv) if argv is not None else _sys.argv[1:]
    if not args:
        print("usage: python -m duho.mcp <app>", file=_sys.stderr)
        return 2
    try:
        root_cls = _resolve_app(args[0])
    except Exception as exc:  # noqa: BLE001 - report, don't traceback, a bad app spec
        print("duho.mcp: could not resolve app %r: %s" % (args[0], exc), file=_sys.stderr)
        return 1
    return serve(root_cls)


if __name__ == "__main__":
    _sys.exit(main())
