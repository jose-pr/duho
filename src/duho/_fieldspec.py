"""The type -> argparse-spec ladder: one annotation, resolved once.

:func:`_factory_for` is the single dispatch ladder shared by a top-level
field, every Union member and every collection element or dict value
(``Literal``, ``Enum``, collections, ``dict``, ISO date types, ``Union``,
scalars). Each branch returns a :class:`_FieldSpec` bundling what it
determines: ``factory``, ``choices``, ``metavar``, ``action``, ``nargs``, the
empty ``default`` and ``collection``. ``Factory`` and ``UpdateAction`` are
re-exported from ``duho.args``.
"""

from __future__ import annotations

import argparse as _argparse
import collections as _collections
import datetime as _datetime
import enum as _enum
import sys as _sys
import typing as _ty

from . import _compat as _compat
from ._introspect import NOT_DEFINED as NOT_DEFINED

_NONETYPE = type(None)

_T = _ty.TypeVar("_T")

#: A text-to-value CLI/element/config-value converter: takes the raw string
#: (or, via ``ArgumentBuilder.convert_layered``, an already-native env/config
#: value) and returns the converted Python value. Public (``duho.Factory``) --
#: the type a user's own custom conversion callable is annotated with.
Factory = _ty.Callable[[str], _T]


class _ConversionError(_argparse.ArgumentTypeError, ValueError):
    """Raised by duho's own text factories so argparse shows OUR message.

    argparse keeps a type function's message only for ``ArgumentTypeError``; a
    plain ``ValueError`` gets a generic ``invalid <type> value``. Also a
    ``ValueError`` so the Union/Literal try-loops and the env/config layers,
    which catch ``(TypeError, ValueError)``, treat it as a rejection.
    """


class _LayeredChoiceError(ValueError):
    """A layered (env/config) value fails its field's ``choices`` check.

    The message omits the offending value, since an env or config value can be
    secret and the layering redaction otherwise collapses a failure to
    "expected <type>". Carrying ``choices`` lets that redaction show the CLI's
    "invalid choice" wording without the value.
    """

    def __init__(self, choices) -> None:
        self.choices = choices
        super().__init__(
            f"invalid choice (choose from {', '.join(map(repr, choices))})"
        )


def _bool_from_text(text, /):
    """Strict CLI-text-to-bool factory.

    Plain ``bool`` is not a valid factory (``bool("False")`` is true). Uses the
    token table shared with the env/config converter
    (:data:`duho.text.BOOL_TRUE`/:data:`duho.text.BOOL_FALSE`), passes a real
    ``bool`` through, and rejects any other non-string like unrecognized text.
    """
    if isinstance(text, bool):
        return text
    if isinstance(text, str):
        low = text.strip().lower()
        if low in _compat.BOOL_TRUE:
            return True
        if low in _compat.BOOL_FALSE:
            return False
    raise _ConversionError(f"{text!r} is not a valid boolean")


_bool_from_text.__name__ = "bool"


def _choice_checked(factory: Factory, choices) -> Factory:
    """Wrap `factory` so its result must be one of `choices`.

    A Union field never gets argparse's own ``choices=`` kwarg (it can't
    express "this member's choices OR any other member's"), so a Literal
    member composed into a multi-member Union needs its membership check
    enforced here instead -- otherwise the try-loop below accepts anything
    the member's bare TYPE conversion accepts, silently dropping the
    Literal's restriction.
    """

    def _checked(text: str, /, _factory=factory, _choices=choices):
        value = _factory(text)
        if value not in _choices:
            raise _ConversionError(f"{text!r} is not one of {_choices}")
        return value

    return _checked


