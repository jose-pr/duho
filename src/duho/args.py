import argparse as _argparse
import copy as _copy
import enum as _enum
import importlib.metadata as _importlib_metadata
import logging as _logging_module
import typing as _ty

from . import _compat as _compat
from . import _introspect as _inspect
from . import logging as _duho_logging

NOT_DEFINED = _inspect.NOT_DEFINED
_NONETYPE = type(None)

if _ty.TYPE_CHECKING:
    from typing_extensions import Self as _Self  # type:ignore

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


def _enum_name_factory(enum_cls: type) -> "Factory":
    """Build a factory that resolves CLI text to an enum member by NAME.

    Raises ValueError (not KeyError) on a miss so callers that catch
    ``(TypeError, ValueError)`` (e.g. the Union-branch try-loop) can treat a
    non-matching name as "this sub-factory rejects text" and fall through.
    """
    names = tuple(member.name for member in enum_cls)

    def _factory(text: str, /, _enum_cls=enum_cls, _names=names):
        if text not in _names:
            raise ValueError(
                f"invalid choice: {text!r} (choose from {', '.join(_names)})"
            )
        return _enum_cls[text]

    return _factory


def _resolve_version(cls) -> "str | None":
    """Resolve a class's effective ``--version`` string, or None to skip it.

    ``_version_`` may be unset/None (no --version), an explicit str (used
    as-is), or the ``AUTO`` sentinel (resolved via importlib.metadata using
    ``_distribution_`` or the class's top-level import package). Never
    raises -- any resolution failure is logged at debug level and treated
    as "no version available".
    """
    raw = getattr(cls, "_version_", None)
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    if raw is AUTO:
        dist = getattr(cls, "_distribution_", None) or cls.__module__.split(".")[0]
        try:
            return _importlib_metadata.version(dist)
        except _importlib_metadata.PackageNotFoundError:
            _logging_module.getLogger("duho").debug(
                "duho.AUTO: distribution %r not found for %s; skipping --version",
                dist,
                cls,
            )
            return None
        except Exception:
            _logging_module.getLogger("duho").debug(
                "duho.AUTO: failed to resolve version for %s (distribution %r)",
                cls,
                dist,
                exc_info=True,
            )
            return None
    return None


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
        flags = next(
            filter(lambda x: isinstance(x, (list, tuple, set)), decl.exprs),
            ("--" + name.replace("_", "-"),),
        )
        required = None
        choices = None
        metavar = None
        action = None
        nargs = None
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
            if origin is _ty.Literal:
                choices = args
                literal_types = []
                for lit in args:
                    lit_ty = type(lit)
                    if lit_ty not in literal_types:
                        literal_types.append(lit_ty)
                if len(literal_types) == 1:
                    _factory = literal_types[0]
                else:
                    # Mixed-type Literal: try each declared literal's own type,
                    # but only accept a conversion that round-trips to one of
                    # the declared values (a naive "first type that doesn't
                    # raise" would let e.g. str('1') shadow int(1)).
                    def _factory(text: str, /, _literals=tuple(args)):
                        for lit in _literals:
                            try:
                                candidate = type(lit)(text)
                            except (TypeError, ValueError):
                                continue
                            if candidate == lit:
                                return candidate
                        raise ValueError(
                            f"could not convert {text!r} using any of {_literals}"
                        )

            elif isinstance(cls, type) and issubclass(cls, _enum.Enum):
                names = tuple(member.name for member in cls)
                metavar = "{" + ",".join(names) + "}"
                _factory = _enum_name_factory(cls)

            elif origin is list or cls is list:
                elem_ty = args[0] if args else str
                action = "extend"
                nargs = "*"
                _factory = elem_ty
                if default is _inspect.NOT_DEFINED:
                    default = []

            elif origin in _compat.UNION_ORIGINS:
                if _NONETYPE in args:
                    args = [a for a in args if a is not _NONETYPE]
                    required = False

                # Specialized per-member factories, preserving declaration
                # order: Enum members resolve by NAME (consistent with the
                # bare-enum branch), others use the type itself.
                specialized = tuple(
                    _enum_name_factory(a)
                    if isinstance(a, type) and issubclass(a, _enum.Enum)
                    else a
                    for a in args
                )

                if len(specialized) == 1:
                    _factory = specialized[0]

                if len(specialized) > 1:

                    def _factory(text: str, /, _factories=specialized):
                        for f in _factories:
                            try:
                                return f(text)
                            except (TypeError, ValueError):
                                pass
                        raise ValueError(
                            f"could not convert {text!r} using any of {_factories}"
                        )

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

    def _kwargs(self):
        # NS(kwargs={...}) is the raw escape-hatch override: it must win over
        # every field-derived kwarg (explicit NS(field=...) loses to it), so
        # field derivation writes into `kwargs` first and the raw overrides
        # are applied last, on top.
        overrides = dict(getattr(self, "kwargs", None) or {})
        kwargs: dict = {}

        if self.nargs != None:
            kwargs["nargs"] = self.nargs

        if self.choices is not None:
            kwargs["choices"] = self.choices

        if self.metavar is not None:
            kwargs["metavar"] = self.metavar

        if self.default is not _inspect.NOT_DEFINED:
            kwargs["default"] = self.default

        if self.type is bool and not self.action:
            if self.default is True:
                kwargs["action"] = _argparse.BooleanOptionalAction
            else:
                kwargs["action"] = "store_true"
        if self.action:
            kwargs["action"] = self.action

        # Resolve the *effective* action (raw override wins) so the
        # type-incompatibility / const / version guards below key off what
        # will actually be sent to add_argument, not the pre-override value.
        action = overrides.get("action", kwargs.get("action"))

        if action not in _TYPE_INCOMPATIBLE_ACTIONS:
            kwargs["type"] = self.type
        else:
            kwargs.pop("type", None)

        if action == "store_true" and "default" not in kwargs:
            kwargs["default"] = False

        if action in _CONST_REQUIRED_ACTIONS:
            const = self.const if self.const is not NOT_DEFINED else kwargs.get("const", NOT_DEFINED)
            if const is NOT_DEFINED:
                raise ValueError(
                    f"argument {self.name!r}: action={action!r} requires const="
                )
            kwargs["const"] = const
        elif self.const is not NOT_DEFINED:
            kwargs["const"] = self.const

        if action == "version":
            version = self.version if self.version is not None else kwargs.get("version")
            if version is not None:
                kwargs["version"] = version

        flags = self.flags
        dest = self.name
        positional = len(flags) == 1 and not flags[0].startswith("-")
        if positional:
            dest = None

        if dest:
            kwargs["dest"] = dest

        if positional:
            # Optional positional (has a real default, nargs unset) needs
            # nargs="?" -- otherwise argparse makes it required and ignores
            # the default. argparse also forbids required= on positionals.
            if "default" in kwargs and "nargs" not in kwargs:
                kwargs["nargs"] = "?"
            kwargs.pop("required", None)
        elif action in ("version", "help"):
            # argparse's _VersionAction / _HelpAction don't accept required=.
            pass
        elif self.required is not None:
            kwargs["required"] = self.required
        elif dest is not None:
            kwargs["required"] = "default" not in kwargs

        kwargs.update(overrides)
        return kwargs

    def add_to_parser(self, parser: _argparse.ArgumentParser):
        help = self.help
        if callable(help):  # type:ignore
            help = help()
        return parser.add_argument(
            *self.flags,
            help=help,
            **self._kwargs(),
        )


