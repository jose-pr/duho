"""The type -> argparse-spec ladder: one annotation, resolved once.

:func:`_factory_for` is the single dispatch ladder shared by a top-level
field, every Union member, and every collection element/dict value:
``Literal``, ``Enum``, ``list``/``set``/``frozenset``/``tuple[T, ...]``,
``dict[str, V]``, the ISO-format date/datetime/time types, ``Union``/
``Optional``, and a plain/custom scalar fallthrough. Each branch returns a
:class:`_FieldSpec` -- the one small namedtuple bundling everything that
branch determines about the field (its text-to-value ``factory``, argparse
``choices``, ``metavar``, ``action``, ``nargs``, empty ``default``, and
``collection`` type).

Split out of ``args.py``: this ladder, and the env/config layering
pipeline in ``_layers.py``, are the two subsystems ``duho.args`` itself and
``duho.mcp`` both depend on -- giving each its own small module (instead of
reaching into a single ~4000-line file for a handful of private names) is
what actually shrinks that file's maintainability problem. Public names
(``Factory``, ``UpdateAction``) are re-exported from ``duho.args`` unchanged,
so every existing import path keeps working.
"""

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

    argparse's ``_get_value`` only preserves a type function's own message for
    ``ArgumentTypeError``; a plain ``ValueError``/``TypeError`` is replaced
    with a generic ``invalid <type> value: ...`` that discards whatever
    detail the factory raised -- so a crafted "choose from ..."/"expected
    KEY=VALUE" message never reached the user. Subclassing
    ``ValueError`` too means every existing ``except (TypeError, ValueError)``
    catch (the Union/Literal try-loops, the env/config layers) keeps working
    unchanged.
    """


class _LayeredChoiceError(ValueError):
    """A layered (env/config) value fails its field's ``choices`` membership
    check.

    Deliberately does NOT embed the offending value in its message the way
    a CLI ``_ConversionError`` does -- an env var or config value can be
    secret, and this error's text reaches the layering pipeline's own
    redaction (``_layers.py``), which otherwise always collapses a
    conversion failure to a generic "expected <type>" (to avoid echoing a
    raw secret back). Carrying ``choices`` separately lets that redaction
    show the SAME "invalid choice" wording the CLI gives -- just never the
    value.
    """

    def __init__(self, choices) -> None:
        self.choices = choices
        super().__init__(
            f"invalid choice (choose from {', '.join(map(repr, choices))})"
        )


def _bool_from_text(text, /):
    """Strict CLI-text-to-bool factory.

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


def _enum_name_factory(enum_cls: type) -> "Factory":
    """Build a factory that resolves CLI text to an enum member by NAME.

    Validates against ``enum_cls.__members__`` rather than iterating
    the enum: iteration skips ALIASES (a second name for the same value) and,
    since Python 3.11, skips multi-bit ``Flag`` composite members too, so a
    declaration that worked on the 3.9 floor could reject a composite name on
    the ceiling. ``__members__`` includes both on every supported version.
    The "choose from" text still lists only the canonical (non-alias) names
    from iteration, matching the metavar built alongside this factory.

    Raises :class:`_ConversionError` (a ``ValueError`` subclass) so
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
    # Named after the enum, not left as the generic "_factory" a conversion
    # error's "invalid <type> value"/"expected <type>" text would otherwise
    # show (argparse, and duho's own layered-value redaction, both read a
    # factory's `__name__` for that -- see `_field_type_desc`).
    _factory.__name__ = enum_cls.__name__
    return _factory


class _CollectionAction(_argparse.Action):
    """Extend-and-coerce action for ``list``/``set``/``tuple`` collection
    fields.

    argparse's built-in ``extend`` action only extends a *list* and starts
    from whatever is already on the namespace (a layered default), so a CLI
    occurrence merges onto it instead of replacing it. This action instead
    starts its sidecar EMPTY on the first call of a parse, so the first CLI
    occurrence always REPLACES a class/env/config/instance default -- the
    same "CLI wins" semantics for every collection kind -- and
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
            # tokens were consumed.
            from ._layers import _LayeredDefault  # lazy: avoids a circular

            # import (`_layers` imports this module's `_CollectionAction`/
            # `UpdateAction` at module scope to classify actions for env/
            # config layering).
            if isinstance(values, _LayeredDefault):
                # `values` is a not-yet-converted env/config/instance
                # placeholder, not a real collection default -- pass it
                # through UNCHANGED so `_finalize_layers` still sees the
                # exact same object (`is`, not `==`) and converts it; wrapping
                # it in `self._collection_(...)` here would either raise
                # (the placeholder isn't iterable) or silently discard it.
                setattr(namespace, self.dest, values)
                return
            # A REAL collection default (no layer touched this field): coerce
            # a FRESH one from it instead of treating it as a user-supplied
            # value -- otherwise a `set` default crashes (`set([<the default
            # set>])`, unhashable) and a `list` default gets doubled. The
            # fresh coercion also means the returned instance never aliases
            # the action's default object, so a later mutation can't leak
            # into a future parse.
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


