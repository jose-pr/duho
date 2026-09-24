import argparse as _argparse
import collections as _collections
import copy as _copy
import dataclasses as _dataclasses
import datetime as _datetime
import enum as _enum
import logging as _logging_module
import os as _os
import pathlib as _pathlib
import sys as _sys
import typing as _ty

from . import _compat as _compat
from . import _introspect as _inspect
from . import logging as _duho_logging

_duho_module_logger = _logging_module.getLogger(__name__)

NOT_DEFINED = _inspect.NOT_DEFINED
_NONETYPE = type(None)

if _ty.TYPE_CHECKING:
    from typing_extensions import Self as _Self  # type: ignore

_type = type

_T = _ty.TypeVar("_T")

Factory = _ty.Callable[[str], _T]

NS = _argparse.Namespace
Arg = _ty.Annotated


class _AutoVersion:
    """Sentinel for ``_version_ = duho.AUTO``: resolve via importlib.metadata."""

    def __repr__(self) -> str:
        return "duho.AUTO"


AUTO = _AutoVersion()


class _MetaUnset:
    """Sentinel for a :class:`Meta` field left unset (never merged)."""

    def __repr__(self) -> str:
        return "duho.Meta.UNSET"


_META_UNSET = _MetaUnset()


@_dataclasses.dataclass
class Meta:
    """Typed, typo-safe alternative to ``NS(...)`` for field metadata (F5).

    ``NS(...)`` is an untyped ``argparse.Namespace``: a misspelled key
    (``NS(hlep="oops")``) is silently dropped. ``Meta`` declares the known
    metadata fields as a dataclass, so an unknown keyword is a ``TypeError`` at
    class-definition time -- the whole point. Only the fields you set are merged
    (each defaults to a private sentinel); everything ``NS`` accepts, ``Meta``
    accepts, and ``NS`` keeps working forever.

    Use it exactly where ``NS`` goes::

        level: Arg[int, Meta(help="verbosity", env="LEVEL")] = 0
        ("--level",)

    Recommended over ``NS`` precisely because a typo fails loud instead of
    vanishing. The ``kwargs`` field is the same raw ``add_argument`` escape hatch
    ``NS(kwargs=...)`` provides.
    """

    help: "_ty.Any" = _META_UNSET
    env: "_ty.Any" = _META_UNSET
    conflicts: "_ty.Any" = _META_UNSET
    conflicts_required: "_ty.Any" = _META_UNSET
    group: "_ty.Any" = _META_UNSET
    action: "_ty.Any" = _META_UNSET
    nargs: "_ty.Any" = _META_UNSET
    const: "_ty.Any" = _META_UNSET
    choices: "_ty.Any" = _META_UNSET
    metavar: "_ty.Any" = _META_UNSET
    required: "_ty.Any" = _META_UNSET
    type: "_ty.Any" = _META_UNSET
    version: "_ty.Any" = _META_UNSET
    dest: "_ty.Any" = _META_UNSET
    kwargs: "_ty.Any" = _META_UNSET

    def _duho_options_(self) -> "dict[str, object]":
        """The explicitly-set metadata as a plain dict (unset fields omitted).

        Consumed by ``Args._getargs_`` in place of ``vars(self)`` so a
        sentinel-valued (never-set) field never overrides a type-derived kwarg.
        """
        return {k: v for k, v in vars(self).items() if v is not _META_UNSET}


class _ConversionError(_argparse.ArgumentTypeError, ValueError):
    """Raised by duho's own text factories so argparse shows OUR message.

    argparse's ``_get_value`` only preserves a type function's own message for
    ``ArgumentTypeError``; a plain ``ValueError``/``TypeError`` is replaced
    with a generic ``invalid <type> value: ...`` that discards whatever
    detail the factory raised -- so a crafted "choose from ..."/"expected
    KEY=VALUE" message never reached the user (A018). Subclassing
    ``ValueError`` too means every existing ``except (TypeError, ValueError)``
    catch (the Union/Literal try-loops, the env/config layers) keeps working
    unchanged.
    """


def _bool_from_text(text, /):
    """Strict CLI-text-to-bool factory (A002).

    Plain ``bool`` is never a valid CLI/element/value factory: ``bool(text)``
    is true for almost any non-empty string, so ``--flag False`` silently
    became ``True``. This shares the same shared token table
    (:data:`_compat.BOOL_TRUE`/:data:`_compat.BOOL_FALSE`) the layered
    (env/config) converter already used, closing the gap where the CLI and
    env/config disagreed on the very same field. A real ``bool`` passes
    through unchanged (a native TOML/JSON value, or a value already
    converted upstream); any other non-string is rejected the same as
    unrecognized text, rather than crashing on ``.strip()``.
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


def _choice_checked(factory: "Factory", choices) -> "Factory":
    """Wrap `factory` so its result must be one of `choices` (A004).

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


def _enum_name_factory(enum_cls: type) -> "Factory":
    """Build a factory that resolves CLI text to an enum member by NAME.

    Validates against ``enum_cls.__members__`` (A053) rather than iterating
    the enum: iteration skips ALIASES (a second name for the same value) and,
    since Python 3.11, skips multi-bit ``Flag`` composite members too, so a
    declaration that worked on the 3.9 floor could reject a composite name on
    the ceiling. ``__members__`` includes both on every supported version.
    The "choose from" text still lists only the canonical (non-alias) names
    from iteration, matching the metavar built alongside this factory.

    Raises :class:`_ConversionError` (a ``ValueError`` subclass, A018) so
    argparse shows the crafted "choose from ..." message instead of its own
    generic "invalid <x> value", and so callers that catch
    ``(TypeError, ValueError)`` (e.g. the Union-branch try-loop) can still
    treat a non-matching name as "this sub-factory rejects text" and fall
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
    return _factory


class _CollectionAction(_argparse.Action):
    """Extend-and-coerce action for ``list``/``set``/``tuple`` collection
    fields.

    argparse's built-in ``extend`` action only extends a *list* and starts
    from whatever is already on the namespace (a layered default), so a CLI
    occurrence merges onto it instead of replacing it. This action instead
    starts its sidecar EMPTY on the first call of a parse, so the first CLI
    occurrence always REPLACES a class/env/config/instance default -- the
    same "CLI wins" semantics for every collection kind (A005) -- and
    further occurrences accumulate onto that (repeated flags still add up:
    ``--x a --x b`` -> both). It gathers elements in insertion order across
    both invocation forms -- repeated flags (``--x a --x b``) and
    space-separated (``--x a b``) -- then stores the final field value
    coerced to the target collection type.

    The running elements are kept in insertion order on a private sidecar
    attribute (``_duho_items_<dest>``) so a ``tuple`` field's order is stable
    regardless of how many times the flag appears; ``set`` dedups at coercion.
    The declared collection type is bound at build time as ``_collection_``
    (``list``, ``set``, ``tuple``, or ``frozenset``).
    """

    #: Target collection type; bound at construction.
    _collection_: type = tuple

    def __call__(self, parser, namespace, values, option_string=None):
        if values is self.default:
            # A zero-token variable-arity (nargs="*") POSITIONAL: argparse
            # hands the action's own default object back as `values` when no
            # tokens were consumed (A006). Coerce a FRESH collection from it
            # instead of treating it as a user-supplied value -- otherwise a
            # `set` default crashes (`set([<the default set>])`, unhashable)
            # and a `list` default gets doubled. The fresh coercion also
            # means the returned instance never aliases the action's default
            # object, so a later mutation can't leak into a future parse.
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


def _collection_action(collection: type) -> "_type[_argparse.Action]":
    """Build a ``_CollectionAction`` subclass bound to a target collection."""

    class _BoundCollectionAction(_CollectionAction):
        _collection_ = collection

    return _BoundCollectionAction


def _split_kv(text: str, name: str) -> "tuple[str, str]":
    """Split a ``KEY=VALUE`` token on its first ``=``.

    Raises :class:`_ConversionError` naming the field when no ``=`` is
    present (A018), so a ``dict`` field's ``--opt noequals`` shows this
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
    """CLI-text factory for a ``dict[str, V]`` field (F1).

    Converts one ``KEY=VALUE`` token into a one-pair dict, applying the value
    factory ``V`` to the value half; :class:`UpdateAction` merges successive
    pairs. The value factory is exposed as ``value_factory`` so the env/config
    layer (:meth:`ArgumentBuilder.convert_layered`) can convert a TOML *table*'s
    values the same way a CLI ``KEY=VALUE`` token would.
    """

    def __init__(self, name: str, value_factory: "Factory"):
        self.name = name
        self.value_factory = value_factory

    def __call__(self, text: str) -> dict:
        key, value = _split_kv(text, self.name)
        return {key: self.value_factory(value)}


def _isoformat_factory(cls: type) -> "Factory":
    """Build the ``fromisoformat`` factory for a date/datetime/time `cls`.

    Before Python 3.11, ``fromisoformat`` only accepts its OWN ``isoformat()``
    output: no trailing ``Z`` (RFC 3339's UTC marker, and the form most tools
    emit) and no basic ``YYYYMMDD`` format. duho.mcp advertises
    ``format: date-time`` (RFC 3339) on every version regardless (A043), so a
    schema-valid MCP call could fail on the 3.9 floor. On <3.11 this rewrites
    a trailing ``Z``/``z`` to ``+00:00`` before delegating; 3.11+ uses
    ``fromisoformat`` directly, which already accepts ``Z`` natively. Basic
    (no-dash) formats stay unsupported on every version -- out of scope here.
    """
    if _sys.version_info >= (3, 11):
        return cls.fromisoformat

    def _factory(text: str, /, _cls=cls):
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        return _cls.fromisoformat(text)

    _factory.__name__ = cls.__name__
    return _factory


#: Stdlib types whose constructor does NOT accept an ISO string; each maps to
#: its ``fromisoformat``-based factory so a CLI/date field converts cleanly
#: (C15), consistently across the supported Python range (A043).
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


def _scalar_spec(factory=None) -> "_FieldSpec":
    return _FieldSpec(factory, None, None, None, None, NOT_DEFINED, None)


def _literal_spec(args: tuple) -> "_FieldSpec":
    """Spec for a ``Literal[...]`` annotation: choices + a round-trip factory."""
    literal_types: list = []
    for lit in args:
        lit_ty = type(lit)
        if lit_ty not in literal_types:
            literal_types.append(lit_ty)
    if len(literal_types) == 1:
        lit_ty = literal_types[0]
        # bool is never a valid CLI text factory on its own: bool(text) is
        # true for almost any non-empty string, so Literal[True, False] with
        # "--flag False" silently became True (A002).
        factory: "Factory" = _bool_from_text if lit_ty is bool else lit_ty
    else:
        # Mixed-type Literal: try each declared literal's own type, but only
        # accept a conversion that round-trips to one of the declared values (a
        # naive "first type that doesn't raise" would let str('1') shadow int(1),
        # and a naive bool(text) would let True shadow every other member).
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

    return _FieldSpec(factory, tuple(args), None, None, None, NOT_DEFINED, None)


def _union_spec(members: "list", name: str) -> "_FieldSpec":
    """Spec for a Union of ``members`` (``None`` already stripped).

    Each member is resolved through :func:`_factory_for` so a member like
    ``list[int]`` or ``Literal[...]`` gets its full spec (C6). A single remaining
    member (an ``Optional[T]``) adopts T's ENTIRE spec -- element conversion,
    action, choices, default (this is also why a bare/``Optional`` ``bool``
    keeps `store_true`/`BooleanOptionalAction`: its single-member spec's
    factory stays the raw ``bool`` builtin, untouched below). A multi-member
    union composes the member factories in declaration order (the
    enum-by-name rule preserved), but rejects any member that needs a special
    ``action`` (a collection): argparse cannot switch actions per value within
    one option. Within that multi-member composition, a raw ``bool`` member
    is routed through the strict :func:`_bool_from_text` (A002) and any
    member carrying ``choices`` (e.g. a Literal) is membership-checked before
    the try-loop can silently accept a value only because a LATER member's
    bare type conversion happens not to raise (A004) -- a union field never
    gets argparse's own ``choices=`` kwarg, so this is the only enforcement.
    """
    member_specs = [_factory_for(m, name) for m in members]
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

    def factory(text: str, /, _factories=factories):
        for f in _factories:
            try:
                return f(text)
            except (TypeError, ValueError):
                pass
        raise _ConversionError(f"could not convert {text!r} using any of {_factories}")

    return _scalar_spec(factory)


#: ``typing.TypeAliasType`` only exists on 3.12+ (PEP 695); ``None`` on the
#: 3.9 floor, where the attribute-probe branch below simply never matches.
_TypeAliasType = getattr(_ty, "TypeAliasType", None)


