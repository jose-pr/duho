from __future__ import annotations

import argparse as _argparse
import copy as _copy
import typing as _ty

from .. import _compat as _compat
from .. import _introspect as _introspect
from .._fieldspec import Factory as Factory
from .._fieldspec import _AppendAction as _AppendAction
from .._fieldspec import _bool_from_text as _bool_from_text
from .._fieldspec import _choice_checked as _choice_checked
from .._fieldspec import _factory_for as _factory_for
from .._fieldspec import _LayeredChoiceError as _LayeredChoiceError
from .._fieldspec import _NegatedBoolAction as _NegatedBoolAction

from ._helptext import _escape_stray_percent
from ._meta import NOT_DEFINED, _META_UNSET, _NONETYPE, _T, _type
from ._naming import _default_long_flag, _expand_flag_shorthand


class ArgumentMeta(_ty._ProtocolMeta):
    """Metaclass backing :class:`Argument`'s duck-typed ``isinstance`` check.

    An object satisfies :class:`Argument` simply by having a callable
    ``_argbuilder_`` -- no explicit subclassing/registration needed, so any
    custom type can opt in by defining that one classmethod.
    """

    def __instancecheck__(self, instance) -> bool:
        builder_factory = getattr(instance, "_argbuilder_", None)
        return callable(builder_factory)


@_ty.runtime_checkable
class Argument(_ty.Protocol, metaclass=ArgumentMeta):
    """The customization protocol for a field's declared type.

    Any object with a classmethod ``_argbuilder_(name, decl, factory=None) ->
    ArgumentBuilder`` satisfies this protocol (see :class:`ArgumentMeta`) and
    can be used as a field's type -- wrapped in ``Arg[CustomType, ...]``, or
    returned by :meth:`from_type` for a plain type -- to fully control how
    that field's :class:`ArgumentBuilder` is built. This is how duho's own
    type ladder (``int``, ``list[T]``, ``Literal[...]``, ...) is implemented,
    and the extension point a user-defined type (e.g. a ``Port`` value type)
    hooks into for the exact same treatment.
    """

    @classmethod
    def _argbuilder_(
        cls,
        name: str,
        decl: _introspect.ClsArgDeclaration,
        factory: _ty.Optional[Factory] = None,
    ) -> ArgumentBuilder:
        """Build this field's :class:`ArgumentBuilder` from its declaration.

        ``name`` is the field name; ``decl`` is its
        ``_introspect.ClsArgDeclaration`` (annotation, default, docstring,
        flags expression); ``factory`` is an already-resolved text-to-value
        callable, or ``None`` to derive one from ``decl.type``/``cls``.
        Returns a freshly built ``ArgumentBuilder`` -- ``Meta(...)`` or
        ``dict`` metadata is applied by the CALLER afterward (see
        :func:`_apply_argument_options`), not here.
        """
        # `%` must be escaped: argparse %-expands every `help=` (crashing parser
        # build on 3.14, `--help` on 3.9). An explicit `help=` override is applied
        # after this, so it is never double-escaped.
        help = _escape_stray_percent(decl.docstring or "")
        flags_expr = next(
            filter(lambda x: isinstance(x, (list, tuple, set)), decl.exprs),
            None,
        )
        default_flag = _default_long_flag(name)
        if flags_expr is None:
            flags: tuple[str, ...] = (default_flag,)
        else:
            flags = _normalise_flags(name, flags_expr, default_flag)
        required = None
        choices = None
        metavar = None
        action = None
        nargs = None
        collection = None
        default = decl.default
        ty = decl.type
        if ty is NOT_DEFINED:
            ty = factory if isinstance(factory, type) else cls

        if factory is None:
            _factory = decl.type if decl.type is not NOT_DEFINED else cls
        else:
            _factory = _ty.cast(Factory, factory)

        cls = ty
        if cls is not None and cls is not Argument:
            origin = _ty.get_origin(cls)
            args = _ty.get_args(cls)
            # An Optional[...] (Union carrying None) is not required.
            if origin in _compat.UNION_ORIGINS and _NONETYPE in args:
                required = False

            # One dispatch ladder, shared by the top level and every Union member.
            # A plain/custom type yields factory=None -- keep the seeded
            # `_factory` (e.g. a `from_type` custom factory) for that case.
            spec = _factory_for(cls, name, _enum_by_of(name, decl))
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
                # `_factory_for` gives list/set/tuple nargs="*", wrong for an
                # OPTION (one value per occurrence). `_kwargs` downgrades it once
                # the flags/nargs overrides are final; here we only record that
                # it came from the type ladder.
                nargs = spec.nargs
                implicit_nargs = True
            if spec.collection is not None:
                collection = spec.collection
            if spec.default is not NOT_DEFINED and default is NOT_DEFINED:
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
    def from_type(
        cls, factory: _ty.Callable[[str], _T], **kwargs: object
    ) -> type[Argument]:
        """Wrap a plain type/text-factory as an :class:`Argument`.

        Returns an ``Argument`` subclass whose ``_argbuilder_`` builds via
        ``cls``'s own (``Argument``'s base) logic using ``factory``, then
        applies ``**kwargs`` (the ``Meta(...)`` metadata dict) onto
        the result via :func:`_apply_argument_options`. Every plain-typed
        field (i.e. one not ALREADY a custom :class:`Argument`) is built
        through this single path -- see ``Args._getargs_``.
        """
        _factory = factory

        class Arg(cls):

            @classmethod
            def _argbuilder_(
                cls,
                name: str,
                decl: _introspect.ClsArgDeclaration,
                factory: Factory | None = _factory,
            ):
                builder = super()._argbuilder_(name, decl, factory or _factory)
                _apply_argument_options(builder, kwargs)
                return builder

        return Arg


