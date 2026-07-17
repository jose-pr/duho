import argparse as _argparse
import copy as _copy
import enum as _enum
import importlib.metadata as _importlib_metadata
import logging as _logging_module
import os as _os
import pathlib as _pathlib
import sys as _sys
import typing as _ty

from . import _compat as _compat
from . import _introspect as _inspect
from . import logging as _duho_logging

_duho_module_logger = _logging_module.getLogger("duho")

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
            resolved[builder.name] = builder.type(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"environment variable {builder.env!r} for field {builder.name!r}: "
                f"invalid value {raw!r} ({exc})"
            ) from exc
    return resolved


def _load_config(path: "str | _pathlib.Path") -> dict:
    """Read a TOML config file into a plain dict.

    Uses stdlib `tomllib` (3.11+) when available, else falls back to the
    third-party `tomli` package IFF it's installed. Neither is a hard
    dependency (duho stays zero-runtime-deps) -- if neither is importable,
    raises a clear RuntimeError telling the user to `pip install tomli`.
    """
    try:
        import tomllib as _toml  # type:ignore[import-not-found]
    except ImportError:
        try:
            import tomli as _toml  # type:ignore[import-not-found,no-redef]
        except ImportError:
            raise RuntimeError(
                "duho: reading a config file requires a TOML backend. "
                "Python 3.11+ has one built in (tomllib); on earlier "
                "versions, install the optional 'tomli' package "
                "(e.g. `pip install tomli` or `pip install duho[config]`)."
            ) from None

    p = _pathlib.Path(path).expanduser()
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
            resolved[key] = builder.type(raw) if isinstance(raw, str) else raw
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

    if merged:
        parser.set_defaults(**merged)
        for action in parser._actions:
            if action.dest in merged:
                action.required = False

    parser._duho_value_sources_ = sources  # type:ignore[attr-defined]
    parser._duho_merged_defaults_ = merged  # type:ignore[attr-defined]


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
    raw_config: dict = _load_config(config_path) if config_path is not None else {}

    def _walk(parser_, cls_, table: dict):
        _apply_default_layers_one(parser_, cls_, table)
        subcommands = getattr(cls_, "_subcommands_", None)
        if not subcommands:
            return
        # argparse stores each subcommand's parser on the _SubParsersAction
        # registered on parser_; find it and look up by the subcommand's
        # registered name (its _parsername_, set during _parser_()).
        subparsers_action = next(
            (
                a
                for a in parser_._actions
                if isinstance(a, _argparse._SubParsersAction)
            ),
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
    env: "str | None" = None

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
            instance = _cls(**parsed.__dict__)
            # Attach captured passthrough (empty list when no `--` was seen).
            instance._passthrough_ = passthrough if passthrough is not None else []
            # Debug-aid linkage for duho.value_sources(): remember the parser
            # (which carries _duho_value_sources_/_duho_merged_defaults_ from
            # _apply_default_layers) that produced instances of this class.
            # Per-class, not per-instance -- keeps Args instances themselves
            # free of framework bookkeeping in vars()/__dict__.
            _cls._duho_last_parser_ = parser  # type:ignore[attr-defined]
            return instance, unk

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

        # --print-completion: opt-in via _completion_ = True, only injected
        # on the top-level parser (a subcommand's own parser only sees its
        # own subtree, not the whole app) -- skipped if a "print_completion"
        # dest already exists (e.g. from a parents=[...] parser).
        if not is_subcommand and getattr(cls, "_completion_", False):
            actions_by_dest_pre2 = {action.dest: action for action in parser._actions}
            if "print_completion" not in actions_by_dest_pre2:
                parser.add_argument(
                    "--print-completion",
                    choices=("bash", "zsh", "fish"),
                    action=_PrintCompletionAction,
                    root_parser=parser,
                    dest="print_completion",
                    help="Print a shell completion script for the given shell and exit.",
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


class Cmd(Args):
    """An executable command: a data ``Args`` plus the command contract.

    ``Args`` (Plan 13) is pure data -- a Namespace of parsed values, not
    required to run. ``Cmd`` adds the *executable* contract on top: a
    ``main(self)`` entrypoint (named to align with ``__main__``), with
    ``__call__`` delegating to it so a ``Cmd`` instance stays directly
    callable (``instance()`` runs the command via ``main``).

    A ``Cmd`` subclass that defines neither ``main`` nor its own
    ``__call__`` raises ``NotImplementedError`` naming the class when
    dispatched -- the same loud-failure spirit as Plan 04's earlier
    "missing ``__call__``", just relocated onto ``Cmd``. Data-only ``Args``
    subclasses stay non-runnable by design: ``duho.main`` rejects them with
    a clear error rather than silently no-op'ing (the whole point of the
    split is that "runnable" is explicit).

    Base-order for the ``LoggingArgs`` mixin: ``class App(LoggingArgs, Cmd)``
    (data mixin first, executable base last). Both orders resolve the MRO
    correctly since ``LoggingArgs`` defines no ``main``/``__call__``; the
    recommended order reads "add logging to a command".
    """

    #: argv captured after the first literal ``--`` separator (parse-time);
    #: an empty list when no ``--`` was present. Populated on the parsed
    #: instance by ``_initparser_``'s patched ``parse_known_args``.
    _passthrough_: "list[str]"

    def main(self):  # noqa: D401 - contract stub, overridden by subclasses
        """Primary command entrypoint. Override in a ``Cmd`` subclass.

        The base raises ``NotImplementedError`` naming the concrete class,
        so a ``Cmd`` that forgets to implement ``main`` (and does not
        override ``__call__``) fails loud when dispatched rather than
        silently doing nothing.
        """
        raise NotImplementedError(
            f"{type(self).__name__} is a Cmd but implements neither "
            f"'main' nor '__call__'"
        )

    def __call__(self):
        """Run the command by delegating to ``main`` (keeps ``Cmd`` callable)."""
        return self.main()


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
    executable contract). Its ``main`` calls ``func(self)`` -- the parsed
    instance IS the parsed args, matching ``Cmd.main(self)`` -- so
    ``command(MyArgs, f)`` makes ``f`` receive the parsed ``MyArgs``-shaped
    instance and its return value becomes the command's result.

    ``name`` (optional) sets the built class's ``_parsername_`` (the
    subcommand name). When omitted, the usual
    ``_parsername_``/class-name rule applies to the generated class.
    """
    if Cmd in getattr(args_cls, "__mro__", ()):
        bases: tuple = (args_cls,)
    else:
        bases = (args_cls, Cmd)

    def main(self, _func=func):
        return _func(self)

    namespace: "dict[str, object]" = {"main": main}
    if name is not None:
        namespace["_parsername_"] = name

    cls_name = name or getattr(args_cls, "__name__", "Command")
    return _ty.cast("type[Cmd]", type(cls_name, bases, namespace))


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


def print_completion(cls, shell: str, file=None) -> None:
    """Print a shell completion script for `cls` to `file` (default sys.stdout).

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
    a ``Cmd`` (executable). A bare data ``Args`` -- with no ``main`` and no
    ``__call__`` -- raises a clear ``NotImplementedError`` ("Args holds data;
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
            f"(no '__call__'/'main'); make it a Cmd (subclass duho.Cmd or "
            f"build one with duho.command(...)) to run it"
        )

    result = run()
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
            effective_default = builder.default
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
    "Cmd",
    "command",
    "Const",
    "Count",
    "Extend",
    "Factory",
    "main",
    "NS",
    "NOT_DEFINED",
    "parse",
    "print_completion",
    "UpdateAction",
    "value_sources",
]
