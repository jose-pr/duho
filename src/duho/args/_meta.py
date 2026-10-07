from __future__ import annotations

import argparse as _argparse
import dataclasses as _dataclasses
import inspect as _inspect
import typing as _ty

from .. import _introspect as _introspect
from .._fieldspec import Factory as Factory

if _ty.TYPE_CHECKING:
    from ._argsclass import Args
    from ._cmd import Cmd


NOT_DEFINED = _introspect.NOT_DEFINED

_NONETYPE = type(None)

_T = _ty.TypeVar("_T")

if _ty.TYPE_CHECKING:
    from typing_extensions import Self as _Self  # type: ignore
else:
    # A type checker reads `Self`; at run time a TypeVar stands in so an
    # annotation that names it resolves with `typing.get_type_hints`.
    _Self = _ty.TypeVar("_Self")


if _ty.TYPE_CHECKING:
    from typing_extensions import Unpack as _Unpack
else:
    # `typing.Unpack` from 3.11; before that a stand-in, so an annotation that
    # names it still resolves with `typing.get_type_hints`.
    _Unpack = getattr(_ty, "Unpack", None)
    if _Unpack is None:

        class _Unpack:
            def __class_getitem__(cls, item: object) -> object:
                return item


class InitParserKwargs(_ty.TypedDict, total=False):
    """The keywords ``_parser_`` passes to ``_initparser_``, every one optional.

    Annotate an override's ``**kwargs`` with ``Unpack[InitParserKwargs]`` and
    forward them to ``super()._initparser_(parser, **kwargs)``. The base method
    is where they end up: it raises ``TypeError`` for a key that is not listed
    here and uses each key's default when it is missing.
    """

    #: The parser is a subparser: no ``--`` split, no completion flag.
    is_subcommand: bool
    #: Dests the parser already has from ``parents=``; reusing one is deliberate.
    parent_dests: _ty.Optional[_ty.FrozenSet[str]]
    #: Accepted and unused.
    explicit_prog: bool
    #: The root class agent help describes, on every node of its tree.
    agent_root_cls: _ty.Optional[type]
    #: A config table will reach this class although it declares no ``_config_``.
    external_config: bool


class _Parser(_argparse.ArgumentParser, _ty.Generic[_T]):
    """The parser ``_parser_`` returns, as a type checker sees it: an
    ``ArgumentParser`` whose ``parse_args``/``parse_known_args`` return ``_T``
    (the constructed ``Args``/``Cmd`` instance), not a bare ``Namespace``.

    Never instantiated: every real parser is a plain
    ``argparse.ArgumentParser`` with ``parse_known_args`` patched in place
    (see ``Args._initparser_``). It is a real class only so the public
    annotations that name it resolve with ``typing.get_type_hints``.
    """

    def parse_args(self, args=None, namespace: _T | None = None) -> _T:  # type: ignore
        raise NotImplementedError()

    def parse_known_args(  # type: ignore
        self, args=None, namespace: _T | None = None
    ) -> tuple[_T, list[str]]:
        raise NotImplementedError()


_type = type


#: Bound to :class:`Args`: lets ``duho.parse``/``duho.parse_globals`` return the
#: caller's own subclass instead of erasing it to ``Args``/``Any``.
_A = _ty.TypeVar("_A", bound="Args")

#: Bound to ``type[Cmd]``: lets ``Cli.subcommand``/``@Cli.subcommand`` and
#: ``Cli._register_subcmd_`` return the decorated CLASS's own type instead of
#: widening it to ``type[Cmd]``.
_C = _ty.TypeVar("_C", bound="type[Cmd]")


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


if _ty.TYPE_CHECKING:
    from typing_extensions import dataclass_transform as _dataclass_transform
else:

    def _dataclass_transform(**_options: object):
        """Run-time stand-in: only a type checker reads the real decorator."""
        return lambda obj: obj