def _enum_name_factory(enum_cls: type) -> Factory:
    """Build a factory that resolves CLI text to an enum member by NAME.

    Validates against ``enum_cls.__members__``, not iteration: iteration skips
    aliases and, since 3.11, multi-bit ``Flag`` composites. The "choose from"
    text lists only canonical names, matching the metavar. Raises
    :class:`_ConversionError` so argparse shows it and a Union try-loop falls
    through.
    """
    canonical = tuple(member.name for member in enum_cls)
    valid = frozenset(enum_cls.__members__)

    def _factory(text: str, /, _enum_cls=enum_cls, _valid=valid, _canonical=canonical):
        if text not in _valid:
            raise _ConversionError(
                f"invalid choice: {text!r} (choose from {', '.join(_canonical)})"
            )
        return _enum_cls[text]

    # Completion reads the canonical names from here; argparse's own
    # ``choices`` stays unset because it would compare converted members.
    _factory._duho_choices_ = canonical
    # Named after the enum: argparse and `_field_type_desc` read ``__name__``
    # for "invalid <type> value"/"expected <type>".
    _factory.__name__ = enum_cls.__name__
    return _factory


def _enum_value_factory(enum_cls: type, field: str) -> Factory:
    """Build a factory that resolves CLI text to an enum member by ``str(value)``.

    Two members whose values render to the same text cannot be told apart, so
    that is a build-time ``ValueError`` naming ``field``. An alias (a second
    name for the same member) is not a clash. Raises :class:`_ConversionError`
    for text that matches no member, like :func:`_enum_name_factory`.
    """
    by_text: dict[str, object] = {}
    for member in enum_cls.__members__.values():
        text = str(member.value)
        prior = by_text.setdefault(text, member)
        if prior is not member:
            raise ValueError(
                f"argument {field!r}: enum_by='value' needs distinct value "
                f"text, but {prior.name} and {member.name} of "
                f"{enum_cls.__name__} both render as {text!r}"
            )
    canonical = tuple(str(member.value) for member in enum_cls)

    def _factory(text: str, /, _by_text=by_text, _canonical=canonical):
        try:
            return _by_text[text]
        except (KeyError, TypeError):
            raise _ConversionError(
                f"invalid choice: {text!r} (choose from {', '.join(_canonical)})"
            ) from None

    _factory._duho_choices_ = canonical  # type: ignore[attr-defined]
    _factory.__name__ = enum_cls.__name__
    return _factory


class _CollectionAction(_argparse.Action):
    """Extend-and-coerce action for ``list``/``set``/``tuple`` fields.

    argparse's ``extend`` starts from the namespace value, so a CLI occurrence
    would merge onto a layered default. This action keeps its running elements
    in insertion order on a private sidecar (``_duho_items_<dest>``) that starts
    empty each parse: the first CLI occurrence REPLACES any class, env, config
    or instance default and later ones (repeated flags or space-separated
    values) accumulate. The final value is coerced to ``_collection_`` (``list``,
    ``set``, ``tuple`` or ``frozenset``, bound at build time); ``set`` dedups.
    """

    #: Target collection type; bound at construction.
    _collection_: type = tuple

    def __call__(self, parser, namespace, values, option_string=None):
        if values is self.default:
            # A zero-token variable-arity (nargs="*") POSITIONAL: argparse
            # hands the action's own default object back as `values` when no
            # tokens were consumed.
            from ._layers import _LayeredDefault  # lazy: avoids a circular

            # import (`_layers` imports this module's `_CollectionAction`/
            # `UpdateAction` at module scope to classify actions for env/
            # config layering).
            if isinstance(values, _LayeredDefault):
                # A not-yet-converted placeholder: pass it through unchanged so
                # `_finalize_layers` still sees the same object (`is`) and
                # converts it; coercing it would raise or discard it.
                setattr(namespace, self.dest, values)
                return
            # A real default: coerce a fresh collection from it. Otherwise a
            # `set` default crashes (unhashable), a `list` is doubled, and the
            # result would alias the action's default across parses.
            setattr(namespace, self.dest, self._collection_(values))
            return
        sidecar = "_duho_items_" + self.dest
        items = getattr(namespace, sidecar, None)
        if items is None:
            items = []
            setattr(namespace, sidecar, items)
        if isinstance(values, (list, tuple, set, frozenset)):
            items.extend(values)
        else:  # nargs unset / single value -- one flag occurrence
            items.append(values)
        setattr(namespace, self.dest, self._collection_(items))


def _collection_action(collection: type) -> type[_argparse.Action]:
    """Build a ``_CollectionAction`` subclass bound to a target collection."""

    class _BoundCollectionAction(_CollectionAction):
        _collection_ = collection

    return _BoundCollectionAction