class _Parser(_argparse.ArgumentParser, _ty.Generic[_T]):
    def parse_args(self, args=None, namespace: "_T | None" = None) -> _T:  # type:ignore
        raise NotImplementedError()

    def parse_known_args(  # type:ignore
        self, args=None, namespace: "_T | None" = None
    ) -> tuple[_T, list[str]]:
        raise NotImplementedError()


class Args(_argparse.Namespace):
    @classmethod
    def _getargs_(cls):
        if "_duho_builders_" in vars(cls):
            return cls._duho_builders_

        clsargs = _inspect.get_clsargs(cls)
        args: list[ArgumentBuilder] = []
        for name, decl in clsargs.items():
            if decl.annotations:
                if decl.annotations[0] is _argparse.SUPPRESS:
                    continue
                options = {}
                for opts in decl.annotations:
                    options.update(
                        opts if isinstance(opts, _ty.Mapping) else opts.__dict__
                    )
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
        name: "str | None" = None,  # type:ignore
        parents: _ty.Sequence[_argparse.ArgumentParser] = (),
        init=True,
        **kwargs,
    ) -> "_Parser[_Self]":
        if subparser:
            method = subparser.add_parser
        else:
            method = _argparse.ArgumentParser

        name: str = name or getattr(cls, "_parsername_", None) or cls.__name__
        if not getattr(cls, "_parsername_", None):
            setattr(cls, "_parsername_", name)
        kwargs.setdefault("description", cls.__doc__ or "")
        if subparser:
            docstring = cls.__doc__ or ""
            kwargs.setdefault("help", docstring.strip().splitlines()[0] if docstring.strip() else "")
        parser = _ty.cast(
            "_Parser[_ty.Self]",
            method(name, parents=list(parents), **kwargs),
        )

        if init:
            cls._initparser_(parser, is_subcommand=bool(subparser))

        subcommands = getattr(cls, "_subcommands_", None)
        if subcommands:
            subparsers = parser.add_subparsers(dest="command", required=True)
            for sub in subcommands:
                sub._parser_(subparsers)

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

            parsed, unk = _argparse.ArgumentParser.parse_known_args(
                parser, args, namespace
            )

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
            parser._duho_selected_cls_ = _cls  # type:ignore
            return _cls(**parsed.__dict__), unk

        parser.parse_known_args = parse_known_args  # type:ignore
        exclusive_groups = exclusive_groups or {}

        version = _resolve_version(cls)
        actions_by_dest_pre = {action.dest: action for action in parser._actions}
        if version and "version" not in actions_by_dest_pre:
            parser.add_argument(
                "--version",
                action="version",
                version=f"%(prog)s {version}",
            )

        actions_by_dest = {action.dest: action for action in parser._actions}
        for arg in cls._getargs_():
            _action = actions_by_dest.get(arg.name)
            if not _action:
                conflicts = getattr(arg, "conflicts", None)
                if conflicts:
                    if conflicts not in exclusive_groups:
                        exclusive_groups[conflicts] = (
                            parser.add_mutually_exclusive_group()
                        )
                    group = exclusive_groups[conflicts]
                else:
                    group = parser
                _action = arg.add_to_parser(group)

        return parser