@_dataclass_transform(kw_only_default=True)
def _keyword_only_fields(cls: type) -> type:
    """Make ``cls`` a dataclass whose constructor takes its annotated fields by keyword only.

    Every field is optional and defaults to :data:`_META_UNSET`. A positional
    argument or an unknown name is a ``TypeError``; ``inspect.signature`` shows
    the real keyword-only parameters.
    """
    cls = _dataclasses.dataclass(init=False)(cls)
    names = tuple(field.name for field in _dataclasses.fields(cls))

    def __init__(self, *args: _ty.Any, **kwargs: _ty.Any) -> None:
        if args:
            first = args[0]
            hint = (
                "; flags go in flags=(...)"
                if isinstance(first, str) and first.startswith("-")
                else ""
            )
            raise TypeError(
                f"{cls.__name__} takes keyword arguments only, "
                f"got {len(args)} positional{hint}"
            )
        if "dest" in kwargs:
            # `dest` is the one key `Meta` deliberately never accepts.
            raise TypeError(
                f"{cls.__name__} has no 'dest' field: "
                "an argument's dest is always its field name"
            )
        for key in kwargs:
            if key not in names:
                import difflib as _difflib

                close = _difflib.get_close_matches(key, names, n=1)
                hint = f"; did you mean {close[0]!r}?" if close else ""
                raise TypeError(f"{cls.__name__} has no field {key!r}{hint}")
        for name in names:
            setattr(self, name, kwargs.get(name, _META_UNSET))

    __init__.__qualname__ = f"{cls.__qualname__}.__init__"
    cls.__init__ = __init__  # type: ignore[method-assign]
    cls.__signature__ = _inspect.Signature(  # type: ignore[attr-defined]
        [
            _inspect.Parameter(
                name,
                _inspect.Parameter.KEYWORD_ONLY,
                default=_META_UNSET,
                annotation=_ty.Any,
            )
            for name in names
        ]
    )
    return cls


@_keyword_only_fields
class Meta:
    """Typed, typo-safe field metadata: ``Arg[int, Meta(help="...")]``.

    ``Meta`` takes keyword arguments only, each one a documented metadata
    field; an unknown name (``Meta(hlep="oops")``) is a ``TypeError``, raised
    as soon as the annotation is evaluated: at class-definition time on Python
    3.9-3.13 with eager annotations, or at first parser build on 3.14+ (PEP
    649), under string annotations, or with ``from __future__ import
    annotations``. Only the fields you set are merged (each defaults to a
    private sentinel), so an unset field never overrides a type-derived value::

        level: Arg[int, Meta(help="verbosity", env="LEVEL")] = 0
        ("--level",)

    The ``kwargs`` field is the raw ``add_argument`` escape hatch. A plain
    ``dict`` is the permissive alternative: a key no ``Meta`` field and no
    custom argument claims is ignored with a warning.

    There is deliberately no ``dest`` field: an argument's ``dest`` is always
    its declared field name (the parsed instance attribute), so ``Meta`` raises
    a dedicated ``TypeError`` for ``dest=``.

    ``enum_by="value"`` matches an Enum field against ``str(member.value)``
    (command line, env and config alike) instead of the member name; the
    default, ``"name"``, is the rule when it is not set.

    ``literal_value=True`` on an option that takes one value makes the token
    after it always that value, even when it looks like an option (``--k --``,
    ``--k -x``); the default is the usual argparse rule.

    ``split`` is the callable ``Extend`` sets: it turns one raw token into
    several values.

    ``flags`` is the typed, lint-clean way to give an explicit flag tuple
    (equivalent to the bare ``("-n", "--times")`` statement in the class body,
    which some checkers flag as an unused expression)::

        times: Arg[int, Meta(flags=("-n", "--times"))] = 1
    """

    help: _ty.Any = _META_UNSET
    env: _ty.Any = _META_UNSET
    conflicts: _ty.Any = _META_UNSET
    conflicts_required: _ty.Any = _META_UNSET
    group: _ty.Any = _META_UNSET
    action: _ty.Any = _META_UNSET
    nargs: _ty.Any = _META_UNSET
    const: _ty.Any = _META_UNSET
    choices: _ty.Any = _META_UNSET
    metavar: _ty.Any = _META_UNSET
    required: _ty.Any = _META_UNSET
    type: _ty.Any = _META_UNSET
    version: _ty.Any = _META_UNSET
    flags: _ty.Any = _META_UNSET
    kwargs: _ty.Any = _META_UNSET
    default: _ty.Any = _META_UNSET
    enum_by: _ty.Any = _META_UNSET
    literal_value: _ty.Any = _META_UNSET
    split: _ty.Any = _META_UNSET

    def _duho_options_(self) -> dict[str, object]:
        """The explicitly-set metadata as a plain dict (unset fields omitted).

        Consumed by ``Args._getargs_`` in place of ``vars(self)`` so a
        sentinel-valued (never-set) field never overrides a type-derived kwarg.
        """
        return {k: v for k, v in vars(self).items() if v is not _META_UNSET}