class _AppendAction(_argparse.Action):
    """``duho.Append()``'s action: one scalar per flag occurrence, accumulated
    into a *list*.

    Like :class:`_CollectionAction`, the running list lives on a private
    per-parse sidecar (``_duho_items_<dest>``), never read from
    ``namespace.<dest>``, so the first occurrence starts a fresh list and
    replaces a layered default instead of appending to it (stdlib ``append``
    would).
    """

    def __call__(self, parser, namespace, values, option_string=None):
        sidecar = "_duho_items_" + self.dest
        items = getattr(namespace, sidecar, None)
        if items is None:
            items = []
            setattr(namespace, sidecar, items)
        items.append(values)
        setattr(namespace, self.dest, list(items))


class _NegatedBoolAction(_argparse.Action):
    """A bool flag whose own spelling reads as a negation (``no_verify`` ->
    ``--no-verify``), with a way back to ``False`` when env/config supply ``True``.

    ``argparse.BooleanOptionalAction`` refuses ``--no-`` option strings, so this
    one action carries the declared flags (``negative``, set ``True``) and a
    stripped positive-sense counterpart (set ``False``) under one dest: the
    layering code keys on one action per dest. It overwrites from
    ``option_string`` alone, so a ``_LayeredDefault`` placeholder is safe.
    """

    def __init__(self, option_strings, dest, negative, **kwargs):
        self._duho_negative_ = frozenset(negative)
        kwargs["nargs"] = 0  # a flag, like store_true/store_false -- no value
        super().__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, option_string in self._duho_negative_)


def _split_kv(text: str, name: str) -> tuple[str, str]:
    """Split a ``KEY=VALUE`` token on its first ``=``.

    Raises :class:`_ConversionError` naming the field when no ``=`` is
    present, so a ``dict`` field's ``--opt noequals`` shows this
    message on the CLI instead of argparse's generic
    ``invalid <_KVFactory object at 0x...> value``.
    """
    if "=" not in text:
        raise _ConversionError(
            f"argument {name!r}: expected KEY=VALUE, got {text!r} (no '=' found)"
        )
    key, value = text.split("=", 1)
    return key, value


class _KVFactory:
    """CLI-text factory for a ``dict[str, V]`` field.

    Converts one ``KEY=VALUE`` token into a one-pair dict, applying the value
    factory ``V`` to the value half; :class:`UpdateAction` merges successive
    pairs. The value factory is exposed as ``value_factory`` so the env/config
    layer (:meth:`ArgumentBuilder.convert_layered`) can convert a TOML *table*'s
    values the same way a CLI ``KEY=VALUE`` token would.
    """

    def __init__(self, name: str, value_factory: Factory):
        self.name = name
        self.value_factory = value_factory

    def __call__(self, text: str) -> dict:
        key, value = _split_kv(text, self.name)
        try:
            converted = self.value_factory(value)
        except (TypeError, ValueError):
            # An internal callable has no useful ``__name__``, so argparse's
            # fallback would show its raw repr; name the field and value type.
            type_name = getattr(self.value_factory, "__name__", None) or "value"
            raise _ConversionError(
                f"argument {self.name!r}: value {value!r} for key {key!r} "
                f"is not a valid {type_name}"
            ) from None
        return {key: converted}


def _isoformat_factory(cls: type) -> Factory:
    """Build the ``fromisoformat`` factory for a date/datetime/time `cls`.

    Before 3.11 ``fromisoformat`` rejects a trailing ``Z``, which RFC 3339 (and
    the ``date-time`` format ``duho.mcp`` advertises) allows. A trailing ``Z`` or
    ``z`` is rewritten on every version: 3.11+ accepts only an uppercase ``Z``.
    Basic (``YYYYMMDD``) formats stay unsupported.
    """
    accepts_z_natively = _sys.version_info >= (3, 11)

    def _factory(text: str, /, _cls=cls, _accepts_z=accepts_z_natively):
        # A non-str `text` (a native TOML/JSON date) has no `.endswith`; let
        # `fromisoformat` reject it with a TypeError, not an AttributeError.
        if isinstance(text, str) and text.endswith(("Z", "z")):
            text = text[:-1] + ("Z" if _accepts_z else "+00:00")
        return _cls.fromisoformat(text)

    _factory.__name__ = cls.__name__
    return _factory


