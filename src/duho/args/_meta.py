from __future__ import annotations

import argparse as _argparse
import dataclasses as _dataclasses
import typing as _ty

from .. import _introspect as _introspect
from .._fieldspec import Factory as Factory

if _ty.TYPE_CHECKING:
    from ._argsclass import Args
    from ._cmd import Cmd


NOT_DEFINED = _introspect.NOT_DEFINED

_NONETYPE = type(None)

if _ty.TYPE_CHECKING:
    from typing_extensions import Self as _Self  # type: ignore

    class _Parser(_argparse.ArgumentParser, _ty.Generic[_T]):
        """Type-checking-only view of the parser ``_parser_`` returns: an
        ``ArgumentParser`` whose ``parse_args``/``parse_known_args`` are typed
        as returning ``_T`` (the constructed ``Args``/``Cmd`` instance) instead
        of a bare ``Namespace``. Never instantiated -- every real parser is a
        plain ``argparse.ArgumentParser`` with ``parse_known_args`` patched in
        place (see ``Args._initparser_``); this class exists only so
        ``-> "_Parser[_Self]"`` return annotations and the one
        ``typing.cast("_Parser[_Self]", ...)`` describe that shape to a type
        checker, referenced exclusively through quoted annotations that are
        never evaluated at runtime."""

        def parse_args(self, args=None, namespace: _T | None = None) -> _T:  # type: ignore
            raise NotImplementedError()

        def parse_known_args(  # type: ignore
            self, args=None, namespace: _T | None = None
        ) -> tuple[_T, list[str]]:
            raise NotImplementedError()


_type = type

_T = _ty.TypeVar("_T")

#: Bound to :class:`Args`: lets ``duho.parse``/``duho.parse_globals`` return the
#: caller's own subclass instead of erasing it to ``Args``/``Any``.
_A = _ty.TypeVar("_A", bound="Args")

#: Bound to ``type[Cmd]``: lets ``Cli.subcommand``/``@Cli.subcommand`` and
#: ``Cli._register_subcmd_`` return the decorated CLASS's own type instead of
#: widening it to ``type[Cmd]``.
_C = _ty.TypeVar("_C", bound="type[Cmd]")

NS = _argparse.Namespace


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


