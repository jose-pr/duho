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

from ._helptext import _escape_help
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
        factory: "Factory | None" = None,
    ):
        """Build this field's :class:`ArgumentBuilder` from its declaration.

        ``name`` is the field name; ``decl`` is its
        ``_introspect.ClsArgDeclaration`` (annotation, default, docstring,
        flags expression); ``factory`` is an already-resolved text-to-value
        callable, or ``None`` to derive one from ``decl.type``/``cls``.
        Returns a freshly built ``ArgumentBuilder`` -- ``NS(...)``/
        ``Meta(...)`` metadata is applied by the CALLER afterward (see
        :func:`_apply_argument_options`), not here.
        """
        # Escape a literal `%` in the field's docstring -- argparse
        # `%`-expands every action's `help=` unconditionally (crashing parser
        # BUILD on 3.14, `--help` on 3.9). An explicit `NS(help=...)`/
        # `Meta(help=...)` overrides this via the builder-options setattr
        # loop (see `Argument.from_type`/`_apply_argument_options`), applied
        # AFTER this, so it is never double-escaped.
        help = _escape_help(decl.docstring or "")
        flags_expr = next(
            filter(lambda x: isinstance(x, (list, tuple, set)), decl.exprs),
            None,
        )
        default_flag = _default_long_flag(name)
        if flags_expr is None:
            flags: "tuple[str, ...]" = (default_flag,)
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
                # unable to change it. The downgrade itself now lives
                # in `ArgumentBuilder._kwargs`, computed from the FINAL flags
                # and nargs once every override is known; here we only record
                # that this `nargs` came from the type ladder (not a user
                # override) via `implicit_nargs`, so `_kwargs` can tell the
                # two cases apart.
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
    def from_type(cls, factory: _ty.Callable[[str], _T], **kwargs):
        """Wrap a plain type/text-factory as an :class:`Argument`.

        Returns an ``Argument`` subclass whose ``_argbuilder_`` builds via
        ``cls``'s own (``Argument``'s base) logic using ``factory``, then
        applies ``**kwargs`` (the ``NS(...)``/``Meta(...)`` metadata dict) onto
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
                factory: "Factory | None" = _factory,
            ):
                builder = super()._argbuilder_(name, decl, factory or _factory)
                _apply_argument_options(builder, kwargs)
                return builder

        return Arg


def _normalise_flags(name: str, flags: object, default_flag: str) -> "tuple[str, ...]":
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


def _enum_by_of(name: str, decl: "_introspect.ClsArgDeclaration") -> str:
    """The ``enum_by`` a field's ``Meta``/``NS`` metadata asks for (default ``"name"``)."""
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


def _keep_message(func: "_ty.Callable[[str], _ty.Any]"):
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