def _normalise_flags(name: str, flags: object, default_flag: str) -> tuple[str, ...]:
    """A declared flag sequence as a tuple, with the ``"--"`` shorthand expanded.

    A set (no defined order) or an empty sequence raises a ``ValueError``
    naming the field.
    """
    if isinstance(flags, str):
        flags = (flags,)
    if isinstance(flags, (set, frozenset)):
        raise ValueError(
            f"argument {name!r}: flags must be given as a list or tuple, "
            f"not a set {flags!r} (a set has no guaranteed order)"
        )
    flags = tuple(_ty.cast("_ty.Sequence[str]", flags))
    if not flags:
        raise ValueError(f"argument {name!r}: flags must not be empty")
    return _expand_flag_shorthand(name, flags, default_flag)


_ENUM_BY = ("name", "value")


def _enum_by_of(name: str, decl: _introspect.ClsArgDeclaration) -> str:
    """The ``enum_by`` a field's ``Meta`` metadata asks for (default ``"name"``)."""
    enum_by = "name"
    for opts in decl.annotations or ():
        if isinstance(opts, _ty.Mapping):
            found = opts.get("enum_by")
        else:
            found = getattr(opts, "enum_by", None)
            if found is _META_UNSET:
                found = None
        if found is not None:
            enum_by = found
    if enum_by not in _ENUM_BY:
        raise ValueError(
            f"argument {name!r}: enum_by must be 'name' or 'value', got {enum_by!r}"
        )
    return enum_by


def _is_user_converter(func: object) -> bool:
    """Whether ``func`` is a callable the user wrote, not a builtin or duho's own."""
    if not callable(func):
        return False
    module = getattr(func, "__module__", None) or ""
    return module != "builtins" and module.split(".")[0] != "duho"


def _keep_message(func: _ty.Callable[[str], _ty.Any]):
    """Wrap ``func`` so a ValueError/TypeError carrying text reaches the user.

    argparse replaces such an error with a generic ``invalid NAME value``;
    re-raising it as ``ArgumentTypeError`` makes it print the message instead.
    """

    def convert(text):
        try:
            return func(text)
        except (ValueError, TypeError) as exc:
            if str(exc):
                raise _argparse.ArgumentTypeError(str(exc)) from exc
            raise

    for attr in ("__name__", "__qualname__", "__module__", "__doc__"):
        try:
            setattr(convert, attr, getattr(func, attr))
        except AttributeError:
            pass
    convert.__wrapped__ = func  # type: ignore[attr-defined]
    return convert