@_dataclasses.dataclass(init=False)
class Meta:
    """Typed, typo-safe alternative to ``NS(...)`` for field metadata.

    ``NS(...)`` is an untyped ``argparse.Namespace``: a misspelled key
    (``NS(hlep="oops")``) is silently dropped. ``Meta`` declares the known
    metadata fields as a dataclass, so an unknown keyword is a ``TypeError`` --
    the whole point -- raised as soon as the annotation is evaluated: at
    class-definition time on Python 3.9-3.13 with eager annotations, or at
    first parser build on 3.14+ (PEP 649), under string annotations, or with
    ``from __future__ import annotations``. Only the fields you set are merged
    (each defaults to a private sentinel); every key ``NS`` accepts EXCEPT
    ``dest`` (see below) is also a ``Meta`` field, and ``NS`` keeps working
    forever.

    Use it exactly where ``NS`` goes::

        level: Arg[int, Meta(help="verbosity", env="LEVEL")] = 0
        ("--level",)

    Recommended over ``NS`` precisely because a typo fails loud instead of
    vanishing. The ``kwargs`` field is the same raw ``add_argument`` escape hatch
    ``NS(kwargs=...)`` provides.

    There is deliberately no ``dest`` field: an argument's ``dest`` is always
    its declared field name (the parsed instance attribute), so ``Meta`` raises
    a dedicated ``TypeError`` for ``dest=`` instead of a value that looks
    honored but never is (as ``NS(dest=...)`` -- accepted, and ignored -- does).

    ``enum_by="value"`` matches an Enum field against ``str(member.value)``
    (command line, env and config alike) instead of the member name; the
    default, ``"name"``, is the rule when it is not set.

    ``literal_value=True`` on an option that takes one value makes the token
    after it always that value, even when it looks like an option (``--k --``,
    ``--k -x``); the default is the usual argparse rule.

    ``flags`` is the typed, lint-clean way to give an explicit flag tuple
    (equivalent to the bare ``("-n", "--times")`` statement in the class body,
    which some checkers flag as an unused expression)::

        times: Arg[int, Meta(flags=("-n", "--times"))] = 1
    """

    # Field order matters: `Meta` is a plain dataclass, so positional
    # construction (`Meta("help text")`) binds by position. The prefix
    # through `version` matches the pre-existing (pre-`Meta`-rewrite) order
    # exactly; `flags` takes over `dest`'s old slot (the removed `dest`
    # field, a documented [minor] break) immediately before `kwargs`, which
    # ALSO stays in dest's old neighboring slot -- right after `flags`, not
    # last. Every field added SINCE `kwargs` existed (`default`) goes AFTER
    # it: appending there, never inserting before an already-existing field,
    # is what keeps every earlier field's positional index (`Meta("help
    # text")`, `Meta(..., kwargs={...})`) from silently shifting each time a
    # new one is added.
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

    def __init__(
        self,
        help: _ty.Any = _META_UNSET,
        env: _ty.Any = _META_UNSET,
        conflicts: _ty.Any = _META_UNSET,
        conflicts_required: _ty.Any = _META_UNSET,
        group: _ty.Any = _META_UNSET,
        action: _ty.Any = _META_UNSET,
        nargs: _ty.Any = _META_UNSET,
        const: _ty.Any = _META_UNSET,
        choices: _ty.Any = _META_UNSET,
        metavar: _ty.Any = _META_UNSET,
        required: _ty.Any = _META_UNSET,
        type: _ty.Any = _META_UNSET,
        version: _ty.Any = _META_UNSET,
        flags: _ty.Any = _META_UNSET,
        kwargs: _ty.Any = _META_UNSET,
        default: _ty.Any = _META_UNSET,
        enum_by: _ty.Any = _META_UNSET,
        literal_value: _ty.Any = _META_UNSET,
        *,
        dest: _ty.Any = _META_UNSET,
    ) -> None:
        if dest is not _META_UNSET:
            # A dedicated message rather than the generic "unexpected keyword
            # argument": `dest` is the one key `Meta` deliberately never accepts.
            raise TypeError(
                "Meta has no 'dest' field: an argument's dest is always its field name"
            )
        self.help = help
        self.env = env
        self.conflicts = conflicts
        self.conflicts_required = conflicts_required
        self.group = group
        self.action = action
        self.nargs = nargs
        self.const = const
        self.choices = choices
        self.metavar = metavar
        self.required = required
        self.type = type
        self.version = version
        self.flags = flags
        self.kwargs = kwargs
        self.default = default
        self.enum_by = enum_by
        self.literal_value = literal_value

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
    split: str | _ty.Callable[[str], _ty.Iterable], **kwargs: object
) -> _argparse.Namespace:
    """Create a collection argument whose text is split on ``split`` first.

    A ``list[str]`` OPTION's own default builder already takes ``nargs=None``
    (one value per flag occurrence); a VARIADIC positional, or an option with
    an explicit ``NS(nargs="*")`` override, instead gathers several raw
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

    return _argparse.Namespace(split=splitter, **options)


def Count(**kw: object) -> _argparse.Namespace:
    """Create a count-action argument (e.g. `-vvv` -> 3)."""
    return NS(action="count", **_helper_options(kw))


def Append(type: Factory = str, **kw: object) -> _argparse.Namespace:
    """Create an append-action argument, accumulating repeated flag values.

    Explicitly clears nargs to `None` (one scalar value per flag occurrence)
    regardless of the field's own declared collection kind or any ambient
    `NS(nargs=...)`, so `append()` always collects one scalar per occurrence
    instead of gathering a list of tokens per occurrence.
    """
    return NS(action="append", type=type, nargs=None, **_helper_options(kw))


def Const(value: object, **kw: object) -> _argparse.Namespace:
    """Create a store_const-action argument that stores `value` when present."""
    return NS(action="store_const", const=value, **_helper_options(kw))


def Choice(*choices: object, **kw: object) -> _argparse.Namespace:
    """Restrict an argument's accepted values to `choices`."""
    return NS(choices=tuple(choices), **_helper_options(kw))