def _apply_argument_options(builder: "ArgumentBuilder", options: dict) -> None:
    """Apply ``NS(...)``/``Meta(...)`` metadata onto an already-built
    ``ArgumentBuilder``.

    The post-processing :meth:`Argument.from_type`'s wrapper applies to a
    plain-type field's builder, factored out so it can ALSO be applied to a
    CUSTOM :class:`Argument` type's own builder (built by that type's own
    ``_argbuilder_``, not through ``from_type`` at all) when it is used
    inside ``Arg[CustomType, NS(...)]`` -- see ``Args._getargs_``. Without
    this, wrapping a custom type in ``Arg[...]``/``Meta(...)`` (the
    documented way to attach ``help=``/``env=`` to ANY field, custom types
    included) silently discarded the type's own ``_argbuilder_`` override and
    replaced it with the type used as a bare, uncustomized `type=` factory.
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
        # An explicit NS(nargs=...)/Meta(nargs=...) override wins outright --
        # clear the "came from the type ladder" marker so `_kwargs` never
        # downgrades it back.
        builder._implicit_nargs_ = False
    if builder.split is not None:
        # duho.Extend(): compose the split function with the field's OWN
        # element factory (already resolved onto `builder.type` by the
        # type's own `_argbuilder_` above) rather than replacing it outright,
        # so a typed collection (e.g. `list[int]`) still converts each split
        # part, and the natural collection action (list/set/tuple) still runs
        # unmodified.
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


def _is_positional(flags: "_ty.Sequence[str]") -> bool:
    """A flag tuple whose sole entry has no leading ``-`` is a positional.

    The one place this decision is made -- ``ArgumentBuilder._kwargs``
    and the ``is_positional`` property below both call this, instead of each
    re-deriving ``len(flags) == 1 and not flags[0].startswith("-")``
    independently (and, before this fix, disagreeing with a THIRD copy in
    ``duho.mcp``).
    """
    return len(flags) == 1 and not flags[0].startswith("-")


class ArgumentBuilder(_argparse.Namespace):
    """One declared field's build state: raw ``add_argument`` kwargs plus
    duho's own metadata (``collection``/``split``/``env``/``conflicts``/...).

    Built by :meth:`Argument._argbuilder_` (via :meth:`Argument.from_type` for
    a plain type) from a field's ``_introspect.ClsArgDeclaration``, then
    post-processed with any ``NS(...)``/``Meta(...)`` metadata
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
    default: "None | object | _introspect.NotDefined"
    help: str
    required: "bool | None" = None
    action: "str | type[_argparse.Action] | None" = None
    nargs: "str|int|None" = None
    choices: "_ty.Sequence | None" = None
    metavar: "str | None" = None
    const: "object | _introspect.NotDefined" = NOT_DEFINED
    version: "str | None" = None
    env: "str | None" = None
    #: ``NS(conflicts=...)``/``Meta(conflicts=...)``'s mutually-exclusive-group
    #: key; ``None`` for a field in no group. Declared here (rather than read
    #: via ``getattr(..., "conflicts", None)``) so every consumed metadata key
    #: has ONE declaration, matching ``Meta``'s own field list.
    conflicts: "str | None" = None
    #: Whether THIS member's group must be satisfied (``NS(conflicts_required=True)``);
    #: a group is required if ANY of its members sets this.
    conflicts_required: bool = False
    #: ``NS(group=...)``/``Meta(group=...)``'s titled-argument-group heading;
    #: ``None`` puts the field directly on the parser/container instead.
    group: "str | None" = None
    #: The raw ``add_argument`` escape hatch (``NS(kwargs={...})``/
    #: ``Meta(kwargs={...})``), applied LAST in :meth:`_kwargs` so it wins over
    #: every field-derived kwarg, including duho's own ``dest``.
    kwargs: "_ty.Mapping[str, object] | None" = None
    #: For a collection field (``list``/``set``/``tuple``) the target collection
    #: type; ``None`` for a scalar field. Recorded at build time so a layered
    #: (env/config) value converts to the SAME collection a CLI occurrence would
    #: produce (see :meth:`convert_layered`). ``self.type`` is then the *element*
    #: factory, not the collection factory.
    collection: "_type | None" = None
    #: ``duho.Extend()``'s split callable, or ``None``. Consumed by
    #: `Argument.from_type`'s wrapper to compose a text-splitting factory with
    #: the field's own element type; never read afterwards.
    split: "_ty.Callable | None" = None
    #: True when `nargs` came from the type ladder (a `list`/`set`/`tuple`
    #: field) rather than an explicit `NS(nargs=...)` override. Lets
    #: `_kwargs` downgrade a repeatable OPTION to one value per occurrence
    #: without also clobbering a deliberate opt-back into space-separated
    #: multi-value.
    _implicit_nargs_: bool = False
    #: True when `type` came from a user's ``NS(type=...)``/``Meta(type=...)``
    #: and is neither a builtin type nor one of duho's own factories; the
    #: command-line conversion then keeps that callable's own error message.
    _user_type_: bool = False
    #: ``"name"`` (default) or ``"value"``: how an Enum field's text is matched
    #: (``Meta(enum_by=...)``). Read by agent help and the MCP schema.
    enum_by: str = "name"

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

    #: Truthy/falsy strings a layered bool value maps to True/False
    #: (case-insensitive, whitespace-stripped). The one shared table
    #: (``_compat.BOOL_TRUE``/``BOOL_FALSE``) aliased here so
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
        :meth:`_convert_non_str`.
        """
        factory = self.type
        if isinstance(raw, str):
            return factory(raw)
        return self._convert_non_str(raw, factory)

    def _convert_non_str(self, raw, factory):
        """Shared lossless-widening rule for a non-string raw value.

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
        * a plain ``int``/``float`` that reaches neither rule above (a
          composite ``Union``/``Literal`` factory, or a type whose
          constructor rejects a bare number outright, e.g. ``Path(5)``) is
          routed through its own TEXT form instead of the raw number: every
          duho factory is fundamentally a CLI text factory, and calling it
          with the raw Python number only "worked" for ``int``/``str`` by
          accident of what those two builtins happen to accept. Handing a
          ``Union[int, str]`` factory the float ``1.5`` directly let
          ``int(1.5)`` truncate to ``1`` without ever raising; handing it
          ``"1.5"`` instead makes ``int("1.5")`` correctly reject it so the
          union falls through to its lossless ``str`` member. An integral
          float widens via its plain digits (``"1"``, not ``"1.0"``) so an
          ``int``-typed member still recognizes it. Excluded: any
          bool-flavoured factory (``bool``, ``_bool_from_text``, or a
          Union/Literal that also accepts a native bool) -- those keep their
          existing raw-number handling unchanged, since stringifying would
          make a falsy ``0`` a truthy non-empty string ``"0"``.
        * anything else: the factory itself decides, and a ``TypeError`` (a
          factory that flatly cannot accept a non-string, e.g.
          ``date.fromisoformat``) keeps the raw value unchanged -- documented,
          deliberate behavior, not a bug (a native TOML/JSON date in a date
          field, for example).
        """
        if isinstance(raw, bool):
            if (
                factory is bool
                or factory is _bool_from_text
                or getattr(factory, "_duho_union_bool_ok_", False)
            ):
                # The last check covers a Union/Literal factory that ALSO
                # accepts bool as one of its members (`Union[bool, int]`,
                # `Literal[True, "auto"]`) -- that composite callable is
                # neither `bool` nor `_bool_from_text` by identity, but a raw
                # bool is still one of its declared shapes, so it must not be
                # rejected here as "a boolean but the field expects
                # 'factory'" (a regression: this used to be accepted).
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
                self._check_layered_choices(result)
                return result
            value = self._convert_single(raw)
            self._check_layered_choices(value)
            return value

        if self.collection is not None:
            extend_base = getattr(self.type, "_duho_extend_base_", None)
            if extend_base is not None:
                # duho.Extend(): `self.type` SPLITS one string into several
                # elements rather than converting a single one, so it must
                # NOT be run once per array element like a plain per-element
                # factory would -- a *string* raw is the whole thing
                # to split; a *list/tuple/set* raw (a TOML array) splits each
                # STRING element and flattens the parts together, widening a
                # non-string element (already fully typed) via the base
                # (per-element) factory instead.
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
        # NS(kwargs={...}) is the raw escape-hatch override: it must win over
        # every field-derived kwarg (explicit NS(field=...) loses to it), so
        # field derivation writes into `kwargs` first and the raw overrides
        # are applied last, on top.
        overrides = dict(self.kwargs or {})
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
        # that makes the field positional both work. `_CollectionAction`
        # (bound to list/set/tuple/frozenset alike) already has its
        # own single-value-per-occurrence branch, so no action swap is needed.
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
            # A bare bool becomes a store_true/BooleanOptionalAction flag. A
            # `Literal[True, False]` (carries choices) or an explicit
            # action= override is excluded by `is_bare_bool_flag` -- those go
            # through type=+choices= like any other Literal, since argparse
            # forbids choices= on a store_true action.
            no_flag = any(
                f.startswith("--no-") for f in self.flags if f.startswith("--")
            )
            if self.default is True:
                if no_flag:
                    # BooleanOptionalAction tries to synthesize a --no-<flag>
                    # pair for a flag that ALREADY starts with --no- -- 3.14+
                    # rejects that outright, and 3.9-3.13 built the confusing
                    # --no-verify/--no-no-verify pair. A plain
                    # store_false under the SAME flag means what a
                    # True-default --no-* flag always meant: presence sets
                    # False, absence keeps the True default.
                    kwargs["action"] = "store_false"
                else:
                    kwargs["action"] = _argparse.BooleanOptionalAction
            elif layered and not no_flag:
                # A field that can receive True from a layer OTHER than the
                # CLI (env=, or the owning class has a config source) needs a
                # way to turn it back off from the command line -- store_true
                # can only ever SET True, never re-assert False. Excluded
                # when `no_flag`: a FALSE-default field whose own flag
                # already reads as a negation (e.g. a field literally named
                # `no_verify`, auto-deriving `--no-verify`) means the OPPOSITE
                # of the True-default case above -- presence of that flag is
                # the field's own plain, honest "on" spelling, not a reversal
                # of a default. BooleanOptionalAction would try to double the
                # negation (crashing outright on 3.14, see above); falling
                # through to plain store_true instead keeps that meaning
                # (env/config still supply the value when the CLI doesn't
                # mention the flag at all -- only overriding a layered True
                # back to False through THIS specific flag has no natural
                # spelling, an inherent limit of a field name that begins
                # with "no_").
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
            # metavar parameters outright -- drop them here, not only
            # when duho itself picked the action, so an explicit
            # NS(action=argparse.BooleanOptionalAction) override is covered
            # too.
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
            # duho.Append() forces argparse's stdlib "append" action, which
            # always produces a *list* -- it doesn't compose with a set/tuple
            # field's own collection action. Fail loud at build time
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
                # never validates anything itself here, on any version.
                # `metavar` still shows the allowed values.
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
                # A mutually-exclusive member with no explicit required= and
                # no default: argparse forbids a required member inside a
                # mutex group ("mutually exclusive arguments must be
                # optional"), so this can never become `required=True` here
                # -- group-level requiredness is exactly what
                # `conflicts_required=` expresses instead.
                kwargs["required"] = False
            elif action in _ZERO_ARG_ACTION_DEFAULTS and "default" not in kwargs:
                # A flag-style zero-argument action (count/store_const/
                # append_const/store_false) with no declared default gets
                # argparse's own natural resting value instead of becoming a
                # mandatory flag.
                kwargs["required"] = False
                kwargs["default"] = _ZERO_ARG_ACTION_DEFAULTS[action]
            else:
                kwargs["required"] = "default" not in kwargs

        kwargs.update(overrides)

        # Copy a mutable default so each parser build gets its OWN list/set/
        # dict (the builder is cached on the class, so without this every
        # build would share the same object). A second, per-PARSE copy (for
        # a parser reused across multiple parse_args() calls) happens
        # in `_initparser_`'s wrapped `parse_known_args`. Covers both the
        # collection-branch default ([]/set()) and an override default (e.g.
        # an explicit Extend(sep, default=[...])). Tuples are immutable.
        default_value = kwargs.get("default")
        if isinstance(default_value, (list, set, dict)):
            kwargs["default"] = _copy.copy(default_value)

        if kwargs.get("action") == "append":
            # argparse's stdlib "append" action starts from whatever is
            # already on the namespace, so the first CLI occurrence would
            # merge onto a class/env/config/instance default instead of
            # replacing it. `_AppendAction` gives `duho.Append()` the same
            # "first occurrence replaces" rule every other collection action
            # already has. Swapped in here (not earlier): every check above
            # this point (`action == "append"`'s own const/collection
            # guards) keys off the plain string, matching what a raw
            # `NS(action="append")`/`Append()` declares.
            kwargs["action"] = _AppendAction

        return kwargs

    def add_to_parser(self, parser: _argparse.ArgumentParser, *, layered: bool = False):
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
            # `_kwargs` falls through to plain `store_true` for a
            # FALSE-default bool whose own flag already reads as a negation
            # (`no_verify` -> `--no-verify`): `BooleanOptionalAction` rejects
            # any `--no-`-prefixed option string outright (it cannot tell
            # "already negative" from "would double-negate"), so that branch
            # is deliberately never reached for this shape. store_true alone
            # then has no way to turn a LAYERED (env/config) True back off
            # from the CLI, since it can only ever SET True.
            # `_NegatedBoolAction` gives it one: the stripped, positive-sense
            # counterpart (`--verify`) is added as an EXTRA option string on
            # this SAME action/dest (never a second action -- the layering
            # pipeline keys everything off ONE action per dest) that sets
            # False, while every originally-declared flag keeps setting True
            # exactly as `store_true` did.
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
        if isinstance(action, _argparse.BooleanOptionalAction):
            # 3.9/3.10's BooleanOptionalAction.__init__ unconditionally
            # appends " (default: %(default)s)" to any non-None help
            # (removed in 3.11) -- reset to the exact help duho passed in so
            # an NS(help=argparse.SUPPRESS) flag stays hidden (the identity
            # check argparse itself uses to hide it) and no literal
            # "%(default)s" leaks into agent-help JSON on the floor versions.
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