#: Metadata keys the helpers below accept directly (as `Meta` does); every
#: other keyword is passed through to `add_argument`.
_HELPER_METADATA_KEYS = frozenset(
    {"help", "env", "conflicts", "conflicts_required", "group", "flags"}
)


def _helper_options(kw: dict[str, object]) -> dict[str, object]:
    """Split a helper's keywords into metadata keys and raw `add_argument` ones."""
    options = {k: v for k, v in kw.items() if k in _HELPER_METADATA_KEYS}
    raw = {k: v for k, v in kw.items() if k not in _HELPER_METADATA_KEYS}
    options["kwargs"] = raw
    return options


def Extend(
    split: _ty.Union[str, _ty.Callable[[str], _ty.Iterable]], **kwargs: object
) -> Meta:
    """Create a collection argument whose text is split on ``split`` first.

    A ``list[str]`` OPTION's own default builder already takes ``nargs=None``
    (one value per flag occurrence); a VARIADIC positional, or an option with
    an explicit ``Meta(nargs="*")`` override, instead gathers several raw
    tokens per occurrence. Combined with a factory that SPLITS one token into
    several, that shape would double-collect: argparse applies the factory to
    EACH gathered token individually, so a token's split result (itself a
    list, e.g. ``"a,b"`` -> ``["a", "b"]``) would be appended as ONE nested
    element instead of being flattened -- ``--rcopts '!*,build'`` becoming
    ``[['!*', 'build']]``, not ``['!*', 'build']``. Explicitly overriding
    ``nargs=None`` here (a single string per flag occurrence) avoids the
    double-collection regardless of what the field's own default would be.

    The split parts are mapped through the field's OWN element factory (so
    ``Arg[list[int], Extend(",")]`` yields ints, not strings) and fed to
    whichever collection action the field's declared type already uses --
    ``list``, ``set``, or ``tuple`` -- so this composes with a typed
    or non-list collection instead of forcing a stdlib list-only action. The
    field's own declared default (or the type ladder's empty collection when
    none is declared) is used as-is; it is NOT overridden here, so it is kept
    when the flag is absent and replaced -- like any other collection option
    -- on the first CLI occurrence.
    """
    options = _helper_options(kwargs)
    options["kwargs"].setdefault("nargs", None)  # type: ignore[union-attr]
    if isinstance(split, str):
        splitter: _ty.Callable[[str], list] = lambda x: x.split(split)  # type: ignore
    else:

        def splitter(text: str):
            result = split(text)
            if isinstance(result, list):
                return result
            return list(result)

    return Meta(split=splitter, **options)


def Count(**kw: object) -> Meta:
    """Create a count-action argument (e.g. `-vvv` -> 3)."""
    return Meta(action="count", **_helper_options(kw))


def Append(type: Factory = str, **kw: object) -> Meta:
    """Create an append-action argument, accumulating repeated flag values.

    Explicitly clears nargs to `None` (one scalar value per flag occurrence)
    regardless of the field's own declared collection kind or any ambient
    `Meta(nargs=...)`, so `append()` always collects one scalar per occurrence
    instead of gathering a list of tokens per occurrence.
    """
    return Meta(action="append", type=type, nargs=None, **_helper_options(kw))


def Const(value: object, **kw: object) -> Meta:
    """Create a store_const-action argument that stores `value` when present."""
    return Meta(action="store_const", const=value, **_helper_options(kw))


def Choice(*choices: object, **kw: object) -> Meta:
    """Restrict an argument's accepted values to `choices`."""
    return Meta(choices=tuple(choices), **_helper_options(kw))