def _collection_action(collection: type) -> "type[_argparse.Action]":
    """Build a ``_CollectionAction`` subclass bound to a target collection."""

    class _BoundCollectionAction(_CollectionAction):
        _collection_ = collection

    return _BoundCollectionAction


class _AppendAction(_argparse.Action):
    """``duho.Append()``'s action: one scalar value per flag occurrence,
    accumulated into a *list*.

    argparse's own stdlib ``"append"`` action starts from whatever is
    ALREADY on the namespace -- a class/env/config/instance default -- so
    the FIRST CLI occurrence merges onto it instead of replacing it, unlike
    every other collection action duho builds (see :class:`_CollectionAction`,
    which exists for exactly this reason). This mirrors that same fix: the
    running list lives on a private per-parse sidecar
    (``_duho_items_<dest>``), never read back off ``namespace.<dest>``
    itself, so the first occurrence always starts a FRESH list -- a layered
    default is replaced, not appended to -- and later occurrences accumulate
    onto that same list.
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
    """A bool flag whose OWN declared spelling already reads as a negation
    (``no_verify`` -> ``--no-verify``), given a way back to ``False`` from
    the CLI when a layer (env/config) can supply ``True``.

    ``argparse.BooleanOptionalAction`` cannot do this: it refuses ANY
    ``--no-``-prefixed option string outright (3.9-3.14+ alike), so it is
    never reachable for this shape (see ``ArgumentBuilder.add_to_parser``,
    the only caller). This does the same job by hand, as ONE action
    carrying BOTH the field's own originally-declared flags (``negative`` --
    presence sets ``True``, this field's own honest "on" spelling) and an
    EXTRA, stripped positive-sense counterpart argparse also registers
    under the SAME dest (presence sets ``False``) -- never a SECOND action:
    duho's env/config layering pipeline keys everything off exactly one
    action per dest (``_layers.py``'s several ``{action.dest: action for
    action in parser._actions}`` maps would otherwise silently pick
    whichever action happens to be LAST for that dest).

    Never reads back whatever is already on ``namespace.<dest>`` -- it
    always overwrites outright from ``option_string`` alone -- so it is
    safe against a not-yet-converted ``_LayeredDefault`` placeholder the
    same way a plain ``store_true``/``store_false`` is (see
    ``_layers._REPLACE_SEMANTICS_ACTION_TYPES``, where it is listed).
    """

    def __init__(self, option_strings, dest, negative, **kwargs):
        self._duho_negative_ = frozenset(negative)
        kwargs["nargs"] = 0  # a flag, like store_true/store_false -- no value
        super().__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, option_string in self._duho_negative_)


def _split_kv(text: str, name: str) -> "tuple[str, str]":
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

    def __init__(self, name: str, value_factory: "Factory"):
        self.name = name
        self.value_factory = value_factory

    def __call__(self, text: str) -> dict:
        key, value = _split_kv(text, self.name)
        try:
            converted = self.value_factory(value)
        except (TypeError, ValueError):
            # Never let argparse fall back to its own generic message here:
            # `self.value_factory` is an internal callable (a bound method, a
            # closure) with no useful `__name__` of its own, so that fallback
            # showed its raw object repr (`invalid <_KVFactory object at
            # 0x...> value`) instead of naming the field and the value type
            # the way every other conversion error does.
            type_name = getattr(self.value_factory, "__name__", None) or "value"
            raise _ConversionError(
                f"argument {self.name!r}: value {value!r} for key {key!r} "
                f"is not a valid {type_name}"
            ) from None
        return {key: converted}


def _isoformat_factory(cls: type) -> "Factory":
    """Build the ``fromisoformat`` factory for a date/datetime/time `cls`.

    Before Python 3.11, ``fromisoformat`` only accepts its OWN ``isoformat()``
    output: no trailing ``Z`` (RFC 3339's UTC marker, and the form most tools
    emit) and no basic ``YYYYMMDD`` format. duho.mcp advertises
    ``format: date-time`` (RFC 3339) on every version regardless, so a
    schema-valid MCP call could fail on the 3.9 floor. This rewrites a
    trailing ``Z``/``z`` before delegating to ``fromisoformat`` -- on EVERY
    version, not only <3.11: 3.11+'s own ``fromisoformat`` accepts an
    uppercase ``Z`` natively but rejects a lowercase ``z``, and RFC 3339
    treats the two as equivalent, so leaving the floor's shim as the only
    place that normalized case made ``--field ...z`` behave differently
    depending on which Python duho happened to run on. Basic (no-dash)
    formats stay unsupported on every version -- out of scope here.
    """
    accepts_z_natively = _sys.version_info >= (3, 11)

    def _factory(text: str, /, _cls=cls, _accepts_z=accepts_z_natively):
        # A non-str `text` (a native TOML/JSON date/datetime object passed
        # through the env/config layer) has no `.endswith` -- let
        # `fromisoformat` itself reject it (a TypeError, caught by
        # `_convert_non_str`'s fallback the same way this used to on 3.11+
        # too) instead of crashing here with an unrelated AttributeError.
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


def _scalar_spec(factory=None) -> "_FieldSpec":
    return _FieldSpec(factory, None, None, None, None, NOT_DEFINED, None)


def _literal_spec(args: tuple) -> "_FieldSpec":
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
            factory: "Factory" = _bool_from_text
        elif isinstance(lit_ty, type) and issubclass(lit_ty, _enum.Enum):
            # A Literal of specific Enum MEMBERS (as opposed to a bare Enum
            # annotation, handled by `_enum_spec`) previously fell through to
            # `factory = lit_ty` -- the enum CLASS itself, which looks members
            # up by VALUE (`Color("RED")`), not by name, so a "choose from"
            # name that argparse's own metavar/choices already advertised
            # (`{Color.RED,Color.BLUE}`, from `repr()`-ing the raw members)
            # was rejected outright. Resolve by NAME instead, scoped to only
            # the members THIS Literal actually lists (a subset is allowed),
            # matching `_enum_spec`'s own by-name convention.
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

        if bool in literal_types:
            # A mixed-type Literal that ALSO accepts bool (e.g.
            # ``Literal[True, "auto"]``) must still accept a native bool from
            # an env/config layer -- `ArgumentBuilder._convert_non_str` reads
            # this attribute to widen a raw bool for a composite factory it
            # doesn't otherwise recognize by identity.
            factory._duho_union_bool_ok_ = True  # type: ignore[attr-defined]

    return _FieldSpec(factory, tuple(args), metavar, None, None, NOT_DEFINED, None)


def _union_spec(members: "list", name: str) -> "_FieldSpec":
    """Spec for a Union of ``members`` (``None`` already stripped).

    Each member is resolved through :func:`_factory_for` so a member like
    ``list[int]`` or ``Literal[...]`` gets its full spec. A single remaining
    member (an ``Optional[T]``) adopts T's ENTIRE spec -- element conversion,
    action, choices, default (this is also why a bare/``Optional`` ``bool``
    keeps `store_true`/`BooleanOptionalAction`: its single-member spec's
    factory stays the raw ``bool`` builtin, untouched below). A multi-member
    union composes the member factories in declaration order (the
    enum-by-name rule preserved), but rejects any member that needs a special
    ``action`` (a collection): argparse cannot switch actions per value within
    one option. Within that multi-member composition, a raw ``bool`` member
    is routed through the strict :func:`_bool_from_text` and any
    member carrying ``choices`` (e.g. a Literal) is membership-checked before
    the try-loop can silently accept a value only because a LATER member's
    bare type conversion happens not to raise -- a union field never
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

    # For the error message only: the ORIGINAL annotation types (`int`,
    # `str`, ...), not `_factories` -- those are the resolved conversion
    # CALLABLES (bound closures, `_bool_from_text`, a `_choice_checked`
    # wrapper), whose `repr()` is an unreadable
    # `<function ... at 0x...>` rather than the member type the user wrote.
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

    # Named after the union's own members, not left as the generic
    # "factory" a layered conversion-failure message's "expected <type>"
    # text would otherwise show (see `_field_type_desc`) -- an unreadable
    # internal name, not the "int or float" a user actually declared.
    factory.__name__ = " or ".join(_member_names)

    if bool in members:
        # A multi-member Union that ALSO accepts bool (e.g.
        # ``Union[bool, int]``) must still accept a native bool from an
        # env/config layer -- `ArgumentBuilder._convert_non_str` reads this
        # attribute to widen a raw bool for a composite factory it doesn't
        # otherwise recognize by identity.
        factory._duho_union_bool_ok_ = True  # type: ignore[attr-defined]

    return _scalar_spec(factory)