#: Stdlib types whose constructor does NOT accept an ISO string; each maps to
#: its ``fromisoformat``-based factory so a CLI/date field converts cleanly
#: consistently across the supported Python range.
_ISOFORMAT_FACTORIES = {
    _datetime.date: _isoformat_factory(_datetime.date),
    _datetime.datetime: _isoformat_factory(_datetime.datetime),
    _datetime.time: _isoformat_factory(_datetime.time),
}


#: The per-type spec the branch ladder resolves. ``factory=None`` means "no
#: special factory -- keep the caller's seeded factory (a plain/custom type)".
#: ``default`` is :data:`NOT_DEFINED` when the branch imposes no default.
_FieldSpec = _collections.namedtuple(
    "_FieldSpec",
    "factory choices metavar action nargs default collection",
)


def _scalar_spec(factory=None) -> _FieldSpec:
    return _FieldSpec(factory, None, None, None, None, NOT_DEFINED, None)


def _literal_spec(args: tuple) -> _FieldSpec:
    """Spec for a ``Literal[...]`` annotation: choices + a round-trip factory."""
    literal_types: list = []
    for lit in args:
        lit_ty = type(lit)
        if lit_ty not in literal_types:
            literal_types.append(lit_ty)
    metavar = None
    if len(literal_types) == 1:
        lit_ty = literal_types[0]
        if lit_ty is bool:
            # bool is never a valid CLI text factory on its own: bool(text) is
            # true for almost any non-empty string, so Literal[True, False]
            # with "--flag False" silently became True.
            factory: Factory = _bool_from_text
        elif isinstance(lit_ty, type) and issubclass(lit_ty, _enum.Enum):
            # A Literal of Enum MEMBERS: the enum looks up by VALUE but the
            # choices are names, so resolve by NAME among this Literal's members.
            names = tuple(member.name for member in args)
            valid = frozenset(names)

            def factory(  # type: ignore[misc]
                text: str, /, _enum_cls=lit_ty, _valid=valid, _names=names
            ):
                if text not in _valid:
                    raise _ConversionError(
                        f"invalid choice: {text!r} (choose from "
                        f"{', '.join(_names)})"
                    )
                return _enum_cls[text]

            metavar = "{" + ",".join(names) + "}"
        else:
            factory = lit_ty
    else:
        # Mixed-type Literal: try each literal's own type, accepting only a
        # conversion that round-trips to a declared value (so str('1') cannot
        # shadow int(1) and bool(text) cannot shadow every other member).
        def factory(text: str, /, _literals=tuple(args)):  # type: ignore[misc]
            for lit in _literals:
                lit_ty = type(lit)
                convert = _bool_from_text if lit_ty is bool else lit_ty
                try:
                    candidate = convert(text)
                except (TypeError, ValueError):
                    continue
                if candidate == lit:
                    return candidate
            raise _ConversionError(
                f"could not convert {text!r} using any of {_literals}"
            )

        if bool in literal_types:
            # A mixed Literal that also accepts bool must accept a native bool
            # from env/config: `_convert_non_str` reads this attribute to widen a
            # raw bool for a composite factory.
            factory._duho_union_bool_ok_ = True  # type: ignore[attr-defined]

    return _FieldSpec(factory, tuple(args), metavar, None, None, NOT_DEFINED, None)


