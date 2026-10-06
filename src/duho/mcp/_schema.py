from __future__ import annotations

import argparse as _argparse
import datetime as _datetime
import enum as _enum
import pathlib as _pathlib
import typing as _ty

from .. import _compat as _compat
from .. import _introspect as _introspect
from .. import agenthelp as _agenthelp
from ..args import ArgumentBuilder as _ArgumentBuilder
from ..args import Cmd as _Cmd
from .._fieldspec import _ISOFORMAT_FACTORIES as _ISOFORMAT_FACTORIES
from ..args._helptext import _escape_help as _escape_help

_NOT_DEFINED = _introspect.NOT_DEFINED

_NONETYPE = type(None)

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


# --------------------------------------------------------------------------
# Step 1: type -> JSON Schema
# --------------------------------------------------------------------------


def _schema_for_type(tp: object, enum_by: str = "name") -> dict:
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
      duho's standing convention; ``enum_by="value"`` lists ``str(member.value)``
      instead).
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
        schema: dict = {"enum": [_agenthelp._jsonable(v) for v in values]}
        if len(types) == 1:
            json_type = _JSON_SCALARS.get(next(iter(types)))
            if json_type:
                schema["type"] = json_type
        return schema

    if isinstance(tp, type) and issubclass(tp, _enum.Enum):
        return {"type": "string", "enum": _agenthelp._enum_members(tp, enum_by)}

    if origin is list or tp is list:
        elem = args[0] if args else str
        return {
            "type": "array",
            "items": _schema_for_type(elem, enum_by),
            "maxItems": _MAX_ARRAY_ITEMS,
        }

    if origin is set or tp is set:
        elem = args[0] if args else str
        return {
            "type": "array",
            "items": _schema_for_type(elem, enum_by),
            "uniqueItems": True,
            "maxItems": _MAX_ARRAY_ITEMS,
        }

    if origin is tuple or tp is tuple:
        elem = args[0] if args else str
        return {
            "type": "array",
            "items": _schema_for_type(elem, enum_by),
            "maxItems": _MAX_ARRAY_ITEMS,
        }

    if origin is dict or tp is dict:
        val = args[1] if len(args) > 1 else str
        return {
            "type": "object",
            "additionalProperties": _schema_for_type(val, enum_by),
            "maxProperties": _MAX_OBJECT_PROPERTIES,
        }

    if origin in _compat.UNION_ORIGINS:
        members = [a for a in args if a is not _NONETYPE]
        if len(members) == 1:
            return _schema_for_type(members[0], enum_by)
        if len(members) > 1:
            return {"anyOf": [_schema_for_type(m, enum_by) for m in members]}
        return {"type": "string"}

    if isinstance(tp, type) and issubclass(tp, _pathlib.PurePath):
        return {"type": "string"}

    if tp in _ISO_FORMATS:
        return {"type": "string", "format": _ISO_FORMATS[tp]}

    json_type = _JSON_SCALARS.get(tp)
    if json_type:
        return {"type": json_type}

    return {"type": "string"}


def _is_required(builder: _ArgumentBuilder) -> bool:
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
    decl: _introspect.ClsArgDeclaration | None, builder: _ArgumentBuilder
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
    decl: _ty.Optional[_introspect.ClsArgDeclaration], builder: _ArgumentBuilder
) -> tuple[dict, bool]:
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
    enum_by = getattr(builder, "enum_by", "name")
    schema = _schema_for_type(tp, enum_by) if tp is not None else {"type": "string"}

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
            schema["default"] = _agenthelp._jsonable(effective_default, enum_by)
        else:
            schema.setdefault("default", None)

    help_text = _description_for(decl, builder)
    if help_text:
        schema["description"] = help_text

    return schema, required


def input_schema_for_command(cls: type[_Cmd]) -> dict:
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
    properties: dict = {}
    required: list[str] = []
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