def _enum_spec(tp: type) -> "_FieldSpec":
    """Spec for an ``enum.Enum`` annotation: a choose-by-name factory
    plus a ``{member,...}`` metavar built from the same canonical names."""
    names = tuple(member.name for member in tp)
    metavar = "{" + ",".join(names) + "}"
    return _FieldSpec(
        _enum_name_factory(tp), None, metavar, None, None, NOT_DEFINED, None
    )


def _dict_spec(key_ty, val_ty, name: str) -> "_FieldSpec":
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
    val_factory, _val_choices, _val_metavar = _element_spec(val_ty, name, "dict value")
    return _FieldSpec(
        _KVFactory(name, val_factory), None, "KEY=VALUE", UpdateAction, None, {}, dict
    )


def _sequence_spec(
    collection: type, elem_ty, name: str, what: str, default
) -> "_FieldSpec":
    """Spec for a homogeneous ``list``/``set``/``frozenset``/variadic
    ``tuple[T, ...]`` annotation.

    ``list``/``set``/``frozenset``/``tuple`` differ only in their collection
    type, its empty default value, and the :class:`_CollectionAction`
    subclass bound to it -- the element factory/choices/metavar ladder and
    ``"*"`` nargs are identical across all four, so this is the one place
    that wiring is written.
    """
    factory, choices, metavar = _element_spec(elem_ty, name, what)
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
    """Unwrap a PEP 695 ``type X = ...`` alias (3.12+) or a ``typing.NewType``
    down to the real type it describes, looping so a chain of aliases
    resolves fully.

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
    ladder a top-level field uses, so an enum element matches by
    name, a date element parses ISO text, a bool element parses strictly,
    and a Literal element carries its own membership check. Raises a
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
    if factory is bool:
        # `elem_ty is bool` alone misses an `Optional[bool]` element: its
        # single-member Union spec RESOLVES to the raw `bool` builtin (see
        # `_union_spec`'s single-member branch) without `elem_ty` itself
        # ever being literally `bool`. Checking the RESOLVED factory instead
        # catches that case too, so `list[Optional[bool]]`/`dict[str,
        # Optional[bool]]` reject "false" the same strict way a bare
        # `bool` element does, instead of silently truthy-casting the text.
        factory = _bool_from_text
    if spec.choices is not None:
        factory = _choice_checked(factory, spec.choices)
    return factory, spec.choices, spec.metavar


def _factory_for(tp, name: str) -> "_FieldSpec":
    """Resolve a single annotation type to its :class:`_FieldSpec`.

    The one dispatch ladder shared by the top-level field, every Union
    member, and every collection element/dict value. Ordering
    matches the historical branch order (Literal, Enum, list, set, frozenset,
    tuple, dict, iso-date, Union, fallthrough). A plain/custom type returns
    ``factory=None`` so the caller keeps whatever factory it seeded (e.g. a
    ``duho.Argument.from_type`` custom factory) -- EXCEPT when `tp` was
    itself a type alias/NewType that had to be unwrapped to reach that plain
    type, since the caller's seeded factory is the un-unwrapped original,
    which is not usable as-is.
    """
    original_tp = tp
    tp = _unwrap_type_alias(tp)
    origin = _ty.get_origin(tp)
    args = _ty.get_args(tp)

    if origin is _ty.Annotated:
        # A nested Annotated/Arg[...] Union member (e.g.
        # `Optional[Arg[int, NS(env=...)]]`) previously crashed later at the
        # unhashable-metadata isoformat lookup, or silently dropped its
        # metadata. Reject it loudly instead.
        raise ValueError(
            f"argument {name!r}: a nested Annotated/Arg[...] type {tp!r} is "
            f"not supported inside a Union; put the metadata on the OUTER "
            f"annotation instead (e.g. Arg[Optional[T], ...])"
        )

    if origin is _ty.Literal:
        return _literal_spec(args)

    if isinstance(tp, type) and issubclass(tp, _enum.Enum):
        return _enum_spec(tp)

    if origin is list or tp is list:
        elem_ty = args[0] if args else str
        return _sequence_spec(list, elem_ty, name, "list", [])

    if origin is set or tp is set:
        elem_ty = args[0] if args else str
        return _sequence_spec(set, elem_ty, name, "set", set())

    if origin is frozenset or tp is frozenset:
        elem_ty = args[0] if args else str
        return _sequence_spec(frozenset, elem_ty, name, "frozenset", frozenset())

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
        return _sequence_spec(tuple, elem_ty, name, "tuple", ())

    if origin is dict or tp is dict:
        # ``dict[K, V]`` -- ``KEY=VALUE`` tokens merged via ``UpdateAction``.
        # Bare ``dict`` == ``dict[str, str]``.
        key_ty = args[0] if args else str
        val_ty = args[1] if len(args) > 1 else str
        return _dict_spec(key_ty, val_ty, name)

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
        return _union_spec(non_none, name)

    if origin is not None:
        # Some other subscripted generic this ladder doesn't know how to
        # build a factory for (frozenset is handled above; this catches
        # things like Sequence[str]/Iterable[str], which previously fell
        # through to calling the raw typing alias on every value -- failing
        # per-value at PARSE time instead of once at build time, or (for a
        # bare `frozenset`) silently splitting text into characters).
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
        # real, callable, unwrapped type instead of `None`.
        return _scalar_spec(tp)

    return _scalar_spec(None)


class UpdateAction(_argparse.Action):
    """Action that merges dict occurrences, replacing any layered default on
    the first CLI occurrence -- the same "CLI wins" semantics
    `_CollectionAction` gives list/set/tuple fields.
    """

    def __call__(self, parser, namespace, values, option_string=None):  # type: ignore
        sidecar = "_duho_dict_seen_" + self.dest
        if not getattr(namespace, sidecar, False):
            # First CLI occurrence of THIS parse: start from an empty dict so
            # a class/env/config/instance default is REPLACED, not merged
            # onto -- matching list/set/tuple's own replace-then-
            # accumulate semantics.
            items: dict = {}
            setattr(namespace, sidecar, True)
        else:
            items = getattr(namespace, self.dest, None)
            items = dict(items) if items else {}
        if isinstance(values, (list, tuple)) and all(
            isinstance(v, _ty.Mapping) for v in values
        ):
            # NS(nargs="*") on a `dict[str, V]` field: argparse passes
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
            # merges directly.
            items.update(values or {})
        setattr(namespace, self.dest, items)