def _union_spec(members: list, name: str, enum_by: str = "name") -> _FieldSpec:
    """Spec for a Union of ``members`` (``None`` already stripped).

    Each member goes through :func:`_factory_for`. A single member
    (``Optional[T]``) adopts T's entire spec, so an ``Optional[bool]`` keeps its
    flag action. A multi-member union composes the member factories in order and
    rejects a member needing a special ``action`` (a collection), since argparse
    cannot switch actions per value. In that composition a ``bool`` member uses
    the strict :func:`_bool_from_text` and a member with ``choices`` is
    membership-checked, because a union field never gets argparse's ``choices=``.
    """
    member_specs = [_member_spec(m, name, enum_by) for m in members]
    resolved_factories = [
        spec.factory if spec.factory is not None else member
        for member, spec in zip(members, member_specs)
    ]

    if len(member_specs) == 1:
        spec = member_specs[0]
        return spec._replace(factory=resolved_factories[0])

    for member, spec in zip(members, member_specs):
        if spec.action is not None:
            raise ValueError(
                f"argument {name!r}: union member {member!r} needs a special "
                f"parsing action (a collection); argparse cannot switch actions "
                f"per value inside a multi-member union"
            )

    factories = []
    for member, f, spec in zip(members, resolved_factories, member_specs):
        if member is bool:
            f = _bool_from_text
        if spec.choices is not None:
            f = _choice_checked(f, spec.choices)
        factories.append(f)
    factories = tuple(factories)

    # For the error message only: the original annotation types, not the
    # resolved callables, whose repr is an unreadable ``<function ... at 0x...>``.
    _member_names = tuple(getattr(m, "__name__", repr(m)) for m in members)

    def factory(text: str, /, _factories=factories, _names=_member_names):
        for f in _factories:
            try:
                return f(text)
            except (TypeError, ValueError):
                pass
        raise _ConversionError(
            f"could not convert {text!r} using any of {', '.join(_names)}"
        )

    # Named after the members: a layered failure message's "expected <type>"
    # reads ``__name__`` (see `_field_type_desc`).
    factory.__name__ = " or ".join(_member_names)

    if bool in members:
        # A Union that also accepts bool must accept a native bool from env/config
        # (`_convert_non_str` reads this attribute).
        factory._duho_union_bool_ok_ = True  # type: ignore[attr-defined]

    return _scalar_spec(factory)


def _enum_spec(tp: type, name: str = "", enum_by: str = "name") -> _FieldSpec:
    """Spec for an ``enum.Enum`` annotation: a choose-by-name (or, with
    ``enum_by="value"``, by-value-text) factory plus a ``{member,...}``
    metavar built from the same canonical choices."""
    if enum_by == "value":
        factory = _enum_value_factory(tp, name)
        names = factory._duho_choices_  # type: ignore[attr-defined]
    else:
        factory = _enum_name_factory(tp)
        names = tuple(member.name for member in tp)
    metavar = "{" + ",".join(names) + "}"
    return _FieldSpec(factory, None, metavar, None, None, NOT_DEFINED, None)


def _dict_spec(key_ty, val_ty, name: str, enum_by: str = "name") -> _FieldSpec:
    """Spec for a ``dict[K, V]`` annotation: ``KEY=VALUE`` tokens merged via
    :class:`UpdateAction`. Bare ``dict`` == ``dict[str, str]``. Only ``str``
    keys are supported (a CLI token's key half is always text); rejected
    loudly at build time otherwise.
    """
    if key_ty is not str:
        raise ValueError(
            f"argument {name!r}: dict key type must be str, got {key_ty!r} "
            f"(a CLI KEY=VALUE token's key is always text)"
        )
    val_factory, _val_choices, _val_metavar = _element_spec(
        val_ty, name, "dict value", enum_by
    )
    return _FieldSpec(
        _KVFactory(name, val_factory), None, "KEY=VALUE", UpdateAction, None, {}, dict
    )


def _sequence_spec(
    collection: type, elem_ty, name: str, what: str, default, enum_by: str = "name"
) -> _FieldSpec:
    """Spec for a homogeneous ``list``/``set``/``frozenset``/variadic
    ``tuple[T, ...]`` annotation.

    ``list``/``set``/``frozenset``/``tuple`` differ only in their collection
    type, its empty default value, and the :class:`_CollectionAction`
    subclass bound to it -- the element factory/choices/metavar ladder and
    ``"*"`` nargs are identical across all four, so this is the one place
    that wiring is written.
    """
    factory, choices, metavar = _element_spec(elem_ty, name, what, enum_by)
    return _FieldSpec(
        factory,
        choices,
        metavar,
        _collection_action(collection),
        "*",
        default,
        collection,
    )


#: ``typing.TypeAliasType`` only exists on 3.12+ (PEP 695); ``None`` on the
#: 3.9 floor, where the attribute-probe branch below simply never matches.
_TypeAliasType = getattr(_ty, "TypeAliasType", None)