def Extend(split: "str | _ty.Callable[[str], _ty.Iterable]", **kwargs):
    """Create an extend-action argument with optional string splitting."""
    kwargs.setdefault("default", [])
    if isinstance(split, str):
        ty: _ty.Callable[[str], list] = lambda x: x.split(split)  # type:ignore
    else:

        def ty(text: str):
            result = split(text)
            if isinstance(result, list):
                return result
            return list(result)

    return _argparse.Namespace(type=ty, action="extend", kwargs=kwargs)


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
    """Action that updates a dict instead of replacing it."""
    def __call__(  # type:ignore
        self, parser, namespace, values: dict, option_string=None
    ):
        items: dict = getattr(namespace, self.dest, {})
        items = _copy.deepcopy(items)
        items.update(values or {})
        setattr(namespace, self.dest, items)


def main(cls, argv: "_ty.Sequence[str] | None" = None, *, setup_logging=True) -> int:
    """Build a parser for cls, parse argv, and dispatch to instance.__run__().

    Module-level (not a classmethod) so the Args subclass namespace stays
    entirely user-owned. Steps: build parser (auto-registers _subcommands_),
    parse argv (SystemExit from argparse propagates), optionally set up
    stderr logging + apply verbosity when the resulting instance provides
    _set_loglevels_, then call instance.__run__() and map a None return to 0.
    """
    parser = cls._parser_()
    instance = parser.parse_args(argv)

    if setup_logging and hasattr(instance, "_set_loglevels_"):
        root = _logging_module.getLogger()
        if not root.handlers:
            _duho_logging.init_stderr_logging()
        instance._set_loglevels_()

    run = getattr(instance, "__run__", None)
    if run is None:
        raise NotImplementedError(
            f"{type(instance).__name__} does not implement __run__"
        )

    result = run()
    return 0 if result is None else result


def parse(spec, argv: "_ty.Sequence[str] | None" = None, *, parser_kwargs=None):
    """Build a parser from `spec` and parse `argv` into a new instance.

    `spec` may be:
    - An `Args` subclass (type): equivalent to `spec._parser_().parse_args(argv)`.
    - An instance of an `Args` subclass: the instance's current field values
      are used as argparse defaults (via `parser.set_defaults(**overrides)`,
      filtered to actual CLI fields -- not `vars(spec)`, which would include
      framework attrs). CLI args still override those defaults. Returns a
      NEW instance of `type(spec)`; `spec` itself is never mutated.

    Precedence: CLI args > instance field values > class defaults. Note this
    means a required field (no class default) that the instance already has
    a value for becomes effectively optional for this call.
    """
    parser_kwargs = parser_kwargs or {}
    if isinstance(spec, type):
        cls = spec
        parser = cls._parser_(**parser_kwargs)
        return parser.parse_args(argv)

    cls = type(spec)
    parser = cls._parser_(**parser_kwargs)
    field_names = {builder.name for builder in cls._getargs_()}
    overrides = {
        name: value
        for name, value in vars(spec).items()
        if name in field_names
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


__all__ = [
    "Append",
    "Argument",
    "ArgumentBuilder",
    "ArgumentMeta",
    "Args",
    "Arg",
    "Choice",
    "Const",
    "Count",
    "Extend",
    "Factory",
    "main",
    "NS",
    "NOT_DEFINED",
    "parse",
    "UpdateAction",
]