def _unwrap_type_alias(tp):
    """Unwrap a PEP 695 ``type X = ...`` alias (3.12+) or a ``typing.NewType``
    down to the real type it describes, looping so a chain of aliases
    resolves fully (A029).

    Neither is safe to hand straight to argparse's ``type=``: a
    ``TypeAliasType`` instance is not callable at all (``type Port = int``
    crashes every parser build with "Port is not callable"), and a
    ``NewType`` IS callable but is the identity function at runtime, so
    ``UserId("5")`` silently returns the string ``'5'`` where the annotation
    promises an ``int``. Both attribute probes are safe on every supported
    version -- neither exists on a plain type.
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


def _element_spec(elem_ty, name: str, what: str) -> "tuple":
    """Resolve a collection ELEMENT or dict VALUE type through the same
    ladder a top-level field uses (A003), so an enum element matches by
    name, a date element parses ISO text, a bool element parses strictly
    (A002), and a Literal element carries its own membership check. Raises a
    build-time ValueError naming the field when the element type is itself a
    collection -- argparse cannot switch actions/nargs per element (mirrors
    the check `_union_spec` makes for a collection Union member).

    Returns ``(factory, choices, metavar)``; ``choices`` is already enforced
    INSIDE `factory` via :func:`_choice_checked` when the element spec
    carried any (e.g. a Literal element) -- element/value fields never get
    argparse's own ``choices=`` kwarg (it validates the whole collection's
    converted value, not each element).
    """
    spec = _factory_for(elem_ty, name)
    if spec.action is not None or spec.collection is not None:
        raise ValueError(
            f"argument {name!r}: {what} element type {elem_ty!r} is itself "
            f"a collection; nested collections are not supported"
        )
    factory = spec.factory if spec.factory is not None else elem_ty
    if elem_ty is bool:
        factory = _bool_from_text
    if spec.choices is not None:
        factory = _choice_checked(factory, spec.choices)
    return factory, spec.choices, spec.metavar


def _factory_for(tp, name: str) -> "_FieldSpec":
    """Resolve a single annotation type to its :class:`_FieldSpec`.

    The one dispatch ladder shared by the top-level field, every Union
    member, and every collection element/dict value (C6, A003). Ordering
    matches the historical branch order (Literal, Enum, list, set, frozenset,
    tuple, dict, iso-date, Union, fallthrough). A plain/custom type returns
    ``factory=None`` so the caller keeps whatever factory it seeded (e.g. a
    ``duho.Argument.from_type`` custom factory) -- EXCEPT when `tp` was
    itself a type alias/NewType that had to be unwrapped to reach that plain
    type, since the caller's seeded factory is the un-unwrapped original,
    which is not usable as-is (A029).
    """
    original_tp = tp
    tp = _unwrap_type_alias(tp)
    origin = _ty.get_origin(tp)
    args = _ty.get_args(tp)

    if origin is _ty.Annotated:
        # A nested Annotated/Arg[...] Union member (e.g.
        # `Optional[Arg[int, NS(env=...)]]`) previously crashed later at the
        # unhashable-metadata isoformat lookup, or silently dropped its
        # metadata. Reject it loudly instead (A029).
        raise ValueError(
            f"argument {name!r}: a nested Annotated/Arg[...] type {tp!r} is "
            f"not supported inside a Union; put the metadata on the OUTER "
            f"annotation instead (e.g. Arg[Optional[T], ...])"
        )

    if origin is _ty.Literal:
        return _literal_spec(args)

    if isinstance(tp, type) and issubclass(tp, _enum.Enum):
        names = tuple(member.name for member in tp)
        metavar = "{" + ",".join(names) + "}"
        return _FieldSpec(
            _enum_name_factory(tp), None, metavar, None, None, NOT_DEFINED, None
        )

    if origin is list or tp is list:
        elem_ty = args[0] if args else str
        factory, choices, metavar = _element_spec(elem_ty, name, "list")
        return _FieldSpec(
            factory, choices, metavar, _collection_action(list), "*", [], list
        )

    if origin is set or tp is set:
        elem_ty = args[0] if args else str
        factory, choices, metavar = _element_spec(elem_ty, name, "set")
        return _FieldSpec(
            factory, choices, metavar, _collection_action(set), "*", set(), set
        )

    if origin is frozenset or tp is frozenset:
        elem_ty = args[0] if args else str
        factory, choices, metavar = _element_spec(elem_ty, name, "frozenset")
        return _FieldSpec(
            factory,
            choices,
            metavar,
            _collection_action(frozenset),
            "*",
            frozenset(),
            frozenset,
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
        factory, choices, metavar = _element_spec(elem_ty, name, "tuple")
        return _FieldSpec(
            factory, choices, metavar, _collection_action(tuple), "*", (), tuple
        )

    if origin is dict or tp is dict:
        # ``dict[K, V]`` -- ``KEY=VALUE`` tokens merged via ``UpdateAction``.
        # Bare ``dict`` == ``dict[str, str]``. Only ``str`` keys are supported
        # (a CLI token's key half is always text); reject anything else loudly
        # at build time.
        key_ty = args[0] if args else str
        val_ty = args[1] if len(args) > 1 else str
        if key_ty is not str:
            raise ValueError(
                f"argument {name!r}: dict key type must be str, got {key_ty!r} "
                f"(a CLI KEY=VALUE token's key is always text)"
            )
        val_factory, _val_choices, _val_metavar = _element_spec(
            val_ty, name, "dict value"
        )
        return _FieldSpec(
            _KVFactory(name, val_factory),
            None,
            "KEY=VALUE",
            UpdateAction,
            None,
            {},
            dict,
        )

    try:
        is_isoformat = tp in _ISOFORMAT_FACTORIES
    except TypeError:
        # An unhashable annotation (e.g. Annotated metadata that doesn't
        # define __hash__) can't be a dict key -- it's simply not one of
        # these, not a crash (A029).
        is_isoformat = False
    if is_isoformat:
        return _scalar_spec(_ISOFORMAT_FACTORIES[tp])

    if origin in _compat.UNION_ORIGINS:
        non_none = [a for a in args if a is not _NONETYPE]
        return _union_spec(non_none, name)

    if origin is not None:
        # Some other subscripted generic this ladder doesn't know how to
        # build a factory for (frozenset is handled above; this catches
        # things like Sequence[str]/Iterable[str], which previously fell
        # through to calling the raw typing alias on every value -- failing
        # per-value at PARSE time instead of once at build time, or (for a
        # bare `frozenset`) silently splitting text into characters) (A029).
        raise ValueError(
            f"argument {name!r}: unsupported annotation {tp!r}; duho does "
            f"not know how to build a CLI factory for this generic type "
            f"(supported: list/set/frozenset/tuple[T, ...], dict[str, V], "
            f"Literal, Enum, Union/Optional, or a plain scalar type)"
        )

    if tp is not original_tp:
        # `tp` was unwrapped from a TypeAliasType/NewType above and fell
        # through to here as a plain scalar type -- `original_tp` (the
        # caller's seeded factory) is not itself usable, so hand back the
        # real, callable, unwrapped type instead of `None` (A029).
        return _scalar_spec(tp)

    return _scalar_spec(None)


def _resolve_version(cls) -> "str | None":
    """Resolve a class's effective ``--version`` string, or None to skip it.

    ``_version_`` may be unset/None (no --version), an explicit str (used
    as-is), or the ``AUTO`` sentinel (resolved via importlib.metadata using
    ``_distribution_`` or the class's top-level import package). Never
    raises -- any resolution failure is logged at debug level and treated
    as "no version available".

    When ``_version_`` is unset/None, a class-level ``__version__`` string is
    used as a fallback (so an app that already carries the conventional
    ``__version__`` gets ``--version`` for free). ``_version_`` always wins when
    both are set; the ``__version__`` fallback accepts only a plain ``str``
    (not the ``AUTO`` sentinel).
    """
    raw = getattr(cls, "_version_", None)
    if raw is None:
        fallback = getattr(cls, "__version__", None)
        return fallback if isinstance(fallback, str) else None
    if isinstance(raw, str):
        return raw
    if raw is AUTO:
        # Imported lazily (not at module top) so a plain `import duho` never pays
        # importlib.metadata's ~30 ms cost -- only a class that actually opts into
        # `_version_ = duho.AUTO` triggers the load, and only at parser-build time
        # (P1). The exception classes are referenced only inside this branch.
        import importlib.metadata as _importlib_metadata

        dist = getattr(cls, "_distribution_", None) or cls.__module__.split(".")[0]
        try:
            return _importlib_metadata.version(dist)
        except _importlib_metadata.PackageNotFoundError:
            _duho_module_logger.debug(
                "duho.AUTO: distribution %r not found for %s; skipping --version",
                dist,
                cls,
            )
            return None
        except Exception:
            _duho_module_logger.debug(
                "duho.AUTO: failed to resolve version for %s (distribution %r)",
                cls,
                dist,
                exc_info=True,
            )
            return None
    return None


class _PrintCompletionAction(_argparse.Action):
    """argparse Action for --print-completion: emits a shell completion
    script for the *root* parser tree and exits 0, mirroring how the
    stdlib's own action="version" short-circuits before dispatch.

    ``root_parser`` is captured at injection time (the top-level parser
    built by this call to _parser_/_initparser_) rather than re-derived
    from ``parser`` at call time, since a subcommand's own parser only
    sees its own subtree, not the whole app.
    """

    def __init__(self, option_strings, dest, root_parser=None, **kwargs):
        kwargs.setdefault("nargs", None)
        kwargs.setdefault("default", _argparse.SUPPRESS)
        super().__init__(option_strings, dest, **kwargs)
        self.root_parser = root_parser

    def __call__(self, parser, namespace, values, option_string=None):
        from . import completion as _completion

        emitter = getattr(_completion, values)
        root = self.root_parser if self.root_parser is not None else parser
        parser._print_message(emitter(root), _sys.stdout)
        parser.exit()


class _AgentHelpAction(_argparse._HelpAction):
    """``-h``/``--help`` action that emits agent help when the env trigger is set.

    Installed by :meth:`Args._initparser_` via a per-instance ``__class__`` swap
    of argparse's own ``_HelpAction`` -- the same blessed idiom ``parsers.py``
    uses (``_NoOpHelpAction``/``_RelaxedSubParsersAction``): argparse's classes
    are never mutated, so the surgery stays thread-safe and reentrant. When the
    trigger env var (``_duho_agent_env`` or the ``AGENT_HELP`` default) is set
    truthy, it prints the machine-readable agent document for THIS parser and
    exits 0; otherwise it defers to the normal human ``_HelpAction``.
    """

    #: The duho class behind this parser (for version/exit-code/example lookup);
    #: the trigger env-var name (``None`` -> the ``AGENT_HELP`` default). Both are
    #: set as instance attrs right after the ``__class__`` swap.
    _duho_agent_cls = None
    _duho_agent_env = None

    def __call__(self, parser, namespace, values, option_string=None):
        from . import agenthelp as _agenthelp

        if _agenthelp.agent_help_requested(self._duho_agent_env):
            spec = _agenthelp.describe_parser(
                parser, root=True, root_cls=self._duho_agent_cls
            )
            parser._print_message(_agenthelp.render(spec), _sys.stdout)
            parser.exit()
        super().__call__(parser, namespace, values, option_string)


class _AgentHelpFlagAction(_argparse.Action):
    """The opt-in ``--help-agents`` flag: always emit agent help, then exit 0.

    Mirrors :class:`_PrintCompletionAction`: ``root_parser`` is captured at
    injection time (in ``_initparser_``, before subparsers are attached) but the
    same parser object carries the full tree by the time the flag fires at parse
    time, so the emitted document covers every subcommand.
    """

    def __init__(self, option_strings, dest, root_parser=None, root_cls=None, **kwargs):
        kwargs.setdefault("nargs", 0)
        kwargs.setdefault("default", _argparse.SUPPRESS)
        super().__init__(option_strings, dest, **kwargs)
        self.root_parser = root_parser
        self.root_cls = root_cls

    def __call__(self, parser, namespace, values, option_string=None):
        from . import agenthelp as _agenthelp

        root = self.root_parser if self.root_parser is not None else parser
        spec = _agenthelp.describe_parser(root, root=True, root_cls=self.root_cls)
        parser._print_message(_agenthelp.render(spec), _sys.stdout)
        parser.exit()


def _install_agent_help(parser, cls, is_subcommand):
    """Wire up both agent-help triggers on a freshly built parser.

    1. Stash ``cls`` on the parser as ``_duho_cls_`` so the emitter can enrich
       each command with duho's field metadata (env bindings, conflicts, declared
       types) -- see :mod:`duho.agenthelp`.
    2. Swap every ``_HelpAction`` on this parser to :class:`_AgentHelpAction` so
       ``--help`` becomes agent-aware (env-triggered). Always on: it only changes
       ``--help`` behavior when the trigger env var is deliberately set, so
       normal human help is unchanged.
    3. On the top-level parser only, when ``_agent_help_ = True``, add the opt-in
       ``--help-agents`` flag (guarded against a duplicate dest).
    """
    parser._duho_cls_ = cls  # type: ignore[attr-defined]

    env_name = getattr(cls, "_agent_help_env_", None)
    for action in parser._actions:
        if isinstance(action, _argparse._HelpAction) and not isinstance(
            action, _AgentHelpAction
        ):
            action.__class__ = _AgentHelpAction
            action._duho_agent_cls = cls  # type: ignore[attr-defined]
            action._duho_agent_env = env_name  # type: ignore[attr-defined]

    if not is_subcommand and getattr(cls, "_agent_help_", False):
        existing_dests = {action.dest for action in parser._actions}
        if "help_agents" not in existing_dests:
            parser.add_argument(
                "--help-agents",
                dest="help_agents",
                action=_AgentHelpFlagAction,
                root_parser=parser,
                root_cls=cls,
                help="Show a detailed machine-readable description of this CLI "
                "(for AI agents) and exit.",
            )


def _resolve_env_defaults(cls) -> "dict[str, object]":
    """Build {field_name: converted_value} for fields whose NS(env=...) var is set.

    Conversion runs the field's `type` factory (same one used for CLI text) so
    a bad env value raises the same clear error argparse would give, and so a
    non-str field (e.g. `port: int`) never leaks a raw str into set_defaults
    (which bypasses argparse's own type= conversion).
    """
    resolved: "dict[str, object]" = {}
    for builder in cls._getargs_():
        if not builder.env:
            continue
        raw = _os.environ.get(builder.env)
        if raw is None:
            continue
        try:
            resolved[builder.name] = builder.convert_layered(raw, source="env")
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"environment variable {builder.env!r} for field {builder.name!r}: "
                f"invalid value {raw!r} ({exc})"
            ) from exc
    return resolved


def _load_config(
    path: "str | _pathlib.Path",
    loader: "_ty.Callable[[_pathlib.Path], dict] | None" = None,
) -> dict:
    """Read a config file into a plain dict, dispatching on shape (F7).

    Resolution order:

    * **``loader`` hook** (``Cli._config_loader_``, if declared) -- when given, it
      is called with the expanded ``Path`` and its result used verbatim. This is
      the zero-dependency escape hatch: a user who wants YAML plugs their own
      ``yaml.safe_load`` here without duho ever importing (or depending on) it.
    * **``.json`` suffix** -- parsed with the stdlib ``json`` module (imported
      lazily, so a non-JSON config never pays its import cost). A parse error is
      re-raised as a ``ValueError`` naming the file.
    * **``.toml`` / anything else** -- parsed with stdlib ``tomllib`` (3.11+) or
      the third-party ``tomli`` IFF installed. Neither is a hard dependency (duho
      stays zero-runtime-deps); if neither is importable a clear ``RuntimeError``
      tells the user to ``pip install tomli``.

    Both JSON and TOML yield the same nested-dict shape (top-level keys -> root
    fields; a nested table/object named for a subcommand -> that subcommand's
    fields), so the layering walk is format-agnostic.
    """
    p = _pathlib.Path(path).expanduser()

    if loader is not None:
        return loader(p)

    if p.suffix.lower() == ".json":
        import json as _json  # lazy: only a JSON config pays json's import cost

        with p.open("rb") as f:
            try:
                return _json.load(f)
            except ValueError as exc:  # JSONDecodeError is a ValueError subclass
                raise ValueError(
                    f"duho: invalid JSON in config file {_os.fspath(p)}: {exc}"
                ) from exc

    try:
        import tomllib as _toml  # type: ignore[import-not-found]
    except ImportError:
        try:
            import tomli as _toml  # type: ignore[import-not-found,no-redef]
        except ImportError:
            raise RuntimeError(
                "duho: reading a config file requires a TOML backend. "
                "Python 3.11+ has one built in (tomllib); on earlier "
                "versions, install the optional 'tomli' package "
                "(e.g. `pip install tomli` or `pip install duho[config]`)."
            ) from None

    with p.open("rb") as f:
        return _toml.load(f)


def _config_values_for(cls, config: dict) -> "dict[str, object]":
    """Extract + convert this class's field values from a loaded config dict.

    Top-level keys map to the root command's fields. When `cls` is a
    subcommand (has `_parsername_`), its own table `[<_parsername_>]` is
    consulted instead of the top-level keys -- callers pass the right slice
    of the config for the class being resolved. Unknown keys are ignored
    (logged at debug) rather than erroring, for forward-compat.
    """
    resolved: "dict[str, object]" = {}
    field_builders = {b.name: b for b in cls._getargs_()}
    for key, raw in config.items():
        builder = field_builders.get(key)
        if builder is None:
            _duho_module_logger.debug(
                "duho: ignoring unknown config key %r for %s", key, cls
            )
            continue
        try:
            resolved[key] = builder.convert_layered(raw, source="config")
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"config value for field {key!r} on {cls.__name__}: "
                f"invalid value {raw!r} ({exc})"
            ) from exc
    return resolved


def _apply_default_layers_one(
    parser: "_argparse.ArgumentParser", cls, config_table: dict
):
    """Resolve + apply the env/config/class-default layers onto a single parser.

    Merge order per field: class default (already argparse's default) ->
    overlay config value if present (from `config_table`, this class's own
    slice of the loaded TOML) -> overlay env value if present -> then
    `parser.set_defaults(**merged)`. CLI parsing overlays on top of that
    naturally (argparse's own behavior), yielding the locked precedence
    contract: CLI > env > config > class default. A `set_defaults` value
    also un-requires the corresponding action for free -- no manual
    `required=` surgery needed.

    Records `_duho_value_sources_` on the parser: the source ("config" or
    "env") for every field touched by a non-default layer, so
    `duho.value_sources()` can report provenance after parsing.
    """
    sources: "dict[str, str]" = {}
    merged: "dict[str, object]" = {}

    if config_table:
        for name, value in _config_values_for(cls, config_table).items():
            merged[name] = value
            sources[name] = "config"

    for name, value in _resolve_env_defaults(cls).items():
        merged[name] = value
        sources[name] = "env"

    # Drop any dest whose action is SUPPRESS-suppressed on this parser: that dest
    # is a root field inherited by a child parser, suppressed precisely so the
    # value the root already parsed (from an option given BEFORE the subcommand)
    # survives. Re-installing a default here via set_defaults would overwrite the
    # SUPPRESS marker and clobber that parsed value (C3). The root parser's own
    # layering already applies the env/config value to the real (root) field.
    if merged:
        actions_by_dest = {action.dest: action for action in parser._actions}
        for name in list(merged):
            action = actions_by_dest.get(name)
            if action is not None and action.default is _argparse.SUPPRESS:
                del merged[name]
                sources.pop(name, None)

    if merged:
        parser.set_defaults(**merged)
        for action in parser._actions:
            if action.dest in merged:
                action.required = False

    parser._duho_value_sources_ = sources  # type: ignore[attr-defined]
    parser._duho_merged_defaults_ = merged  # type: ignore[attr-defined]


def _apply_default_layers(
    parser: "_argparse.ArgumentParser", cls, config: "str | _pathlib.Path | None"
):
    """Resolve config (once) + apply env/config/class-default layers across
    the whole parser tree rooted at `parser`/`cls`.

    `config` (explicit kwarg) overrides `cls._config_` (sandwich-named class
    attr). Top-level TOML keys map to the root command's fields; a
    `[<subcommand-name>]` table (subcommand name = its `_parsername_`) maps
    to that subcommand's fields. Recurses into `_subcommands_` so nested
    command trees get their own table looked up by name, applied to their
    own (sub)parser. Shared by both `duho.parse` and `duho.main`.
    """
    config_path = config if config is not None else getattr(cls, "_config_", None)
    loader = getattr(cls, "_config_loader_", None)
    raw_config: dict = (
        _load_config(config_path, loader) if config_path is not None else {}
    )

    def _walk(parser_, cls_, table: dict):
        _apply_default_layers_one(parser_, cls_, table)
        # Merge each child parser's provenance up into the ROOT parser (keyed by
        # dest, last-wins so the deepest selection lands last -- matching dispatch
        # semantics). `value_sources` reads the root parser via
        # `_duho_last_parser_`, so without this a config value on a SUBcommand
        # field is invisible there and gets mislabeled "cli" (C14).
        if parser_ is not parser:
            parser._duho_value_sources_.update(  # type: ignore[attr-defined]
                parser_._duho_value_sources_  # type: ignore[attr-defined]
            )
            parser._duho_merged_defaults_.update(  # type: ignore[attr-defined]
                parser_._duho_merged_defaults_  # type: ignore[attr-defined]
            )
        subcommands = getattr(cls_, "_subcommands_", None)
        if not subcommands:
            return
        # argparse stores each subcommand's parser on the _SubParsersAction
        # registered on parser_; find it and look up by the subcommand's
        # registered name (its _parsername_, set during _parser_()).
        subparsers_action = next(
            (a for a in parser_._actions if isinstance(a, _argparse._SubParsersAction)),
            None,
        )
        if subparsers_action is None:
            return
        choices = subparsers_action.choices or {}
        for sub in subcommands:
            sub_name = getattr(sub, "_parsername_", None) or sub.__name__
            sub_parser = choices.get(sub_name)
            if sub_parser is None:
                continue
            sub_table = table.get(sub_name)
            sub_table = sub_table if isinstance(sub_table, dict) else {}
            _walk(sub_parser, sub, sub_table)

    _walk(parser, cls, raw_config)


class ArgumentMeta(_ty._ProtocolMeta):

    def __instancecheck__(self, instance) -> bool:
        builder_factory = getattr(instance, "_argbuilder_", None)
        return callable(builder_factory)


@_ty.runtime_checkable
class Argument(_ty.Protocol, metaclass=ArgumentMeta):

    @classmethod
    def _argbuilder_(
        cls,
        name: str,
        decl: _inspect.ClsArgDeclaration,
        factory: "Factory | None" = None,
    ):
        help = decl.docstring or ""
        flags_expr = next(
            filter(lambda x: isinstance(x, (list, tuple, set)), decl.exprs),
            None,
        )
        if isinstance(flags_expr, set):
            # A set has no defined iteration order, so `flags[0]` (positional
            # detection) is nondeterministic and previously crashed. Reject it
            # with a clear build-time error naming the field (M15).
            raise ValueError(
                f"argument {name!r}: flags must be given as a list or tuple, "
                f"not a set {flags_expr!r} (a set has no guaranteed order)"
            )
        flags = (
            flags_expr if flags_expr is not None else ("--" + name.replace("_", "-"),)
        )
        required = None
        choices = None
        metavar = None
        action = None
        nargs = None
        collection = None
        default = decl.default
        ty = decl.type
        if ty is _inspect.NOT_DEFINED:
            ty = factory if isinstance(factory, type) else cls

        if factory is None:
            _factory = decl.type if decl.type is not _inspect.NOT_DEFINED else cls
        else:
            _factory = _ty.cast(Factory, factory)

        cls = ty
        if cls is not None and cls is not Argument:
            origin = _ty.get_origin(cls)
            args = _ty.get_args(cls)
            # An Optional[...] (Union carrying None) is not required.
            if origin in _compat.UNION_ORIGINS and _NONETYPE in args:
                required = False

            # One dispatch ladder, shared by the top level and every Union member
            # (C6). A plain/custom type yields factory=None -- keep the seeded
            # `_factory` (e.g. a `from_type` custom factory) for that case.
            spec = _factory_for(cls, name)
            if spec.factory is not None:
                _factory = spec.factory
            if spec.choices is not None:
                choices = spec.choices
            if spec.metavar is not None:
                metavar = spec.metavar
            if spec.action is not None:
                action = spec.action
            implicit_nargs = False
            if spec.nargs is not None:
                # `_factory_for`'s `list`/`set`/`tuple` branch hardcodes
                # `nargs="*"`, correct for a POSITIONAL (a trailing variadic
                # positional is exactly the point of that shape) but not for
                # an OPTION, which defaults to ONE value per occurrence
                # (`-f a -f b`) rather than space-separated multi-value in one
                # occurrence (`-f a b`). Deciding that here -- before an
                # explicit `NS(nargs=...)`/`NS(flags=...)` override has even
                # been applied (`Argument.from_type`'s `setattr` loop runs
                # AFTER this method returns) -- baked in the type-derived
                # shape too early: the documented `NS(nargs="*")` opt-back and
                # a `NS(flags=...)` that makes the field positional were both
                # unable to change it (A016). The downgrade itself now lives
                # in `ArgumentBuilder._kwargs`, computed from the FINAL flags
                # and nargs once every override is known; here we only record
                # that this `nargs` came from the type ladder (not a user
                # override) via `implicit_nargs`, so `_kwargs` can tell the
                # two cases apart.
                nargs = spec.nargs
                implicit_nargs = True
            if spec.collection is not None:
                collection = spec.collection
            if (
                spec.default is not _inspect.NOT_DEFINED
                and default is _inspect.NOT_DEFINED
            ):
                default = spec.default

        return ArgumentBuilder(
            name=name,
            flags=flags,
            type=_factory,
            default=default,
            help=help,
            required=required,
            choices=choices,
            metavar=metavar,
            action=action,
            nargs=nargs,
            collection=collection,
            _implicit_nargs_=implicit_nargs,
        )

    @classmethod
    def from_type(cls, factory: _ty.Callable[[str], _T], **kwargs):
        _factory = factory

        class Arg(cls):

            @classmethod
            def _argbuilder_(
                cls,
                name: str,
                decl: _inspect.ClsArgDeclaration,
                factory: "Factory | None" = _factory,
            ):
                builder = super()._argbuilder_(name, decl, factory or _factory)
                for k, v in kwargs.items():
                    setattr(builder, k, v)
                if "nargs" in kwargs:
                    # An explicit NS(nargs=...)/Meta(nargs=...) override wins
                    # outright -- clear the "came from the type ladder" marker
                    # so `_kwargs` never downgrades it back (A016).
                    builder._implicit_nargs_ = False
                if builder.split is not None:
                    # duho.Extend(): compose the split function with the
                    # field's OWN element factory (already resolved onto
                    # `builder.type` by `super()._argbuilder_()` above) rather
                    # than replacing it outright, so a typed collection (e.g.
                    # `list[int]`) still converts each split part, and the
                    # natural collection action (list/set/tuple) still runs
                    # unmodified (A020).
                    splitter = builder.split
                    base = builder.type

                    def _extend_factory(text, _splitter=splitter, _base=base):
                        return [_base(part) for part in _splitter(text)]

                    _extend_factory._duho_extend_base_ = base
                    builder.type = _extend_factory
                return builder

        return Arg


#: Actions argparse forbids from receiving `type=`.
_TYPE_INCOMPATIBLE_ACTIONS = frozenset(
    {
        "store_true",
        "store_false",
        "store_const",
        "append_const",
        "count",
        "help",
        "version",
        _argparse.BooleanOptionalAction,
    }
)

#: Actions that require `const=` to be supplied.
_CONST_REQUIRED_ACTIONS = frozenset({"store_const", "append_const"})

#: Zero-argument (flag-only) actions with no declared default get an
#: implicit one instead of `required=True` (A051) -- argparse's own natural
#: resting value for each, so `_effective_default_` agrees.
_ZERO_ARG_ACTION_DEFAULTS = {
    "count": 0,
    "store_const": None,
    "append_const": None,
    "store_false": True,
}


def _is_positional(flags: "_ty.Sequence[str]") -> bool:
    """A flag tuple whose sole entry has no leading ``-`` is a positional.

    The one place this decision is made (A068) -- ``ArgumentBuilder._kwargs``
    and the ``is_positional`` property below both call this, instead of each
    re-deriving ``len(flags) == 1 and not flags[0].startswith("-")``
    independently (and, before this fix, disagreeing with a THIRD copy in
    ``duho.mcp``).
    """
    return len(flags) == 1 and not flags[0].startswith("-")


class ArgumentBuilder(_argparse.Namespace):
    name: str
    flags: list[str]
    type: Factory
    default: "None | object | _inspect.NotDefined"
    help: str
    required: "bool | None" = None
    action: "str | _type[_argparse.Action] | None" = None
    nargs: "str|int|None" = None
    choices: "_ty.Sequence | None" = None
    metavar: "str | None" = None
    const: "object | _inspect.NotDefined" = NOT_DEFINED
    version: "str | None" = None
    env: "str | None" = None
    #: For a collection field (``list``/``set``/``tuple``) the target collection
    #: type; ``None`` for a scalar field. Recorded at build time so a layered
    #: (env/config) value converts to the SAME collection a CLI occurrence would
    #: produce (see :meth:`convert_layered`). ``self.type`` is then the *element*
    #: factory, not the collection factory.
    collection: "_type | None" = None
    #: ``duho.Extend()``'s split callable, or ``None``. Consumed by
    #: `Argument.from_type`'s wrapper to compose a text-splitting factory with
    #: the field's own element type (A020); never read afterwards.
    split: "_ty.Callable | None" = None
    #: True when `nargs` came from the type ladder (a `list`/`set`/`tuple`
    #: field) rather than an explicit `NS(nargs=...)` override. Lets
    #: `_kwargs` downgrade a repeatable OPTION to one value per occurrence
    #: without also clobbering a deliberate opt-back into space-separated
    #: multi-value (A016).
    _implicit_nargs_: bool = False

    @property
    def is_positional(self) -> bool:
        """True when this field's final, post-override flags are positional."""
        return _is_positional(self.flags)

    @property
    def is_bare_bool_flag(self) -> bool:
        """True when this field compiles to a bare ``store_true``/
        ``BooleanOptionalAction`` flag with no value of its own -- as opposed
        to a `bool` that carries `choices` (a `Literal[True, False]` field,
        which must go through `type=`+`choices=` like any other `Literal`) or
        one with an explicit `action=` override (A068).
        """
        return self.type is bool and not self.action and self.choices is None

    #: Truthy/falsy strings a layered bool value maps to True/False
    #: (case-insensitive, whitespace-stripped). The one shared table
    #: (``_compat.BOOL_TRUE``/``BOOL_FALSE``, A074/C046/D052) aliased here so
    #: existing readers of ``ArgumentBuilder._BOOL_TRUE``/``_BOOL_FALSE`` keep
    #: working. Unlike ``Env.bool`` (which treats an unrecognized string as
    #: False) the layered converter is STRICT -- an explicit config/env value
    #: that parses to neither is a user error, not a silent False.
    _BOOL_TRUE = _compat.BOOL_TRUE
    _BOOL_FALSE = _compat.BOOL_FALSE

    def _convert_single(self, raw):
        """Convert one raw scalar (env string / TOML-typed value) to the field type.

        A string always runs through ``self.type`` (the CLI text factory), so a
        bad value raises exactly the error argparse would. A non-string raw
        (TOML int/float/bool/date/list-element) goes through
        :meth:`_convert_non_str` (A017).
        """
        factory = self.type
        if isinstance(raw, str):
            return factory(raw)
        return self._convert_non_str(raw, factory)

    def _convert_non_str(self, raw, factory):
        """Shared lossless-widening rule for a non-string raw value (A017).

        Used by both :meth:`_convert_single` (``self.type``) and
        :meth:`convert_layered`'s dict-table branch (the per-value factory,
        which is NOT ``self.type`` there -- ``self.type`` is the
        ``_KVFactory`` wrapper) -- so a ``dict[str, V]`` table value widens
        exactly like a scalar ``V`` field would, instead of skipping this
        rule entirely.

        A raw value already an instance of the factory's type is kept as-is
        (``timeout: float`` receiving TOML int ``30`` widens to ``30.0``
        below, not here, since ``30`` is not already a ``float``). Otherwise:

        * ``bool`` is rejected for any factory except an actual bool
          factory -- ``bool`` subclasses ``int``, so ``isinstance(True, int)``
          is ``True``; without this check a TOML ``port = true`` silently
          stayed ``True`` in an ``int`` field.
        * ``list``/``tuple``/``dict``/``set``/``frozenset`` is rejected for a
          scalar field -- previously ``str(["a", "b"])`` silently stringified
          a list instead of rejecting it (the factory call itself never
          raises for ``str``).
        * ``float`` -> ``int`` is rejected when it has a fractional part
          (``int(1.5)`` truncates instead of erroring); the reverse (``int``
          widening to ``float``) is always lossless and stays allowed via the
          final factory-call fallback.
        * anything else: the factory itself decides, and a ``TypeError`` (a
          factory that flatly cannot accept a non-string, e.g.
          ``date.fromisoformat``) keeps the raw value unchanged -- documented,
          deliberate behavior, not a bug (a native TOML/JSON date in a date
          field, for example).
        """
        if isinstance(raw, bool):
            if factory is bool or factory is _bool_from_text:
                return raw
            raise ValueError(
                f"{raw!r} is a boolean but the field expects "
                f"{getattr(factory, '__name__', factory)!r}"
            )
        if isinstance(raw, (list, tuple, dict, set, frozenset)):
            raise ValueError(
                f"{raw!r} is a {type(raw).__name__}, which cannot widen to "
                f"{getattr(factory, '__name__', factory)!r}"
            )
        if isinstance(factory, type) and isinstance(raw, factory):
            return raw
        if isinstance(raw, float) and factory is int:
            if not raw.is_integer():
                raise ValueError(
                    f"{raw!r} has a fractional part; converting to int "
                    f"would lose precision"
                )
            return int(raw)
        try:
            return factory(raw)
        except TypeError:
            return raw

    def convert_layered(self, raw, *, source: str):
        """Convert a raw env/config *layer* value to this field's Python value.

        The env/config layers feed ``parser.set_defaults`` directly, bypassing
        argparse's own ``type=``/``action=`` handling -- so a layered value must
        be converted here to match what CLI parsing of the same field yields.
        Three field shapes are handled:

        * **bool** (``self.type is bool`` or a store_true/BooleanOptionalAction
          effective action): real bools pass through; strings map via the
          strict :data:`_BOOL_TRUE`/:data:`_BOOL_FALSE` sets (unknown -> error).
        * **collection** (``self.collection`` set): a *string* raw becomes a
          single element wrapped in the collection (``FILES=a.txt`` ->
          ``["a.txt"]``, matching one CLI occurrence); a *list/tuple/set* raw
          (a TOML array) converts element-wise then coerces to the collection.
        * **scalar**: via :meth:`_convert_single`.

        ``source`` names the layer ("env"/"config") for error messages; the
        calling resolver wraps any ``ValueError``/``TypeError`` with the field
        and variable name.
        """
        is_bool = (
            self.type is bool
            or self.action in ("store_true", "store_false")
            or self.action is _argparse.BooleanOptionalAction
        )
        if is_bool:
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, str):
                low = raw.strip().lower()
                if low in self._BOOL_TRUE:
                    return True
                if low in self._BOOL_FALSE:
                    return False
                raise ValueError(
                    f"{raw!r} is not a valid boolean "
                    f"(expected one of {sorted(self._BOOL_TRUE | self._BOOL_FALSE)})"
                )
            raise ValueError(f"cannot interpret {raw!r} ({source}) as a boolean")

        if self.collection is dict:
            # A dict field: a *string* raw ("k=v") runs the KV factory (one-pair
            # dict); a TOML *table* (Mapping) converts each value through the
            # value factory (strings only; already-typed TOML values pass
            # through, matching :meth:`_convert_single`).
            if isinstance(raw, _ty.Mapping):
                value_factory = getattr(self.type, "value_factory", str)
                result: dict = {}
                for k, v in raw.items():
                    if isinstance(v, str):
                        result[str(k)] = value_factory(v)
                    else:
                        result[str(k)] = self._convert_non_str(v, value_factory)
                return result
            return self._convert_single(raw)

        if self.collection is not None:
            extend_base = getattr(self.type, "_duho_extend_base_", None)
            if extend_base is not None:
                # duho.Extend(): `self.type` SPLITS one string into several
                # elements rather than converting a single one, so it must
                # NOT be run once per array element like a plain per-element
                # factory would (A020) -- a *string* raw is the whole thing
                # to split; a *list/tuple/set* raw (a TOML array) splits each
                # STRING element and flattens the parts together, widening a
                # non-string element (already fully typed) via the base
                # (per-element) factory instead.
                if isinstance(raw, str):
                    return self.collection(self.type(raw))
                if isinstance(raw, (list, tuple, set)):
                    parts: list = []
                    for e in raw:
                        if isinstance(e, str):
                            parts.extend(self.type(e))
                        else:
                            parts.append(self._convert_non_str(e, extend_base))
                    return self.collection(parts)
                return self.collection([self._convert_non_str(raw, extend_base)])
            if isinstance(raw, (list, tuple, set)):
                return self.collection(self._convert_single(e) for e in raw)
            return self.collection([self._convert_single(raw)])

        return self._convert_single(raw)

    def _kwargs(self, *, layered: bool = False):
        # NS(kwargs={...}) is the raw escape-hatch override: it must win over
        # every field-derived kwarg (explicit NS(field=...) loses to it), so
        # field derivation writes into `kwargs` first and the raw overrides
        # are applied last, on top.
        overrides = dict(getattr(self, "kwargs", None) or {})
        kwargs: dict = {}

        positional = self.is_positional
        nargs = self.nargs
        # A repeatable OPTION's collection nargs="*" (from the type ladder,
        # not a user override -- `_implicit_nargs_`) defaults to ONE value
        # per occurrence (`-f a -f b`), not space-separated multi-value in
        # one occurrence (`-f a b`). Computed here, from the FINAL flags and
        # nargs once every NS(nargs=...)/NS(flags=...) override is already
        # applied (both land on `self` before `_kwargs` ever runs), so the
        # documented `NS(nargs="*")` opt-back and a `NS(flags=...)` override
        # that makes the field positional both work (A016). `_CollectionAction`
        # (bound to list/set/tuple/frozenset alike, see A005) already has its
        # own single-value-per-occurrence branch, so no action swap is needed.
        if self._implicit_nargs_ and nargs == "*" and not positional:
            nargs = None
        if nargs is not None:
            kwargs["nargs"] = nargs

        if self.choices is not None:
            kwargs["choices"] = self.choices

        if self.metavar is not None:
            kwargs["metavar"] = self.metavar

        if self.default is not _inspect.NOT_DEFINED:
            kwargs["default"] = self.default

        if self.is_bare_bool_flag:
            # A bare bool becomes a store_true/BooleanOptionalAction flag. A
            # `Literal[True, False]` (carries choices) or an explicit
            # action= override is excluded by `is_bare_bool_flag` -- those go
            # through type=+choices= like any other Literal, since argparse
            # forbids choices= on a store_true action (C10).
            no_flag = any(
                f.startswith("--no-") for f in self.flags if f.startswith("--")
            )
            if self.default is True:
                if no_flag:
                    # BooleanOptionalAction tries to synthesize a --no-<flag>
                    # pair for a flag that ALREADY starts with --no- -- 3.14+
                    # rejects that outright, and 3.9-3.13 built the confusing
                    # --no-verify/--no-no-verify pair (A046). A plain
                    # store_false under the SAME flag means what a
                    # True-default --no-* flag always meant: presence sets
                    # False, absence keeps the True default.
                    kwargs["action"] = "store_false"
                else:
                    kwargs["action"] = _argparse.BooleanOptionalAction
            elif layered:
                # A field that can receive True from a layer OTHER than the
                # CLI (env=, or the owning class has a config source) needs a
                # way to turn it back off from the command line -- store_true
                # can only ever SET True, never re-assert False (A025).
                kwargs["action"] = _argparse.BooleanOptionalAction
            else:
                kwargs["action"] = "store_true"
        if self.action:
            kwargs["action"] = self.action

        # Resolve the *effective* action (raw override wins) so the
        # type-incompatibility / const / version guards below key off what
        # will actually be sent to add_argument, not the pre-override value.
        action = overrides.get("action", kwargs.get("action"))

        if action is _argparse.BooleanOptionalAction:
            # Python 3.14 removed the (already-deprecated) type/choices/
            # metavar parameters outright (A046) -- drop them here, not only
            # when duho itself picked the action, so an explicit
            # NS(action=argparse.BooleanOptionalAction) override is covered
            # too.
            kwargs.pop("metavar", None)
            kwargs.pop("choices", None)

        if action not in _TYPE_INCOMPATIBLE_ACTIONS:
            kwargs["type"] = self.type
        else:
            kwargs.pop("type", None)

        if action == "store_true" and "default" not in kwargs:
            kwargs["default"] = False
        if action is _argparse.BooleanOptionalAction and "default" not in kwargs:
            kwargs["default"] = False

        if action == "append" and self.collection not in (None, list):
            # duho.Append() forces argparse's stdlib "append" action, which
            # always produces a *list* -- it doesn't compose with a set/tuple
            # field's own collection action (A020). Fail loud at build time
            # instead of silently returning the wrong collection type.
            raise ValueError(
                f"argument {self.name!r}: duho.Append() does not support a "
                f"{self.collection.__name__} field (it always produces a "
                f"list); a repeatable {self.collection.__name__} field "
                f"already accumulates one value per occurrence without it"
            )

        if action in _CONST_REQUIRED_ACTIONS:
            const = (
                self.const
                if self.const is not NOT_DEFINED
                else overrides.get("const", NOT_DEFINED)
            )
            if const is NOT_DEFINED:
                raise ValueError(
                    f"argument {self.name!r}: action={action!r} requires const="
                )
            kwargs["const"] = const
        elif self.const is not NOT_DEFINED:
            kwargs["const"] = self.const

        if action == "version":
            version = (
                self.version if self.version is not None else overrides.get("version")
            )
            if version is not None:
                kwargs["version"] = version

        dest = self.name
        if positional:
            dest = None

        if dest:
            kwargs["dest"] = dest

        if positional:
            if self.choices is not None and kwargs.get("nargs") == "*":
                # A variadic (nargs="*") positional with `choices=` -- e.g. a
                # `list[T]` positional through `Choice()`/`NS(choices=...)`,
                # or a `list[Literal[...]]` positional (whose element choices
                # bubble up to the field spec too). argparse itself (through
                # 3.13, bpo-9625) validates the DEFAULT against `choices` too
                # whenever the positional is omitted -- and does so with the
                # raw default object, so even `default=SUPPRESS` gets checked
                # against `choices` and fails (worse than the plain empty
                # list). Move the membership check into the element factory
                # instead (mirrors the enforcement a Union/Literal member
                # already gets, see `_choice_checked`) and drop `choices=`
                # from `add_argument` for this shape entirely -- so argparse
                # never validates anything itself here, on any version
                # (A049). `metavar` still shows the allowed values.
                if "type" in kwargs:
                    kwargs["type"] = _choice_checked(kwargs["type"], self.choices)
                kwargs.pop("choices", None)
                if kwargs.get("metavar") is None:
                    kwargs["metavar"] = (
                        "{" + ",".join(str(c) for c in self.choices) + "}"
                    )
            elif "nargs" not in kwargs and (
                "default" in kwargs or self.required is False
            ):
                # An optional positional (a real default, or an Optional[T]
                # with none at all -- A026) needs nargs="?", otherwise
                # argparse makes it required and ignores the default.
                kwargs["nargs"] = "?"
            kwargs.pop("required", None)
        elif action in ("version", "help"):
            # argparse's _VersionAction / _HelpAction don't accept required=.
            pass
        elif self.required is not None:
            kwargs["required"] = self.required
        elif dest is not None:
            if getattr(self, "conflicts", None):
                # A mutually-exclusive member with no explicit required= and
                # no default: argparse forbids a required member inside a
                # mutex group ("mutually exclusive arguments must be
                # optional"), so this can never become `required=True` here
                # -- group-level requiredness is exactly what
                # `conflicts_required=` expresses instead (A023).
                kwargs["required"] = False
            elif action in _ZERO_ARG_ACTION_DEFAULTS and "default" not in kwargs:
                # A flag-style zero-argument action (count/store_const/
                # append_const/store_false) with no declared default gets
                # argparse's own natural resting value instead of becoming a
                # mandatory flag (A051).
                kwargs["required"] = False
                kwargs["default"] = _ZERO_ARG_ACTION_DEFAULTS[action]
            else:
                kwargs["required"] = "default" not in kwargs

        kwargs.update(overrides)

        # Copy a mutable default so each parser build gets its OWN list/set/
        # dict (the builder is cached on the class, so without this every
        # build would share the same object). A second, per-PARSE copy (for
        # a parser reused across multiple parse_args() calls, A021) happens
        # in `_initparser_`'s wrapped `parse_known_args`. Covers both the
        # collection-branch default ([]/set()) and an override default (e.g.
        # an explicit Extend(sep, default=[...])). Tuples are immutable.
        default_value = kwargs.get("default")
        if isinstance(default_value, (list, set, dict)):
            kwargs["default"] = _copy.copy(default_value)

        return kwargs

    def add_to_parser(self, parser: _argparse.ArgumentParser, *, layered: bool = False):
        help = self.help
        if callable(help):  # type: ignore
            help = help()
        kwargs = self._kwargs(layered=layered)
        action = parser.add_argument(*self.flags, help=help, **kwargs)
        if isinstance(action, _argparse.BooleanOptionalAction):
            # 3.9/3.10's BooleanOptionalAction.__init__ unconditionally
            # appends " (default: %(default)s)" to any non-None help
            # (removed in 3.11) -- reset to the exact help duho passed in so
            # an NS(help=argparse.SUPPRESS) flag stays hidden (the identity
            # check argparse itself uses to hide it) and no literal
            # "%(default)s" leaks into agent-help JSON on the floor versions
            # (A047).
            action.help = help
        return action

    def _effective_default_(self):
        """The value argparse would leave this field at when not supplied.

        Reuses ``_kwargs()`` so it agrees exactly with what ``add_to_parser``
        registers (e.g. a ``store_true`` bool resolves to ``False`` even when no
        ``default`` was declared). Returns :data:`NOT_DEFINED` for a required
        field with no default -- callers seeding an instance leave those unset.
        A non-required OPTION with no declared default (e.g. ``Optional[int]``)
        resolves to ``None``, matching what argparse itself leaves on the
        namespace when the flag is absent (A027).
        """
        kwargs = self._kwargs()
        if "default" in kwargs:
            return kwargs["default"]
        if kwargs.get("required") is False:
            return None
        return NOT_DEFINED


class _Parser(_argparse.ArgumentParser, _ty.Generic[_T]):
    def parse_args(self, args=None, namespace: "_T | None" = None) -> _T:  # type: ignore
        raise NotImplementedError()

    def parse_known_args(  # type: ignore
        self, args=None, namespace: "_T | None" = None
    ) -> tuple[_T, list[str]]:
        raise NotImplementedError()


#: `nargs` values that make a positional variable-arity -- the shape that
#: triggers argparse's greedy positional-run-matching papercut (bpo-15112)
#: when ANOTHER positional sits in the same parser. Verified this session
#: (bare stdlib, no duho): `"*"` and `"?"` fail identically for a flag placed
#: between the two positionals; `"+"` fails too, just at a different arg
#: count (2+ trailing values instead of 1) -- all three are the trigger set.
_VARIADIC_NARGS = ("*", "+", "?")


def _has_variadic_positional(parser: "_argparse.ArgumentParser") -> bool:
    """True if ``parser`` has at least one variable-arity positional.

    Two DIFFERENT argparse papercuts both need this reorder, and between them
    a single variable-arity positional is already enough to trigger one:

    * A flag placed BETWEEN a variable-arity positional and ANOTHER
      positional breaks under argparse's greedy positional-run matching
      (bpo-15112): the run is settled against the argv slice before the next
      optional token, so the variadic one can close out empty/short and
      never reopen.
    * A flag placed anywhere touching a LONE variable-arity positional's own
      run -- with no sibling positional at all -- ALSO gets swallowed as
      "unrecognized arguments" (bpo-14191); a previous version of this
      docstring claimed a lone variadic positional was unaffected, which was
      not actually true (verified this session, bare stdlib: A067).

    Both shapes are handled by the same reorder pass below, so this only
    needs to detect "at least one variable-arity positional" -- no sibling
    required.

    The subparsers action itself (``dest="command"``, added by
    ``add_subparsers``) is NOT option-string-bearing either, but it is not a
    user-declared positional field -- excluded explicitly so a root class
    with subcommands and no OTHER declared positional doesn't false-trigger.
    """
    positionals = [
        action
        for action in parser._actions  # type: ignore[attr-defined]
        if not action.option_strings
        and not isinstance(action, _argparse._SubParsersAction)  # type: ignore[attr-defined]
    ]
    return any(action.nargs in _VARIADIC_NARGS for action in positionals)


def _patch_parser_for_reorder(parser: "_argparse.ArgumentParser") -> None:
    """Install JUST the flag-between-positionals reorder on a plain parser.

    For parsers built outside ``Args._initparser_`` -- a module command's
    subparser (``runtime._register_module_command``), built via a bare
    ``subparsers.add_parser(...)`` -- so they never get the patched
    ``parse_known_args`` a declarative ``Args``/``Cmd`` subparser does. That
    parser needs neither ``"#cls"`` dispatch nor ``_passthrough_`` splitting
    (a module command has no subclass to select and the root already owns the
    ``--`` split), only this one fix, so a full ``_initparser_`` call would be
    the wrong tool -- it would also try to construct an ``Args`` instance from
    a parser that was never declared as one.
    """
    real_parse_known_args = parser.parse_known_args

    def parse_known_args(
        args: "_ty.Sequence[str] | None" = None, namespace: "NS | None" = None
    ):
        if args is not None and _has_variadic_positional(parser):
            args = _reorder_argv_for_variadic_positional(parser, list(args))
        return real_parse_known_args(args, namespace)

    parser.parse_known_args = parse_known_args  # type: ignore


def _reorder_argv_for_variadic_positional(
    parser: "_argparse.ArgumentParser", argv: "list[str]"
) -> "list[str]":
    """Hoist recognized flags (+ their values) before the positional run.

    Preprocessing ONLY -- never decides an input is invalid. Scans ``argv``
    against ``parser``'s OWN, already-built ``_option_string_actions``
    registry; a token recognized there (bare, or via its ``key`` half of a
    ``--flag=value`` form) is hoisted, along with the value(s) its action's
    ``nargs``/type says it consumes, into a leading ``flags`` run. Everything
    else stays in a trailing ``positionals`` run, in original relative order.
    Concatenating ``flags + positionals`` gives argparse a slice where the
    positional run is contiguous and never interrupted by a flag, sidestepping
    the greedy-matching papercut this function exists for.

    Recognizes a flag by exact key, by its ``--flag=value`` split, by an
    attached short-option value (``-fVALUE``), and by an unambiguous
    ``allow_abbrev`` long-option prefix (``--filt`` for ``--filter``) (A048)
    -- the same spellings argparse itself accepts, so a flag written that way
    between two positionals is hoisted exactly like its long/separate-token
    form is.

    **Bails (returns ``argv`` UNCHANGED) on anything it isn't certain about**
    -- a `-`-prefixed token NOT recognized by any of the above (and not a
    negative-number value the parser itself would accept, per its own
    ``_negative_number_matcher``/``_has_negative_number_optionals``), or a
    flag needing a value with none left in ``argv``. This is deliberate: a
    genuine typo (``--filtr`` for ``--filter``) must still surface argparse's
    own honest "unrecognized arguments" error through the UNMODIFIED real
    parse, never get silently absorbed as a phantom positional value. This
    function only ever makes a VALID input parse correctly, or gets out of
    the way entirely.
    """
    known = parser._option_string_actions  # type: ignore[attr-defined]
    negative_number_matcher = getattr(parser, "_negative_number_matcher", None)
    has_negative_number_optionals = bool(
        getattr(parser, "_has_negative_number_optionals", [])
    )
    allow_abbrev = getattr(parser, "allow_abbrev", True)

    flags: "list[str]" = []
    positionals: "list[str]" = []
    i = 0
    n = len(argv)
    while i < n:
        token = argv[i]
        if token == "--":
            # A literal `--` separator is never part of THIS parser's own
            # flag/positional grammar (duho's `_passthrough_` handling
            # already splits argv on the FIRST `--` before this function
            # ever sees it -- see `_initparser_`'s patched
            # `parse_known_args` -- so reaching one here means a SECOND
            # `--`, which is itself part of the payload). Stop reordering;
            # everything from here on is left exactly as-is.
            positionals.extend(argv[i:])
            break

        action = known.get(token)
        self_contained = False
        if action is None and "=" in token:
            action = known.get(token.split("=", 1)[0])
            # `--flag=value` is a single self-contained token; no separate
            # value token to hoist alongside it.
            self_contained = action is not None
        if (
            action is None
            and token.startswith("--")
            and allow_abbrev
            and "=" not in token
        ):
            # An unambiguous long-option PREFIX under argparse's own
            # `allow_abbrev` rule (e.g. `--filt` for `--filter`) -- recognized
            # only when exactly one ACTION (aliases of the same one still
            # count as one) has an option string starting with this token.
            candidates = {
                id(a): a
                for opt, a in known.items()
                if opt.startswith("--") and opt.startswith(token)
            }
            if len(candidates) == 1:
                (action,) = candidates.values()
        if action is None and len(token) > 2 and token[0] == "-" and token[1] != "-":
            # An attached short-option value (`-fVALUE`, `-j3`): recognized
            # only when the two-character prefix maps to a registered short
            # option that itself takes exactly one value -- a zero-value
            # action (`-v`, `-h`) cannot absorb a trailing value this way, and
            # a variadic-nargs one is ambiguous just like the separate-token
            # case below.
            short_action = known.get(token[:2])
            if short_action is not None and short_action.nargs is None:
                action = short_action
                self_contained = True

        if self_contained:
            flags.append(token)
            i += 1
            continue

        if action is not None:
            zero_value_action = action.nargs == 0 or isinstance(
                action,
                (
                    _argparse._StoreTrueAction,
                    _argparse._StoreFalseAction,
                    _argparse._CountAction,
                    _argparse._HelpAction,
                ),
            )
            if zero_value_action:
                flags.append(token)
                i += 1
                continue
            if action.nargs is not None:
                # The flag's OWN nargs is variable (`"*"`/`"+"`/`"?"`) or an
                # explicit fixed count -- e.g. a `list[T]` field's default
                # `action="extend", nargs="*"` builder. Confirmed this
                # session (bare stdlib): a variadic-nargs FLAG placed
                # directly before positional values, with no separator, is
                # AMBIGUOUS FOR ARGPARSE ITSELF -- even correctly-ordered
                # argv silently misparses (extra tokens get absorbed into
                # the flag's own value list instead of the positional they
                # belonged to; see `docs/guide/arguments.md` /
                # `AGENTS.md`'s note on this shape). Reordering cannot
                # resolve an ambiguity the REAL parser cannot resolve either
                # -- bail unreordered rather than guess and risk swallowing
                # a token that belonged to a positional.
                return argv
            if i + 1 >= n:
                # A flag needing exactly one value, with none left in argv --
                # malformed input. Bail: let the REAL parse produce its own
                # error rather than guessing here.
                return argv
            flags.append(token)
            flags.append(argv[i + 1])
            i += 2
            continue

        if token.startswith("-") and len(token) > 1:
            looks_negative_number = bool(
                negative_number_matcher and negative_number_matcher.match(token)
            )
            if looks_negative_number and not has_negative_number_optionals:
                # A negative-number-shaped token this parser has no
                # negative-number-shaped OPTIONAL to collide with -- treat
                # as a genuine positional value (mirrors argparse's own
                # `_negative_number_matcher` logic for the same decision).
                positionals.append(token)
                i += 1
                continue
            # An unrecognized `-`-prefixed token. Could be a real typo, or a
            # flag this reorder pass doesn't understand -- either way, NOT
            # confident enough to reorder. Bail unchanged; the real parse
            # surfaces its own honest error.
            return argv

        positionals.append(token)
        i += 1

    return flags + positionals


def _suppress_inherited_defaults(child_parser, root_dests, root_defaults=None):
    """Make a subcommand's inherited-option defaults not clobber the root's value.

    For each action on ``child_parser`` whose ``dest`` is also a field declared
    on the root (``root_dests``), set the action's default to ``SUPPRESS`` so
    that, when the flag is absent from the subcommand's argv, argparse leaves the
    namespace value the root already parsed (from an option given before the
    subcommand) intact. Only optional (flagged, non-required) actions are
    touched -- positionals and required options keep their behavior, and the
    subparsers action itself (``dest="command"``) is never a root field.

    ``root_defaults`` (optional ``{dest: effective_default}``) lets the caller
    skip suppression for a dest the child DELIBERATELY re-declares with a default
    differing from the root's: that override is intentional and must win, so the
    child keeps its own default rather than deferring to the root (M16).

    ``value_sources`` / config layering are unaffected: they operate on the root
    parser's own actions, not these child copies.
    """
    root_defaults = root_defaults or {}
    for action in child_parser._actions:
        if action.dest not in root_dests:
            continue
        if not action.option_strings:  # positional
            continue
        if getattr(action, "required", False):
            continue
        if (
            action.dest in root_defaults
            and action.default != root_defaults[action.dest]
        ):
            # The child re-declares this field with a different default -- a
            # deliberate override; keep it (M16).
            continue
        action.default = _argparse.SUPPRESS


class Args(_argparse.Namespace):
    #: Pre-seeded empty class-body-constants cache (P2). ``_class_constants``
    #: (``_introspect``) short-circuits on ``"_duho_constants_" in vars(cls)``,
    #: so seeding it here means building ANY user parser never AST-parses
    #: duho's own ``args.py`` to scan these framework base classes for
    #: class-body flag/env/docstring metadata -- they declare none. This is
    #: safe ONLY because ``Args``/``Cmd``/``Cli`` carry no real CLI-field
    #: declarations in their bodies. A subclass that DOES declare fields gets
    #: its OWN ``vars(cls)`` entry populated by the normal scan (the seed is
    #: per-class in ``vars``, never inherited into the MRO walk), and
    #: ``presets.LoggingArgs`` deliberately is NOT seeded because its class body
    #: declares real fields whose flags-tuples must still be scanned.
    _duho_constants_: dict = {}

    def __init__(self, **kwargs):
        # Namespace.__init__ only setattrs what's passed, so a directly-built
        # instance (or the self-cloning `type(self)(**self._get_kwargs())`
        # pattern) would be missing any declared field not supplied -- notably
        # `store_true` bools, whose default only materializes via argparse.
        # Seed each declared field to its effective default when absent, so a
        # direct instance has the same attribute surface as a parsed one. Only
        # GAPS are filled: passed kwargs (incl. parsed values) always win, and a
        # required field with no default (NOT_DEFINED) is left unset.
        #
        # `name not in vars(self)` (rather than `hasattr`) is deliberate: a
        # field with an explicit CLASS-level default (`files: list = []`) is
        # already `hasattr`-true via inheritance, which used to skip seeding
        # entirely -- so a direct instance read the CLASS ATTRIBUTE itself,
        # and mutating a mutable one (`instance.files.append(...)`) mutated
        # every other instance and every later parse's default too (A022).
        # `vars(self)` only sees THIS instance's own attributes, so the gap
        # still gets filled with `_effective_default_()`'s fresh copy.
        super().__init__(**kwargs)
        for builder in type(self)._getargs_():
            name = builder.name
            if name in kwargs or name in vars(self):
                continue
            default = builder._effective_default_()
            if default is not NOT_DEFINED:
                setattr(self, name, default)

    @classmethod
    def _getargs_(cls):
        if "_duho_builders_" in vars(cls):
            return cls._duho_builders_

        clsargs = _inspect.get_clsargs(cls)
        args: list[ArgumentBuilder] = []
        for name, decl in clsargs.items():
            if decl.annotations:
                # SUPPRESS anywhere in the metadata hides the field (M17).
                if _argparse.SUPPRESS in decl.annotations:
                    continue
                options: dict = {}
                for opts in decl.annotations:
                    # Only configuration-shaped metadata is consumed: a Mapping
                    # or a namespace-like object (has __dict__, e.g. NS(...)). A
                    # PEP-727-style object with a str `.documentation` contributes
                    # help text. Any other metadata (a bare `Annotated[int, "doc"]`
                    # string, an int, ...) is ignored silently, as Annotated's
                    # own semantics require (C8).
                    if isinstance(opts, Meta):
                        # Typed metadata: merge only the explicitly-set fields so
                        # an unset (sentinel) field never overrides a type-derived
                        # kwarg (F5).
                        options.update(opts._duho_options_())
                    elif isinstance(opts, _ty.Mapping):
                        options.update(opts)
                    elif isinstance(getattr(opts, "documentation", None), str):
                        options.setdefault("help", opts.documentation)
                    elif hasattr(opts, "__dict__"):
                        options.update(vars(opts))
                builder = Argument.from_type(decl.type, **options)._argbuilder_
            elif isinstance(decl.type, Argument):
                builder = decl.type._argbuilder_
            else:
                builder = Argument.from_type(decl.type)._argbuilder_
            args.append(builder(name, decl))

        setattr(cls, "_duho_builders_", args)
        return args

    @classmethod
    def _parser_(
        cls,
        subparser: "_argparse._SubParsersAction | None" = None,
        name: "str | None" = None,  # type: ignore
        parents: _ty.Sequence[_argparse.ArgumentParser] = (),
        init=True,
        **kwargs,
    ) -> "_Parser[_Self]":
        if subparser:
            method = subparser.add_parser
        else:
            method = _argparse.ArgumentParser

        # Persist the derived name onto the class ONLY when the caller did not
        # supply one: a `_parser_(name="alias")` alias is a one-off (e.g. building
        # a parser under a different prog) and must not permanently rewrite the
        # class's `_parsername_`, which later builds and config-table lookups key
        # off (M10).
        caller_supplied_name = name is not None
        name: str = name or getattr(cls, "_parsername_", None) or cls.__name__
        # Never persist onto a bare framework base (``Args``/``Cmd``/``Cli``) used
        # directly as a root -- e.g. ``duho.app(root=None)`` builds
        # ``Args._parser_()``. ``setattr`` lands on that base's own ``__dict__``
        # and then LEAKS via inheritance to every user subclass, so a later
        # ``getattr(Deploy, "_parsername_")`` returns the base's ``"Args"`` and
        # mis-names the subcommand ("invalid choice: 'Deploy' (choose from
        # 'Args')"). A real user root/subcommand class persists normally.
        _is_framework_base = cls.__module__ == __name__ and cls.__name__ in (
            "Args",
            "Cmd",
            "Cli",
        )
        if (
            not caller_supplied_name
            and not getattr(cls, "_parsername_", None)
            and not _is_framework_base
        ):
            setattr(cls, "_parsername_", name)
        # Escape `%` in docstring-derived text: argparse %-expands help strings
        # (HelpFormatter._expand_help does `help % params`), so a literal `%` in
        # a class docstring (e.g. an RPM `%files` mention) would otherwise crash
        # add_parser's _check_help at parser-BUILD time. A caller-supplied
        # description/help already in kwargs is left untouched by setdefault.
        _doc = (cls.__doc__ or "").replace("%", "%%")
        kwargs.setdefault("description", _doc)
        # F8: opt-in help formatter (``_help_formatter_`` class attr, e.g.
        # ``duho.DefaultsFormatter``/``duho.ColorHelpFormatter``) plumbed into
        # argparse's ``formatter_class``. ``setdefault`` so a caller-supplied
        # ``formatter_class=`` still wins; unset means argparse's plain default.
        help_formatter = getattr(cls, "_help_formatter_", None)
        if help_formatter is not None:
            kwargs.setdefault("formatter_class", help_formatter)
        if subparser:
            docstring = _doc
            kwargs.setdefault(
                "help", docstring.strip().splitlines()[0] if docstring.strip() else ""
            )
            # Subcommand aliases (argparse's add_parser accepts `aliases`; the
            # top-level ArgumentParser does not, so only apply when nested).
            aliases = getattr(cls, "_parseraliases_", None)
            if aliases:
                kwargs.setdefault("aliases", list(aliases))
        parser = _ty.cast(
            "_Parser[_ty.Self]",
            method(name, parents=list(parents), **kwargs),
        )

        if init:
            cls._initparser_(parser, is_subcommand=bool(subparser))

        subcommands = getattr(cls, "_subcommands_", None)
        if subcommands:
            subparsers = parser.add_subparsers(dest="command", required=True)
            # Dests this (root) class declares itself: an option given BEFORE the
            # subcommand parses into these on the root namespace. A child that
            # inherits the same field (via MRO) re-declares it with its own
            # default and would clobber that value back when the flag is absent
            # from the child's argv. Suppress the child's default for those
            # shared, optional dests so the parent's parsed value survives; the
            # child still accepts the flag AFTER the subcommand (overwrites) and
            # its own unique args are untouched.
            root_builders = {b.name: b for b in cls._getargs_()}
            root_dests = set(root_builders)
            root_defaults = {
                n: b._effective_default_() for n, b in root_builders.items()
            }
            for sub in subcommands:
                child = sub._parser_(subparsers)
                _suppress_inherited_defaults(child, root_dests, root_defaults)
                # F8: propagate the root's opt-in help formatter down to a child
                # that declares none of its own, so a single ``_help_formatter_``
                # on the app root styles the whole subcommand tree consistently.
                # A child with its OWN ``_help_formatter_`` (already applied by its
                # ``_parser_``) is left untouched.
                if (
                    help_formatter is not None
                    and getattr(sub, "_help_formatter_", None) is None
                ):
                    child.formatter_class = help_formatter

        return parser

    @classmethod
    def _initparser_(
        cls,
        parser: _argparse.ArgumentParser,
        exclusive_groups: dict = None,
        is_subcommand: bool = False,
    ):

        def parse_known_args(
            args: "_ty.Sequence[str] | None" = None, namespace: "NS | None" = None
        ):
            if namespace is None:
                namespace = _argparse.Namespace()

            setattr(namespace, "#cls", cls)

            # `_passthrough_`: capture argv after the FIRST literal `--`
            # separator. Only the
            # top-level parse owns the split -- a subparser is invoked by
            # argparse._SubParsersAction with an already-sliced arg list and
            # namespace=None, so splitting there would double-consume. The
            # left side is parsed normally; the right side is stashed on the
            # constructed instance as `_passthrough_` (empty list when absent
            # or when multiple `--` appear, only the first splits).
            passthrough: "list[str] | None" = None
            if not is_subcommand:
                if args is None:
                    argv = _sys.argv[1:]
                else:
                    argv = list(args)
                if "--" in argv:
                    idx = argv.index("--")
                    passthrough = argv[idx + 1 :]
                    argv = argv[:idx]
                args = argv

            # A flag placed BETWEEN two positional groups (one of them
            # variable-arity) breaks under argparse's own greedy
            # positional-run matching (bpo-15112) -- reorder recognized
            # flags to the front of the slice THIS parser will actually see
            # so the positional run stays contiguous. Scoped to parsers with
            # the risky shape only (cheap check, no cost/behavior change
            # otherwise); never touches anything after the `--` split above
            # (that's `passthrough`, already carved out); bails unreordered
            # on anything it isn't certain about, so a genuine typo still
            # surfaces argparse's own honest error through the real,
            # UNMODIFIED parse below.
            if args is not None and _has_variadic_positional(parser):
                args = _reorder_argv_for_variadic_positional(parser, list(args))

            parsed, unk = _argparse.ArgumentParser.parse_known_args(
                parser, args, namespace
            )

            # A021: per-PARSE mutable-default copy, scoped to THIS parser's
            # own actions (a subcommand's redeclared fields are a different
            # action set, handled the same way when ITS OWN wrapped
            # parse_known_args runs).
            for _pa in parser._actions:
                _dest = _pa.dest
                if _dest is None or _dest is _argparse.SUPPRESS:
                    continue
                _default = _pa.default
                if isinstance(_default, (list, set, dict)) and (
                    getattr(parsed, _dest, None) is _default
                ):
                    # A021: `_kwargs` already gives each parser BUILD its own
                    # copy of a mutable default, but argparse puts that SAME
                    # object onto every namespace a REUSED parser produces
                    # when the field is never touched by this parse -- so two
                    # `parse_args()` calls on one cached parser would share
                    # (and, on mutation, leak into each other's) that list/
                    # set/dict. Break the aliasing once more, per parse.
                    setattr(parsed, _dest, _copy.copy(_default))

            if is_subcommand:
                # Invoked via argparse._SubParsersAction.__call__, which
                # calls us with namespace=None and then copies vars(result)
                # back onto the *parent* namespace. Keep "#cls" in the
                # returned dict (rather than popping/constructing here) so
                # it propagates upward and overwrites the parent's own
                # "#cls" -- subparsers are parsed after the parent's own
                # actions, so the deepest selection always lands last and
                # wins. Only the true top-level call (is_subcommand=False)
                # pops "#cls" and constructs the final instance.
                return parsed, unk

            _cls: "type[_ty.Self]" = parsed.__dict__.pop("#cls")
            parser._duho_selected_cls_ = _cls  # type: ignore
            # Drop the `_CollectionAction`/`UpdateAction` sidecars
            # (`_duho_items_<dest>` / `_duho_dict_seen_<dest>`) before
            # constructing the instance so this internal bookkeeping never leaks
            # into vars(instance) or the documented clone pattern (M12).
            for _sidecar in [
                k
                for k in parsed.__dict__
                if k.startswith("_duho_items_") or k.startswith("_duho_dict_seen_")
            ]:
                del parsed.__dict__[_sidecar]
            instance = _cls(**parsed.__dict__)
            # Attach captured passthrough (empty list when no `--` was seen).
            instance._passthrough_ = passthrough if passthrough is not None else []
            # Debug-aid linkage for duho.value_sources(): remember the parser
            # (which carries _duho_value_sources_/_duho_merged_defaults_ from
            # _apply_default_layers) that produced instances of this class.
            # Per-class, not per-instance -- keeps Args instances themselves
            # free of framework bookkeeping in vars()/__dict__.
            _cls._duho_last_parser_ = parser  # type: ignore[attr-defined]
            return instance, unk

        parser.parse_known_args = parse_known_args  # type: ignore
        exclusive_groups = exclusive_groups or {}

        version = _resolve_version(cls)
        actions_by_dest_pre = {action.dest: action for action in parser._actions}
        if version and "version" not in actions_by_dest_pre:
            parser.add_argument(
                "--version",
                action="version",
                version=f"%(prog)s {version}",
            )

        # --print-completion: opt-in via _completion_ = True, only injected
        # on the top-level parser (a subcommand's own parser only sees its
        # own subtree, not the whole app) -- skipped if a "print_completion"
        # dest already exists (e.g. from a parents=[...] parser).
        if not is_subcommand and getattr(cls, "_completion_", False):
            actions_by_dest_pre2 = {action.dest: action for action in parser._actions}
            if "print_completion" not in actions_by_dest_pre2:
                parser.add_argument(
                    "--print-completion",
                    choices=("bash", "zsh", "fish", "powershell"),
                    action=_PrintCompletionAction,
                    root_parser=parser,
                    dest="print_completion",
                    help="Print a shell completion script for the given shell and exit.",
                )

        # F2: a mutually-exclusive group is *required* when ANY of its members
        # declares NS(conflicts_required=True). Groups are created lazily on the
        # first member, so pre-compute requiredness across all members here and
        # pass it at creation (argparse fixes `required` at group-build time).
        required_by_conflicts: "dict[str, bool]" = {}
        # A `conflicts=` key must use the SAME `group=` title (or no title)
        # everywhere it appears -- otherwise members that share a conflicts=
        # string land in TWO separate mutex groups (one per title) and are no
        # longer mutually exclusive at all, silently (A024).
        conflicts_titles: "dict[str, object]" = {}
        for arg in cls._getargs_():
            conflicts = getattr(arg, "conflicts", None)
            if conflicts:
                required_by_conflicts[conflicts] = required_by_conflicts.get(
                    conflicts, False
                ) or bool(getattr(arg, "conflicts_required", False))
                group_title = getattr(arg, "group", None)
                if conflicts in conflicts_titles:
                    if conflicts_titles[conflicts] != group_title:
                        raise ValueError(
                            f"argument {arg.name!r}: conflicts={conflicts!r} is "
                            f"declared with group={group_title!r} here but "
                            f"group={conflicts_titles[conflicts]!r} elsewhere; "
                            f"every field sharing a conflicts= key must use the "
                            f"same group= (or none)"
                        )
                else:
                    conflicts_titles[conflicts] = group_title

        # A025: a bool field that can receive True from a layer OTHER than
        # the CLI needs a way to turn it back off from the command line (see
        # `ArgumentBuilder._kwargs`'s `layered` parameter). `env=` is a
        # per-field signal; a config source is a per-CLASS one.
        _has_config_source = getattr(cls, "_config_", None) is not None

        # F3: titled argument groups (NS(group="...")), created lazily per title.
        # Persisted on the parser so a parents=[...] merge / subclass override can
        # reuse them, mirroring `exclusive_groups`.
        titled_groups: "dict[str, object]" = (
            getattr(parser, "_duho_titled_groups_", None) or {}
        )

        actions_by_dest = {action.dest: action for action in parser._actions}
        for arg in cls._getargs_():
            _action = actions_by_dest.get(arg.name)
            if _action:
                continue
            conflicts = getattr(arg, "conflicts", None)
            group_title = getattr(arg, "group", None)

            # The container the field's argument is added to: a titled group
            # (F3) when NS(group=...) is set, else the parser itself.
            if group_title is not None:
                if group_title not in titled_groups:
                    titled_groups[group_title] = parser.add_argument_group(group_title)
                container = titled_groups[group_title]
            else:
                container = parser

            if conflicts:
                # A conflicts= member lives in a mutually-exclusive group. When
                # it ALSO declares group=, nest the exclusive group inside the
                # titled group (argparse supports it), keyed by (group,
                # conflicts); otherwise it's a top-level group keyed by conflicts
                # (so `parser.exclusive_groups["type"]` keeps working).
                key = (group_title, conflicts) if group_title is not None else conflicts
                if key not in exclusive_groups:
                    exclusive_groups[key] = container.add_mutually_exclusive_group(
                        required=required_by_conflicts.get(conflicts, False)
                    )
                group = exclusive_groups[key]
            else:
                group = container

            layered = _has_config_source or bool(getattr(arg, "env", None))
            _action = arg.add_to_parser(group, layered=layered)

        # Expose the built mutually-exclusive groups on the parser so a subclass
        # `_parser_` override can add extra options into a `conflicts=`-built
        # group (e.g. a short-flag alias that must stay mutually exclusive with a
        # declared field). Merge rather than overwrite so a parents=[...] parser
        # that already carries groups keeps them.
        existing = getattr(parser, "exclusive_groups", None)
        if existing:
            existing.update(exclusive_groups)
        else:
            parser.exclusive_groups = exclusive_groups
        parser._duho_titled_groups_ = titled_groups  # type: ignore[attr-defined]

        # Agent help: stash the class for the emitter, make --help env-aware, and
        # add the opt-in --help-agents flag. See `_install_agent_help`.
        _install_agent_help(parser, cls, is_subcommand)

        return parser


class Cmd(Args):
    """An executable command: a data ``Args`` plus the command contract.

    ``Args`` (Plan 13) is pure data -- a Namespace of parsed values, not
    required to run. ``Cmd`` adds the *executable* contract on top:
    ``__call__(self)`` is the command entrypoint (``instance()`` runs the
    command).

    The entrypoint is ``__call__`` -- a dunder -- deliberately. A ``Cmd``
    subclass's namespace is user-owned: annotated non-underscore attributes
    become CLI fields, so a plain method name like ``main`` would collide
    with a user field ``main: str`` (``--main``). ``__call__`` lives in the
    dunder namespace duho's field introspection already skips, so it can
    never clash with a declared flag.

    A ``Cmd`` subclass that does not override ``__call__`` raises
    ``NotImplementedError`` naming the class when dispatched -- the same
    loud-failure spirit as Plan 04's earlier "missing ``__call__``". Data-only
    ``Args`` subclasses stay non-runnable by design: ``duho.main`` rejects them
    with a clear error rather than silently no-op'ing (the whole point of the
    split is that "runnable" is explicit).

    Base-order for the ``LoggingArgs`` mixin: ``class App(LoggingArgs, Cmd)``
    (data mixin first, executable base last). Both orders resolve the MRO
    correctly since ``LoggingArgs`` defines no ``__call__``; the recommended
    order reads "add logging to a command".
    """

    #: Own empty class-body-constants cache (P2): ``Cmd``'s body declares no
    #: real CLI fields (only ``_passthrough_`` and ``__call__``), so seeding
    #: this skips AST-parsing ``args.py`` for it. See ``Args._duho_constants_``.
    _duho_constants_: dict = {}

    #: argv captured after the first literal ``--`` separator (parse-time);
    #: an empty list when no ``--`` was present. Populated on the parsed
    #: instance by ``_initparser_``'s patched ``parse_known_args``.
    _passthrough_: "list[str]"

    def __call__(self):  # noqa: D401 - contract stub, overridden by subclasses
        """Run the command. Override ``__call__`` in a ``Cmd`` subclass.

        The base raises ``NotImplementedError`` naming the concrete class,
        so a ``Cmd`` that forgets to implement ``__call__`` fails loud when
        dispatched rather than silently doing nothing.
        """
        raise NotImplementedError(
            f"{type(self).__name__} is a Cmd but does not implement '__call__'"
        )


class Cli(Cmd):
    """Application-root layer: an opt-in mixin over ``Cmd``.

    A leaf ``Cmd`` is lean -- it declares its own CLI fields and a
    ``__call__``. A ``Cli`` root is the *top* of an app and additionally
    exposes the app-wide, sandwich-named configuration attributes a plain
    ``Cmd`` does not declare (``--version``, shell completion, a config
    file, a subcommand tree). Opt in by subclassing ``Cli``::

        class MyApp(LoggingArgs, Cli):
            _version_ = "1.2.3"
            _completion_ = True

    ``Cli`` adds **no new runtime behavior for *running*** -- it inherits
    ``Cmd.__call__`` unchanged (a data-only ``Cli`` that never overrides
    ``__call__`` still fails loud when dispatched, exactly like a ``Cmd``).
    What it adds is two things:

    1. **Typed, documented app-root class attrs.** Every one of these is
       already read elsewhere via ``getattr(cls, "_x_", default)``
       (``args.py``/``runtime.py``), so declaring them here changes no
       reader -- it only gives them a typed home and a class-level default
       where one exists. A plain ``Cmd`` leaves them undeclared; a ``Cli``
       root is where they belong.
    2. **Self-registration** (``_register_subcmd_`` / ``@subcommand``): a
       leaf command file can attach itself to the root's subcommand tree
       instead of the root listing every child centrally in
       ``_subcommands_``. The two mechanisms compose (union + dedup).

    ``LoggingArgs`` stays orthogonal (a separate data mixin) -- the
    batteries-included recipe is ``class MyApp(LoggingArgs, Cli)`` (data
    mixin first, executable/root base last), NOT a forced bundle. Every
    member ``Cli`` adds is sandwich-named or dunder, so a ``Cli`` subclass's
    field namespace stays 100% user-owned (annotated non-underscore attrs
    still become CLI fields).
    """

    #: Own empty class-body-constants cache (P2): every field ``Cli`` declares
    #: is sandwich-named (``_version_``, ``_completion_``, ...) and gets filtered
    #: out by ``get_clsargs`` anyway, so seeding this skips AST-parsing
    #: ``args.py`` for ``Cli``. See ``Args._duho_constants_``.
    _duho_constants_: dict = {}

    #: ``--version`` string, the ``AUTO`` sentinel (resolve via
    #: ``importlib.metadata``), or ``None`` for no ``--version`` flag. Read by
    #: ``_resolve_version`` (``args.py``).
    #:
    #: NOTE: every annotation on this class is written with ``typing.Union`` /
    #: ``typing.Optional`` and quoted, NEVER PEP-604 ``X | Y`` -- even sandwich-
    #: named fields are evaluated by ``typing.get_type_hints`` in
    #: ``_introspect.get_clsargs`` (before the ``_``-prefix filter drops them),
    #: so a ``|`` union would raise ``TypeError`` at parser-build time on 3.9.
    _version_: "_ty.Optional[_ty.Union[str, _AutoVersion]]" = None

    #: Distribution name override for ``_version_ = duho.AUTO`` when the import
    #: package differs from the PyPI distribution name. Read by
    #: ``_resolve_version``.
    _distribution_: "_ty.Optional[str]" = None

    #: When ``True``, inject ``--print-completion {bash,zsh,fish}`` on the
    #: top-level parser. Read by ``_initparser_`` (``args.py``); defaults off.
    _completion_: bool = False

    #: Path to a config file whose values become layered defaults (precedence
    #: CLI > env > config > class default). ``None`` disables it. A ``.json`` file
    #: is parsed as JSON, any other suffix (``.toml``/unspecified) as TOML. Read
    #: by ``_apply_default_layers`` (``args.py``) and ``duho.main``.
    _config_: "_ty.Optional[_ty.Union[str, _pathlib.Path]]" = None

    #: Optional custom config loader ``Callable[[Path], dict]`` (F7). When set it
    #: is used INSTEAD of duho's built-in JSON/TOML dispatch, so a user can plug a
    #: format duho does not ship (e.g. YAML via their own ``yaml.safe_load``)
    #: WITHOUT duho depending on it -- keeping the zero-runtime-deps contract.
    #: Read by ``_load_config`` (``args.py``) via ``_apply_default_layers`` /
    #: ``duho.app``.
    _config_loader_: "_ty.Optional[_ty.Callable[[_pathlib.Path], dict]]" = None

    #: Opt-in argparse help ``formatter_class`` (F8). ``None`` (default) uses
    #: argparse's plain formatter; set it to ``duho.DefaultsFormatter``,
    #: ``duho.ColorHelpFormatter``, ``duho.ColorDefaultsFormatter``, or any
    #: ``HelpFormatter`` subclass. Plumbed into ``formatter_class`` by
    #: ``Args._parser_`` (and inherited onto every subcommand parser).
    _help_formatter_: "_ty.Optional[type]" = None

    #: The static subcommand tree. ``None`` (the default) means "no declared
    #: subcommands"; self-registration lazily materializes a per-class list.
    #: Read via ``getattr(cls, "_subcommands_", None)`` (``args.py`` +
    #: ``runtime.py``) -- declaring it here does NOT change that contract.
    _subcommands_: "_ty.Optional[_ty.Sequence[_ty.Type[Cmd]]]" = None

    #: When ``True``, add the opt-in ``--help-agents`` flag (a detailed,
    #: machine-readable description of the whole CLI for AI agents). Read by
    #: ``_install_agent_help`` (``args.py``); defaults off. Independent of the
    #: always-on ``AGENT_HELP`` env-var trigger, which needs no opt-in.
    _agent_help_: bool = False

    #: Environment variable whose truthy value flips ``--help`` into agent mode.
    #: ``None`` (default) uses :data:`duho.agenthelp.DEFAULT_ENV` (``AGENT_HELP``).
    #: Read by ``_AgentHelpAction`` (``args.py``) via ``agent_help_requested``.
    _agent_help_env_: "_ty.Optional[str]" = None

    #: Optional examples surfaced in the agent-help document. A sequence of
    #: command strings, or of ``(command, description)`` pairs. ``None`` (default)
    #: lets duho synthesize a minimal invocation line. Read by
    #: ``duho.agenthelp`` when building the document.
    _examples_: "_ty.Optional[_ty.Sequence[_ty.Any]]" = None

    #: Optional exit-code overrides/additions for the agent-help document, as a
    #: ``{code: meaning}`` mapping merged over duho's defaults (0/1/2). ``None``
    #: (default) uses the defaults alone. Read by ``duho.agenthelp``.
    _exit_codes_: "_ty.Optional[_ty.Mapping[_ty.Any, str]]" = None

    @classmethod
    def _register_subcmd_(cls, child: "type[Cmd]") -> "type[Cmd]":
        """Attach ``child`` to THIS class's own ``_subcommands_`` tree.

        Appends ``child`` to a per-class list, materialized copy-on-write on
        first use: if ``_subcommands_`` is not set directly in ``vars(cls)``
        (i.e. it is unset or inherited from a parent ``Cli``), a fresh list
        is created -- never mutating a parent class's inherited list, so two
        ``Cli`` subclasses never cross-contaminate. An inherited
        ``_subcommands_`` seeds the fresh list (its children are kept, then
        ``child`` is added). Idempotent: if ``child`` is already present it
        is a no-op, so a child registered both statically (in a declared
        ``_subcommands_``) and via this API appears exactly once. Returns
        ``child`` so it can be used as a decorator.
        """
        if "_subcommands_" in vars(cls):
            current = cls._subcommands_
            existing = list(current) if current else []
        else:
            # Copy-on-write: seed from an inherited/unset value WITHOUT
            # mutating the parent's list.
            inherited = getattr(cls, "_subcommands_", None)
            existing = list(inherited) if inherited else []
        if child not in existing:
            existing.append(child)
        cls._subcommands_ = existing
        return child

    @classmethod
    def subcommand(cls, child: "type[Cmd]") -> "type[Cmd]":
        """Decorator form of :meth:`_register_subcmd_`.

        Lets a command file self-attach to the root::

            @MyApp.subcommand
            class Deploy(Cmd):
                ...

        Returns ``child`` unchanged, so the decorated class keeps its
        identity. Equivalent to calling ``MyApp._register_subcmd_(Deploy)``.
        """
        return cls._register_subcmd_(child)


def command(
    args_cls: "type[Args]",
    func: "_ty.Callable[[_ty.Any], object]",
    *,
    name: "str | None" = None,
) -> "type[Cmd]":
    """Build a ``Cmd`` subclass from a data ``Args`` class and a callable.

    Lets a user attach behavior to an existing data ``Args`` without
    rewriting it as a ``Cmd`` subclass ("build one from Args and a
    method"). The returned class subclasses BOTH ``args_cls`` (to inherit
    its declared fields / parsing machinery) and ``Cmd`` (for the
    executable contract). Its ``__call__`` calls ``func(self)`` -- the parsed
    instance IS the parsed args -- so ``command(MyArgs, f)`` makes ``f``
    receive the parsed ``MyArgs``-shaped instance and its return value becomes
    the command's result.

    ``name`` (optional) sets the built class's ``_parsername_`` (the
    subcommand name). When omitted, the usual
    ``_parsername_``/class-name rule applies to the generated class.
    """
    if Cmd in getattr(args_cls, "__mro__", ()):
        bases: tuple = (args_cls,)
    else:
        bases = (args_cls, Cmd)

    def __call__(self, _func=func):
        return _func(self)

    namespace: "dict[str, object]" = {"__call__": __call__}
    if name is not None:
        namespace["_parsername_"] = name

    cls_name = name or getattr(args_cls, "__name__", "Command")
    return _ty.cast("type[Cmd]", type(cls_name, bases, namespace))


def Extend(split: "str | _ty.Callable[[str], _ty.Iterable]", **kwargs):
    """Create a collection argument whose text is split on ``split`` first.

    ``list[str]``'s own default builder sets ``nargs="*"`` (so a plain list
    field accepts both ``--x a --x b`` and ``--x a b``); combined with a
    factory that SPLITS one token into several, that combination
    double-collects: argparse gathers ``nargs="*"`` tokens first and applies
    the factory to EACH ONE individually, so a token's split result (itself a
    list, e.g. ``"a,b"`` -> ``["a", "b"]``) would be appended as ONE nested
    element instead of being flattened -- ``--rcopts '!*,build'`` becoming
    ``[['!*', 'build']]``, not ``['!*', 'build']``. Explicitly overriding
    ``nargs=None`` here (a single string per flag occurrence, argparse's own
    default) avoids the double-collection.

    The split parts are mapped through the field's OWN element factory (so
    ``Arg[list[int], Extend(",")]`` yields ints, not strings) and fed to
    whichever collection action the field's declared type already uses --
    ``list``, ``set``, or ``tuple`` (A020) -- so this composes with a typed
    or non-list collection instead of forcing a stdlib list-only action. The
    field's own declared default (or the type ladder's empty collection when
    none is declared) is used as-is; it is NOT overridden here, so it is kept
    when the flag is absent and replaced -- like any other collection option
    -- on the first CLI occurrence (A007).
    """
    kwargs.setdefault("nargs", None)
    if isinstance(split, str):
        splitter: _ty.Callable[[str], list] = lambda x: x.split(split)  # type: ignore
    else:

        def splitter(text: str):
            result = split(text)
            if isinstance(result, list):
                return result
            return list(result)

    return _argparse.Namespace(split=splitter, kwargs=kwargs)


def Count(**kw):
    """Create a count-action argument (e.g. `-vvv` -> 3)."""
    return NS(action="count", kwargs=kw)


def Append(type: "Factory" = str, **kw):
    """Create an append-action argument, accumulating repeated flag values.

    Explicitly clears nargs: a bare `list`/`list[T]` annotation's implicit
    builder defaults to action="extend", nargs="*" (space-separated), which
    would make append() collect a *list* per occurrence instead of a scalar.
    """
    return NS(action="append", type=type, nargs=None, kwargs=kw)


def Const(value, **kw):
    """Create a store_const-action argument that stores `value` when present."""
    return NS(action="store_const", const=value, kwargs=kw)


def Choice(*choices, **kw):
    """Restrict an argument's accepted values to `choices`."""
    return NS(choices=tuple(choices), kwargs=kw)


class UpdateAction(_argparse.Action):
    """Action that merges dict occurrences, replacing any layered default on
    the first CLI occurrence (A005) -- the same "CLI wins" semantics
    `_CollectionAction` gives list/set/tuple fields.
    """

    def __call__(self, parser, namespace, values, option_string=None):  # type: ignore
        sidecar = "_duho_dict_seen_" + self.dest
        if not getattr(namespace, sidecar, False):
            # First CLI occurrence of THIS parse: start from an empty dict so
            # a class/env/config/instance default is REPLACED, not merged
            # onto (A005) -- matching list/set/tuple's own replace-then-
            # accumulate semantics.
            items: dict = {}
            setattr(namespace, sidecar, True)
        else:
            items = getattr(namespace, self.dest, None)
            items = dict(items) if items else {}
        if isinstance(values, (list, tuple)) and all(
            isinstance(v, _ty.Mapping) for v in values
        ):
            # NS(nargs="*") on a `dict[str, V]` field (A054): argparse passes
            # a LIST of one-pair dicts (one per space-separated KEY=VALUE
            # token, each already converted by the per-token `type=` factory)
            # rather than a single dict -- merge each in order instead of
            # handing the whole list to dict.update(), which raises. A plain
            # single dict (the ordinary nargs=None case), or any other
            # update()-compatible value a custom `type=` produces (e.g. a
            # list of `[key, value]` pairs, as `examples/fileinstall.py`
            # does), is NOT a list of Mappings and falls through unchanged.
            for one in values:
                items.update(one)
        else:
            # A ``None`` starting value (an explicit ``= None`` default) is
            # already normalized to ``{}`` above; a single dict occurrence
            # (F1) merges directly.
            items.update(values or {})
        setattr(namespace, self.dest, items)


def print_completion(cls, shell: str, file=None) -> None:
    """Print a shell completion script for `cls` to `file` (default sys.stdout).

    ``shell`` is one of ``"bash"``, ``"zsh"``, ``"fish"``, or ``"powershell"``.
    Standalone counterpart to the `--print-completion` flag injected when
    `_completion_ = True` -- builds cls's parser tree fresh (independent of
    whether `_completion_` is set) and delegates to `duho.completion.<shell>`.
    """
    from . import completion as _completion

    if file is None:
        file = _sys.stdout
    parser = cls._parser_()
    emitter = getattr(_completion, shell)
    file.write(emitter(parser))


def print_agent_help(cls, file=None) -> None:
    """Print a detailed, machine-readable (JSON) agent-help document for `cls`.

    Standalone counterpart to the ``--help-agents`` flag / the ``AGENT_HELP``
    env-var trigger: builds ``cls``'s parser tree fresh and describes it
    (independent of whether either trigger is wired up), then writes the JSON to
    ``file`` (default ``sys.stdout``). Delegates to
    :func:`duho.agenthelp.print_agent_help`.
    """
    from . import agenthelp as _agenthelp

    _agenthelp.print_agent_help(cls, file=file)


def _maybe_await(result):
    """Drive a coroutine result to completion, returning its value (F4).

    A ``Cmd.__call__`` (or a ``duho.main`` target) may be ``async def``; its
    invocation returns a coroutine. This runs it with ``asyncio.run`` at the
    call site so the awaited value becomes the command's result/exit code, and
    passes any non-coroutine result through unchanged.

    ``asyncio`` is imported lazily here (not at module top) so a plain
    ``import duho`` never pays its import cost -- only a command that actually
    returns a coroutine triggers the load (startup budget, plan 02).
    """
    import inspect as _stdlib_inspect

    if _stdlib_inspect.iscoroutine(result):
        import asyncio as _asyncio

        return _asyncio.run(result)
    return result


def main(
    cls,
    argv: "_ty.Sequence[str] | None" = None,
    *,
    setup_logging=True,
    config: "str | _pathlib.Path | None" = None,
) -> int:
    """Build a parser for cls, parse argv, and dispatch the selected Cmd.

    Module-level (not a classmethod) so the Args subclass namespace stays
    entirely user-owned. Steps: build parser (auto-registers _subcommands_),
    apply the env/config/class-default layers (`config` overrides `cls._config_`;
    precedence CLI > env > config > class default), parse argv (SystemExit from
    argparse propagates), optionally set up stderr logging + apply verbosity
    when the resulting instance provides _set_loglevels_, then run the command
    and map a None return to 0.

    Since Plan 13's Args/Cmd split, dispatch expects the selected class to be
    a ``Cmd`` (executable, defines ``__call__``). A bare data ``Args`` -- with
    no ``__call__`` -- raises a clear ``NotImplementedError`` ("Args holds data;
    make it a Cmd to run it") rather than silently doing nothing.
    """
    parser = cls._parser_()
    _apply_default_layers(parser, cls, config)
    instance = parser.parse_args(argv)

    if setup_logging and hasattr(instance, "_set_loglevels_"):
        root = _logging_module.getLogger()
        if not root.handlers:
            _duho_logging.init_stderr_logging()
        instance._set_loglevels_()

    run = getattr(instance, "__call__", None)
    if run is None:
        raise NotImplementedError(
            f"{type(instance).__name__} holds data but is not runnable "
            f"(no '__call__'); make it a Cmd (subclass duho.Cmd or "
            f"build one with duho.command(...)) to run it"
        )

    result = _maybe_await(run())
    return 0 if result is None else result


def parse(
    spec,
    argv: "_ty.Sequence[str] | None" = None,
    *,
    parser_kwargs=None,
    config: "str | _pathlib.Path | None" = None,
):
    """Build a parser from `spec` and parse `argv` into a new instance.

    `spec` may be:
    - An `Args` subclass (type): equivalent to `spec._parser_().parse_args(argv)`,
      with the env/config/class-default layers applied first (see below).
    - An instance of an `Args` subclass: the instance's current field values
      are used as argparse defaults (via `parser.set_defaults(**overrides)`,
      filtered to actual CLI fields -- not `vars(spec)`, which would include
      framework attrs). CLI args still override those defaults. Returns a
      NEW instance of `type(spec)`; `spec` itself is never mutated.

    `config` (a path, or None to fall back to `cls._config_`) layers config-file
    and environment-variable defaults under the instance/CLI ones. Full
    precedence: CLI args > instance field values > env > config file > class
    defaults. Note this means a required field (no class default) that is
    supplied by *any* layer becomes effectively optional for this call.
    """
    parser_kwargs = parser_kwargs or {}
    if isinstance(spec, type):
        cls = spec
        parser = cls._parser_(**parser_kwargs)
        _apply_default_layers(parser, cls, config)
        return parser.parse_args(argv)

    cls = type(spec)
    parser = cls._parser_(**parser_kwargs)
    _apply_default_layers(parser, cls, config)
    field_names = {builder.name for builder in cls._getargs_()}
    overrides = {
        name: value for name, value in vars(spec).items() if name in field_names
    }
    parser.set_defaults(**overrides)
    # set_defaults() alone doesn't satisfy argparse's required= check (it's
    # enforced independently of the default value) -- an instance-supplied
    # value for a field that has no class default (required=True) must also
    # clear the action's required flag, or parse_args([]) still raises
    # SystemExit even though a usable value is now present via the default.
    for action in parser._actions:
        if action.dest in overrides:
            action.required = False
    return parser.parse_args(argv)


def parse_globals(cls, argv: "_ty.Sequence[str] | None" = None, **parser_kwargs):
    """Parse ONLY a root command's global args, ignoring/relaxing subcommands.

    Builds ``cls``'s root parser (``cls._parser_(**parser_kwargs)``) and parses
    ``argv`` with help suppressed and subcommand validation relaxed, so a
    consumer can resolve config-file-driven command search paths (or any other
    global) BEFORE building/committing to the full subcommand parser. This is
    the documented, public form of the internal prepass ``duho.app`` already
    runs -- it wraps :func:`duho.parsers.prerun_parse` verbatim rather than
    reimplementing the ``_HelpAction``/subparser patching (which
    ``prerun_parse`` performs and restores in a ``finally``).

    Returns the parsed root instance with globals set. Subcommand arguments are
    NOT validated in this pass: a missing subcommand does not error, and an
    unknown trailing token does not crash the globals parse (it is simply
    ignored here). A caller that also wants the leftover argv should call
    ``parser.parse_known_args`` directly -- ``parse_globals`` deliberately
    returns a single value (the globals-only instance), mirroring the shape
    ``prerun_parse`` yields.

    ``**parser_kwargs`` are forwarded to ``cls._parser_`` (e.g. ``add_help=False``),
    mirroring :func:`duho.parser`.
    """
    from .parsers import prerun_parse as _prerun_parse

    parser = cls._parser_(**parser_kwargs)
    # A globals-only parse must not descend into the subcommand tree. Building
    # cls._parser_() materializes any ``_subcommands_`` as a real subparsers
    # action; leaving it in place makes even a globals-only ``prerun_parse``
    # re-enter the root parser's patched ``parse_known_args`` for any trailing
    # token (a subcommand name OR an unknown flag after the globals), which
    # double-pops the internal "#cls" marker and raises KeyError. Dropping the
    # subparsers action makes trailing tokens plain unrecognized extras (which
    # ``prerun_parse`` discards) -- the same shape ``duho.app``'s prepass gets by
    # running before it adds subparsers.
    for action in list(parser._actions):
        if isinstance(action, _argparse._SubParsersAction):
            parser._actions.remove(action)
            subparsers_group = getattr(parser, "_subparsers", None)
            if subparsers_group is not None and action in subparsers_group._actions:
                subparsers_group._actions.remove(action)
    return _prerun_parse(parser, argv)


def value_sources(parsed) -> "dict[str, str]":
    """Report the origin layer ("cli", "env", "config", or "default") of each
    field on a parsed instance produced by `duho.parse`/`duho.main`.

    Looks up the owning parser via the per-class `_duho_last_parser_`
    linkage stashed during dispatch (see `_initparser_`). Returns `{}` if
    unavailable (e.g. the instance wasn't produced via a parser built by
    this framework, or no parse has happened yet for its class).

    A field is "cli" if its parsed value differs from the effective default
    that was in effect for that parse -- the merged env/config value when
    the field was touched by one of those layers, else the class default.
    Otherwise it's whatever layer contributed that default ("env"/"config"),
    or "default" if no layer touched it (value == the untouched class default).
    """
    parser = getattr(type(parsed), "_duho_last_parser_", None)
    if parser is None:
        return {}
    sources: "dict[str, str]" = getattr(parser, "_duho_value_sources_", None) or {}
    merged: "dict[str, object]" = getattr(parser, "_duho_merged_defaults_", None) or {}

    result: "dict[str, str]" = {}
    for builder in type(parsed)._getargs_():
        name = builder.name
        if not hasattr(parsed, name):
            continue
        value = getattr(parsed, name)
        if name in merged:
            effective_default = merged[name]
            layer = sources.get(name, "default")
        else:
            # Use the builder's EFFECTIVE default (e.g. False for an undeclared
            # store_true bool), not the raw declared default (NOT_DEFINED), or a
            # field left at its argparse default is mislabeled "cli" (C14).
            effective_default = builder._effective_default_()
            layer = "default"
        result[name] = layer if value == effective_default else "cli"
    return result


__all__ = [
    "Append",
    "Argument",
    "ArgumentBuilder",
    "ArgumentMeta",
    "Args",
    "Arg",
    "Choice",
    "Cli",
    "Cmd",
    "command",
    "Const",
    "Count",
    "Extend",
    "Factory",
    "main",
    "Meta",
    "NS",
    "NOT_DEFINED",
    "parse",
    "parse_globals",
    "print_agent_help",
    "print_completion",
    "UpdateAction",
    "value_sources",
]