def _apply_argument_options(builder: ArgumentBuilder, options: dict) -> None:
    """Apply ``Meta(...)`` metadata onto an already-built builder.

    Shared by :meth:`Argument.from_type` and by a custom :class:`Argument` type
    used as ``Arg[Custom, Meta(...)]``, whose own ``_argbuilder_`` must keep running.
    """
    for k, v in options.items():
        setattr(builder, k, v)
    if "type" in options:
        builder._user_type_ = _is_user_converter(options["type"])
    if "flags" in options:
        builder.flags = _normalise_flags(
            builder.name, options["flags"], _default_long_flag(builder.name)
        )
    if "nargs" in options:
        # An explicit nargs override wins: clear the type-ladder marker so
        # `_kwargs` never downgrades it.
        builder._implicit_nargs_ = False
    if builder.split is not None:
        # duho.Extend(): compose the split with the field's element factory, so
        # a typed collection (`list[int]`) still converts each part and its
        # collection action runs unmodified.
        splitter = builder.split
        base = builder.type

        def _extend_factory(text, _splitter=splitter, _base=base):
            return [_base(part) for part in _splitter(text)]

        _extend_factory._duho_extend_base_ = base
        builder.type = _extend_factory


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
#: implicit one instead of `required=True` -- argparse's own natural
#: resting value for each, so `_effective_default_` agrees.
_ZERO_ARG_ACTION_DEFAULTS = {
    "count": 0,
    "store_const": None,
    "append_const": None,
    "store_false": True,
}


def _is_positional(flags: _ty.Sequence[str]) -> bool:
    """A flag tuple whose sole entry has no leading ``-`` is a positional.

    The one place this decision is made -- ``ArgumentBuilder._kwargs``
    and the ``is_positional`` property below both call this, instead of each
    re-deriving ``len(flags) == 1 and not flags[0].startswith("-")``
    independently (``duho.mcp`` calls it too).
    """
    return len(flags) == 1 and not flags[0].startswith("-")


