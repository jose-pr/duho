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

#: Lookup built from ``_ISOFORMAT_FACTORIES``'s keys, so a type added or removed
#: there raises ``KeyError`` here instead of the schema silently disagreeing
#: with the CLI.
_ISO_FORMATS = {tp: _ISO_FORMAT_NAMES[tp] for tp in _ISOFORMAT_FACTORIES}

#: Scalar Python type -> JSON Schema ``"type"`` name, shared by the Literal
#: branch and the final scalar fallback of :func:`_schema_for_type`.
_JSON_SCALARS = {bool: "boolean", int: "integer", float: "number", str: "string"}

#: Upper bound published (JSON Schema ``maximum``) and enforced by
#: :func:`_validate_arguments` for a counting flag: an unbounded value would
#: synthesize millions of repeated tokens and stall the single-threaded server.
_MAX_COUNT_VALUE = 10

#: Bound published (``maxItems``/``maxProperties``) and enforced by
#: :func:`_validate_arguments` on collections; a huge one would stall the
#: single-threaded stdio server.
_MAX_ARRAY_ITEMS = 1000

_MAX_OBJECT_PROPERTIES = 1000


# --------------------------------------------------------------------------
# Step 1: type -> JSON Schema
# --------------------------------------------------------------------------


def _schema_for_type(tp: object, enum_by: str = "name") -> dict:
    """Map one declared annotation to a JSON Schema type fragment.

    Mirrors ``duho.args._factory_for``'s branch order. ``Literal`` becomes
    ``enum``, with ``type`` when its values share one JSON type; an ``Enum``
    lists member names (values with ``enum_by="value"``). ``list``/``set``/
    ``tuple`` become capped arrays (``set`` adds ``uniqueItems``) and ``dict``
    a capped object, so an oversized LLM-supplied collection is refused before
    dispatch. A ``Union`` drops ``None`` and is ``anyOf`` when more than one
    member remains; paths and anything unrecognised are ``"string"``, dates
    add a ``format``. Required-ness is decided elsewhere, from the builder.
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

    Derived from ``builder._kwargs()``, the kwargs argparse itself is given, so
    the schema never diverges from what argparse enforces. Order: explicit
    ``required=True`` wins; a positional ``nargs="+"`` is required even with a
    default; a ``"default"`` kwarg or ``nargs`` of ``"?"``/``"*"`` is not
    required; otherwise a positional is required and an option follows its
    resolved ``required`` kwarg.
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

    An explicit ``help=`` override wins verbatim (MCP text is not
    ``%``-expanded, so it is not escaped); otherwise the raw field docstring.
    ``help=argparse.SUPPRESS`` yields ``""``, never the sentinel string.
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
        # Non-negative and capped: see `_MAX_COUNT_VALUE`.
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