def _unwrap_type_alias(tp):
    """Unwrap a PEP 695 ``type X = ...`` alias (3.12+) or ``typing.NewType`` to
    the real type, looping through a chain.

    Neither works as argparse's ``type=``: a ``TypeAliasType`` is not callable,
    and a ``NewType`` is the identity function, so ``UserId("5")`` returns the
    string ``'5'``. The attribute probes are safe on every version.
    """
    while True:
        if _TypeAliasType is not None and isinstance(tp, _TypeAliasType):
            tp = tp.__value__
            continue
        supertype = getattr(tp, "__supertype__", None)
        if supertype is not None:
            tp = supertype
            continue
        return tp


def _member_spec(member, name: str, enum_by: str = "name") -> _FieldSpec:
    """:func:`_factory_for` for a Union member or a collection element, except
    that a type with its own ``_argbuilder_`` (the ``Argument`` protocol)
    supplies its factory, choices and metavar through that builder.
    """
    builder_hook = getattr(member, "_argbuilder_", None)
    if not (isinstance(member, type) and callable(builder_hook)):
        return _factory_for(member, name, enum_by)
    from ._introspect import ClsArgDeclaration

    built = builder_hook(
        name,
        ClsArgDeclaration(
            default=NOT_DEFINED, type=member, annotations=[], docstring="", exprs=[]
        ),
    )
    return _FieldSpec(
        built.type, built.choices, built.metavar, None, None, NOT_DEFINED, None
    )


def _element_spec(elem_ty, name: str, what: str, enum_by: str = "name") -> tuple:
    """Resolve a collection ELEMENT or dict VALUE type through the ladder a
    top-level field uses, so an enum element matches by name, a date parses ISO
    text, a bool parses strictly and a Literal checks membership. Raises
    ``ValueError`` naming the field when the element is itself a collection.

    Returns ``(factory, choices, metavar)``; any ``choices`` are enforced inside
    `factory` via :func:`_choice_checked`, since argparse's ``choices=`` would
    validate the whole collection, not each element.
    """
    spec = _member_spec(elem_ty, name, enum_by)
    if spec.action is not None or spec.collection is not None:
        raise ValueError(
            f"argument {name!r}: {what} element type {elem_ty!r} is itself "
            f"a collection; nested collections are not supported"
        )
    factory = spec.factory if spec.factory is not None else elem_ty
    if factory is bool:
        # Test the resolved factory, not `elem_ty`: an `Optional[bool]` element
        # resolves to the raw `bool` builtin and must reject "false" strictly too.
        factory = _bool_from_text
    if spec.choices is not None:
        factory = _choice_checked(factory, spec.choices)
    return factory, spec.choices, spec.metavar