class ArgumentBuilder(_argparse.Namespace):
    """One declared field's build state: raw ``add_argument`` kwargs plus
    duho's own metadata (``collection``/``split``/``env``/``conflicts``/...).

    Built by :meth:`Argument._argbuilder_` (via :meth:`Argument.from_type` for
    a plain type) from a field's ``_introspect.ClsArgDeclaration``, then
    post-processed with any ``Meta(...)`` metadata
    (:func:`_apply_argument_options`). One instance per declared field,
    cached on the owning class (``Args._getargs_``). :meth:`_kwargs` resolves
    it to the exact keyword arguments :meth:`add_to_parser` passes to
    ``parser.add_argument``; the env/config layering
    (``_raw_env_values``/``_raw_config_values``/``convert_layered``) reads it
    to convert a non-CLI value the same way a CLI occurrence would.
    """

    name: str
    flags: list[str]
    type: Factory
    default: _ty.Optional[_ty.Union[object, _introspect.NotDefined]]
    help: str
    required: _ty.Optional[bool] = None
    action: _ty.Optional[_ty.Union[str, type[_argparse.Action]]] = None
    nargs: _ty.Optional[_ty.Union[str, int]] = None
    choices: _ty.Optional[_ty.Sequence] = None
    metavar: _ty.Optional[str] = None
    const: _ty.Union[object, _introspect.NotDefined] = NOT_DEFINED
    version: _ty.Optional[str] = None
    env: _ty.Optional[str] = None
    #: ``Meta(conflicts=...)``'s mutually-exclusive-group
    #: key; ``None`` for a field in no group.
    conflicts: _ty.Optional[str] = None
    #: Whether THIS member's group must be satisfied (``Meta(conflicts_required=True)``);
    #: a group is required if ANY of its members sets this.
    conflicts_required: bool = False
    #: ``Meta(group=...)``'s titled-argument-group heading;
    #: ``None`` puts the field directly on the parser/container instead.
    group: _ty.Optional[str] = None
    #: The raw ``add_argument`` escape hatch (``Meta(kwargs={...})``/
    #: ``Meta(kwargs={...})``), applied LAST in :meth:`_kwargs` so it wins over
    #: every field-derived kwarg, including duho's own ``dest``.
    kwargs: _ty.Optional[_ty.Mapping[str, object]] = None
    #: For a ``list``/``set``/``tuple`` field the target collection type, else
    #: ``None``; a layered value converts to it as a CLI occurrence would (see
    #: :meth:`convert_layered`). ``self.type`` is then the element factory.
    collection: _ty.Optional[_type] = None
    #: ``duho.Extend()``'s split callable, or ``None``. Consumed by
    #: `Argument.from_type`'s wrapper to compose a text-splitting factory with
    #: the field's own element type; never read afterwards.
    split: _ty.Optional[_ty.Callable] = None
    #: True when `nargs` came from the type ladder, not `Meta(nargs=...)`: lets
    #: `_kwargs` make a repeatable option take one value per occurrence without
    #: clobbering an explicit opt-back into multi-value.
    _implicit_nargs_: bool = False
    #: True when `type` came from a user's ``Meta(type=...)``
    #: and is neither a builtin type nor one of duho's own factories; the
    #: command-line conversion then keeps that callable's own error message.
    _user_type_: bool = False
    #: ``"name"`` (default) or ``"value"``: how an Enum field's text is matched
    #: (``Meta(enum_by=...)``). Read by agent help and the MCP schema.
    enum_by: str = "name"
    #: ``Meta(literal_value=True)``: the token after this option's flag is
    #: always its value. Applied by the root parser before argv is split.
    literal_value: bool = False

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
        one with an explicit `action=` override.
        """
        return self.type is bool and not self.action and self.choices is None

    def _convert_single(self, raw):
        """Convert one raw scalar (env string / TOML-typed value) to the field type.

        A string always runs through ``self.type`` (the CLI text factory), so a
        bad value raises exactly the error argparse would. A non-string raw
        (TOML int/float/bool/date/list-element) goes through
        :meth:`_convert_non_str`.
        """
        factory = self.type
        if isinstance(raw, str):
            return factory(raw)
        return self._convert_non_str(raw, factory)

    def _convert_non_str(self, raw, factory):
        """Lossless-widening rule for a non-string raw value (TOML/JSON).

        Shared by :meth:`_convert_single` and :meth:`convert_layered`'s dict-table
        branch. Rejects a ``bool`` for a non-bool factory (``bool`` subclasses
        ``int``), a list/dict/set for a scalar, and a float with a fractional
        part for ``int``. Any other ``int``/``float`` goes through its text form,
        so a ``Union[int, str]`` factory sees ``"1.5"`` rather than a truncating
        ``1.5`` (an integral float is ``"1"``); bool-flavoured factories keep the
        raw number. Otherwise the factory decides, and a ``TypeError`` keeps the
        raw value (a native TOML date).
        """
        if isinstance(raw, bool):
            if (
                factory is bool
                or factory is _bool_from_text
                or getattr(factory, "_duho_union_bool_ok_", False)
            ):
                # Also a Union/Literal factory accepting bool (`Union[bool, int]`):
                # not `bool` by identity, but a raw bool is one of its shapes.
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
        if (
            isinstance(raw, (int, float))
            and factory is not bool
            and factory is not _bool_from_text
            and not getattr(factory, "_duho_union_bool_ok_", False)
        ):
            text = (
                str(int(raw))
                if isinstance(raw, float) and raw.is_integer()
                else str(raw)
            )
            try:
                return factory(text)
            except TypeError:
                pass
        try:
            return factory(raw)
        except TypeError:
            return raw

    def _check_layered_choices(self, value):
        """Validate a converted layered/instance value against ``self.choices``:
        ``set_defaults`` bypasses argparse's own choices check
        entirely, so a Literal/``Choice(...)`` field silently accepted any
        env or config text without this. Checked per element for a
        collection, per value for a dict; an Enum field never carries
        ``self.choices`` (its factory already validates by member name), so
        it is unaffected.
        """
        if self.choices is None:
            return
        if self.collection is dict:
            candidates = value.values()
        elif self.collection is not None:
            candidates = value
        else:
            candidates = (value,)
        for v in candidates:
            if v not in self.choices:
                raise _LayeredChoiceError(self.choices)

    def convert_layered(self, raw: object, *, source: str) -> object:
        """Convert a raw env/config *layer* value to this field's Python value.

        The env/config layers feed ``parser.set_defaults`` directly, bypassing
        argparse's own ``type=``/``action=`` handling -- so a layered value must
        be converted here to match what CLI parsing of the same field yields.
        Three field shapes are handled:

        * **bool** (``self.type is bool`` or a store_true/BooleanOptionalAction
          effective action): real bools pass through; strings map via the
          the strict :func:`_bool_from_text` table (unknown -> error).
        * **collection** (``self.collection`` set): a *string* raw becomes a
          single element wrapped in the collection (``FILES=a.txt`` ->
          ``["a.txt"]``, matching one CLI occurrence); a *list/tuple/set* raw
          (a TOML array) converts element-wise then coerces to the collection.
        * **scalar**: via :meth:`_convert_single`.

        The result is checked against ``self.choices`` before it is returned,
        the same membership check argparse itself would apply to a
        CLI-supplied value.

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
            if isinstance(raw, (bool, str)):
                return _bool_from_text(raw)
            raise ValueError(f"cannot interpret {raw!r} ({source}) as a boolean")

        if self.collection is dict:
            # A *string* raw ("k=v") runs the KV factory; a TOML table converts
            # each value through the value factory (typed values widen via
            # `_convert_non_str`).
            if isinstance(raw, _ty.Mapping):
                value_factory = getattr(self.type, "value_factory", str)
                result: dict = {}
                for k, v in raw.items():
                    if isinstance(v, str):
                        result[str(k)] = value_factory(v)
                    else:
                        result[str(k)] = self._convert_non_str(v, value_factory)
                self._check_layered_choices(result)
                return result
            value = self._convert_single(raw)
            self._check_layered_choices(value)
            return value

        if self.collection is not None:
            extend_base = getattr(self.type, "_duho_extend_base_", None)
            if extend_base is not None:
                # duho.Extend(): `self.type` SPLITS a string, so it must not run
                # per array element. A string raw is split whole; in an array each
                # string element is split and flattened, and a non-string element
                # widens via the base element factory.
                if isinstance(raw, str):
                    value = self.collection(self.type(raw))
                elif isinstance(raw, (list, tuple, set)):
                    parts: list = []
                    for e in raw:
                        if isinstance(e, str):
                            parts.extend(self.type(e))
                        else:
                            parts.append(self._convert_non_str(e, extend_base))
                    value = self.collection(parts)
                else:
                    value = self.collection([self._convert_non_str(raw, extend_base)])
            elif isinstance(raw, (list, tuple, set)):
                value = self.collection(self._convert_single(e) for e in raw)
            else:
                value = self.collection([self._convert_single(raw)])
            self._check_layered_choices(value)
            return value

        value = self._convert_single(raw)
        self._check_layered_choices(value)
        return value

    def _kwargs(self, *, layered: bool = False):
        # Meta(kwargs={...}) is the raw escape hatch and wins over every derived
        # kwarg, so it is applied last.
        overrides = dict(self.kwargs or {})
        kwargs: dict = {}

        positional = self.is_positional
        nargs = self.nargs
        # A repeatable OPTION's type-ladder nargs="*" (`_implicit_nargs_`) means
        # one value per occurrence; decided here from the FINAL flags/nargs, so
        # an explicit `Meta(nargs="*")` or a `Meta(flags=...)` making it positional
        # both work.
        if self._implicit_nargs_ and nargs == "*" and not positional:
            nargs = None
        if nargs is not None:
            kwargs["nargs"] = nargs

        if self.choices is not None:
            kwargs["choices"] = self.choices

        if self.metavar is not None:
            kwargs["metavar"] = self.metavar

        if self.default is not NOT_DEFINED:
            kwargs["default"] = self.default

        if self.is_bare_bool_flag:
            # A bare bool is a store_true/BooleanOptionalAction flag; a
            # `Literal[True, False]` or an explicit `action=` is excluded and uses
            # type=+choices= (argparse forbids choices= on store_true).
            no_flag = any(
                f.startswith("--no-") for f in self.flags if f.startswith("--")
            )
            if self.default is True:
                if no_flag:
                    # BooleanOptionalAction would build `--no-no-x` for a flag
                    # already starting `--no-` (rejected outright on 3.14+). A plain
                    # store_false keeps the meaning: presence sets False.
                    kwargs["action"] = "store_false"
                else:
                    kwargs["action"] = _argparse.BooleanOptionalAction
            elif layered and not no_flag:
                # A field a non-CLI layer (env=, config) can set True needs a way
                # back off from the CLI; store_true can only set it. Not for
                # `no_flag`: there the flag is the field's plain "on" spelling and
                # BooleanOptionalAction would double the negation (a crash on
                # 3.14), so it falls through to store_true.
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
            # 3.14 removed type/choices/metavar from BooleanOptionalAction; drop
            # them whoever chose the action, an explicit override included.
            kwargs.pop("metavar", None)
            kwargs.pop("choices", None)

        if action not in _TYPE_INCOMPATIBLE_ACTIONS:
            kwargs["type"] = _keep_message(self.type) if self._user_type_ else self.type
        else:
            kwargs.pop("type", None)

        if action == "store_true" and "default" not in kwargs:
            kwargs["default"] = False
        if action is _argparse.BooleanOptionalAction and "default" not in kwargs:
            kwargs["default"] = False

        if action == "append" and self.collection not in (None, list):
            # duho.Append() forces argparse's "append", which always yields a
            # list and does not compose with a set/tuple collection action; fail
            # at build time.
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
                # A variadic positional with `choices=`: argparse (through 3.13,
                # bpo-9625) validates the omitted DEFAULT against `choices` too,
                # even `default=SUPPRESS`. So check membership in the element
                # factory (as for Union/Literal members, see `_choice_checked`),
                # drop `choices=`, and show the values through `metavar`.
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
                # with none at all) needs nargs="?", otherwise
                # argparse makes it required and ignores the default.
                kwargs["nargs"] = "?"
            kwargs.pop("required", None)
        elif action in ("version", "help"):
            # argparse's _VersionAction / _HelpAction don't accept required=.
            pass
        elif self.required is not None:
            kwargs["required"] = self.required
        elif dest is not None:
            if self.conflicts:
                # argparse forbids a required member in a mutex group; group
                # requiredness is `conflicts_required=`.
                kwargs["required"] = False
            elif action in _ZERO_ARG_ACTION_DEFAULTS and "default" not in kwargs:
                # A zero-arg flag action with no default gets argparse's resting
                # value instead of becoming a mandatory flag.
                kwargs["required"] = False
                kwargs["default"] = _ZERO_ARG_ACTION_DEFAULTS[action]
            else:
                kwargs["required"] = "default" not in kwargs

        kwargs.update(overrides)

        # Copy a mutable default so each parser build owns it (the builder is
        # cached on the class); `_initparser_`'s `parse_known_args` copies again
        # per parse. Tuples are immutable.
        default_value = kwargs.get("default")
        if isinstance(default_value, (list, set, dict)):
            kwargs["default"] = _copy.copy(default_value)

        if kwargs.get("action") == "append":
            # argparse's "append" extends what is already on the namespace;
            # `_AppendAction` makes the first occurrence replace a class/env/
            # config default like every other collection action. Swapped in last:
            # the guards above test the plain string.
            kwargs["action"] = _AppendAction

        return kwargs

    def add_to_parser(
        self, parser: _argparse.ArgumentParser, *, layered: bool = False
    ) -> _argparse.Action:
        """Call ``parser.add_argument(*self.flags, ...)`` for this field.

        ``layered=True`` when an env/config layer can ALSO supply a value for
        this field (see :meth:`_kwargs`'s ``layered`` parameter -- it changes
        a bare bool field's action so the CLI can still turn a layer-supplied
        ``True`` back off). Returns the ``argparse.Action`` that was added.
        """
        help = self.help
        if callable(help):  # type: ignore
            help = help()
        kwargs = self._kwargs(layered=layered)
        flags = self.flags
        if kwargs.get("action") == "store_true" and layered and self.is_bare_bool_flag:
            # `_kwargs` gives a False-default `no_*` bool plain store_true, which
            # cannot turn a LAYERED True back off. Add the positive counterpart
            # (`--verify`) as an EXTRA option string on this SAME action (layering
            # keys off one action per dest) that sets False; declared flags still
            # set True.
            positive_flags = tuple(
                "--" + f[len("--no-") :]
                for f in self.flags
                if f.startswith("--no-") and len(f) > len("--no-")
            )
            if positive_flags:
                kwargs["action"] = _NegatedBoolAction
                kwargs["negative"] = self.flags
                flags = self.flags + positive_flags
        kwargs.setdefault("help", help)
        action = parser.add_argument(*flags, **kwargs)
        if self.literal_value:
            if not action.option_strings or action.nargs is not None:
                raise ValueError(
                    f"argument {self.name!r}: literal_value applies only to an "
                    f"option that takes exactly one value"
                )
            action._duho_literal_value_ = True  # type: ignore[attr-defined]
        if isinstance(action, _argparse.BooleanOptionalAction):
            # 3.9/3.10 append " (default: %(default)s)" to any non-None help;
            # reset it so Meta(help=SUPPRESS) stays hidden and no literal
            # `%(default)s` reaches agent-help JSON.
            action.help = help
        return action

    def _effective_default_(self) -> object:
        """The value argparse would leave this field at when not supplied.

        Reuses ``_kwargs()`` so it agrees exactly with what ``add_to_parser``
        registers (e.g. a ``store_true`` bool resolves to ``False`` even when no
        ``default`` was declared). Returns :data:`NOT_DEFINED` for a required
        field with no default -- callers seeding an instance leave those unset.
        A non-required OPTION with no declared default (e.g. ``Optional[int]``)
        resolves to ``None``, matching what argparse itself leaves on the
        namespace when the flag is absent.
        """
        kwargs = self._kwargs()
        if "default" in kwargs:
            return kwargs["default"]
        if kwargs.get("required") is False or (
            self.is_positional and kwargs.get("nargs") == "?"
        ):
            return None
        return NOT_DEFINED