def _factory_for(tp, name: str, enum_by: str = "name") -> _FieldSpec:
    """Resolve a single annotation type to its :class:`_FieldSpec`.

    The one dispatch ladder shared by the top-level field, every Union member
    and every collection element or dict value. A plain/custom type returns
    ``factory=None`` so the caller keeps its seeded factory (such as a
    ``duho.Argument.from_type`` one), unless `tp` was a type alias or NewType
    unwrapped to reach it: the seeded factory is then the un-unwrapped original
    and unusable.
    """
    original_tp = tp
    tp = _unwrap_type_alias(tp)
    origin = _ty.get_origin(tp)
    args = _ty.get_args(tp)

    if origin is _ty.Annotated:
        # A nested Annotated/Arg[...] Union member (e.g.
        # `Optional[Arg[int, NS(env=...)]]`) has unhashable metadata the
        # later lookups cannot use, so it is rejected here.
        raise ValueError(
            f"argument {name!r}: a nested Annotated/Arg[...] type {tp!r} is "
            f"not supported inside a Union; put the metadata on the OUTER "
            f"annotation instead (e.g. Arg[Optional[T], ...])"
        )

    if origin is _ty.Literal:
        return _literal_spec(args)

    if isinstance(tp, type) and issubclass(tp, _enum.Enum):
        return _enum_spec(tp, name, enum_by)

    if origin is list or tp is list:
        elem_ty = args[0] if args else str
        return _sequence_spec(list, elem_ty, name, "list", [], enum_by)

    if origin is set or tp is set:
        elem_ty = args[0] if args else str
        return _sequence_spec(set, elem_ty, name, "set", set(), enum_by)

    if origin is frozenset or tp is frozenset:
        elem_ty = args[0] if args else str
        return _sequence_spec(
            frozenset, elem_ty, name, "frozenset", frozenset(), enum_by
        )

    if origin is tuple or tp is tuple:
        # Only variadic homogeneous ``tuple[T, ...]`` and bare ``tuple``
        # (== ``tuple[str, ...]``) are supported.
        if args and not (len(args) == 2 and args[1] is Ellipsis):
            raise ValueError(
                f"argument {name!r}: fixed-length tuple annotation "
                f"{tp!r} is not supported; use tuple[T, ...] for a "
                f"variadic homogeneous tuple, or bare tuple"
            )
        elem_ty = args[0] if args else str
        return _sequence_spec(tuple, elem_ty, name, "tuple", (), enum_by)

    if origin is dict or tp is dict:
        # ``dict[K, V]`` -- ``KEY=VALUE`` tokens merged via ``UpdateAction``.
        # Bare ``dict`` == ``dict[str, str]``.
        key_ty = args[0] if args else str
        val_ty = args[1] if len(args) > 1 else str
        return _dict_spec(key_ty, val_ty, name, enum_by)

    try:
        is_isoformat = tp in _ISOFORMAT_FACTORIES
    except TypeError:
        # An unhashable annotation (e.g. Annotated metadata that doesn't
        # define __hash__) can't be a dict key -- it's simply not one of
        # these, not a crash.
        is_isoformat = False
    if is_isoformat:
        return _scalar_spec(_ISOFORMAT_FACTORIES[tp])

    if origin in _compat.UNION_ORIGINS:
        non_none = [a for a in args if a is not _NONETYPE]
        return _union_spec(non_none, name, enum_by)

    if origin is not None:
        # Some other subscripted generic (``Sequence[str]``, ``Iterable[str]``):
        # calling the raw typing alias per value would fail at parse time.
        raise ValueError(
            f"argument {name!r}: unsupported annotation {tp!r}; duho does "
            f"not know how to build a CLI factory for this generic type "
            f"(supported: list/set/frozenset/tuple[T, ...], dict[str, V], "
            f"Literal, Enum, Union/Optional, or a plain scalar type)"
        )

    if tp is _ty.Any or tp is object:
        # Nothing to convert to: the text as given.
        return _scalar_spec(str)

    if tp is _NONETYPE:
        raise ValueError(
            f"argument {name!r}: a None annotation has no CLI value to parse; "
            f"declare the field's type, e.g. Optional[str]"
        )

    if tp is not original_tp:
        # Unwrapped from a TypeAliasType/NewType: the caller's seeded factory is
        # the original alias and not callable, so return the unwrapped type.
        return _scalar_spec(tp)

    return _scalar_spec(None)


class UpdateAction(_argparse.Action):
    """Action that merges dict occurrences, replacing any layered default on
    the first CLI occurrence -- the same "CLI wins" semantics
    `_CollectionAction` gives list/set/tuple fields.
    """

    def __call__(
        self,
        parser: _argparse.ArgumentParser,
        namespace: _argparse.Namespace,
        values: object,
        option_string: _ty.Optional[str] = None,
    ) -> None:
        sidecar = "_duho_dict_seen_" + self.dest
        if not getattr(namespace, sidecar, False):
            # First CLI occurrence of this parse: start empty so a layered
            # default is replaced, not merged onto.
            items: dict = {}
            setattr(namespace, sidecar, True)
        else:
            items = getattr(namespace, self.dest, None)
            items = dict(items) if items else {}
        if isinstance(values, (list, tuple)) and all(
            isinstance(v, _ty.Mapping) for v in values
        ):
            # ``NS(nargs="*")`` on a dict field gives a LIST of one-pair dicts
            # (each already converted); merge them in order, since ``dict.update``
            # on the list raises. Any other update()-compatible value falls through.
            for one in values:
                items.update(one)
        else:
            # A ``None`` starting value (an explicit ``= None`` default) is
            # already normalized to ``{}`` above; a single dict occurrence
            # merges directly.
            items.update(values or {})
        setattr(namespace, self.dest, items)
