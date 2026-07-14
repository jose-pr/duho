import argparse as _argparse
import types as _types
import typing as _ty

from .. import inspect as _inspect

NOT_DEFINED = _inspect.NOT_DEFINED
_NONETYPE = type(None)
_UNIONTYPE = type(_ty.Union)

if _ty.TYPE_CHECKING:
    from typing_extensions import Self as _Self  # type:ignore

_type = type

_T = _ty.TypeVar("_T")

Factory = _ty.Callable[[str], _T]

NS = _argparse.Namespace
Arg = _ty.Annotated


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
            if origin:
                if origin is _ty.Union and _NONETYPE in args:
                    args = [a for a in args if a is not _NONETYPE]
                    required = False
                    if len(args) == 1:
                        _factory = args[0]

                if origin is _ty.Union or origin is _UNIONTYPE and len(args) > 1:

                    def _factory(text: str, /):
                        for ty in args:
                            try:
                                return ty(text)
                            except:
                                pass
                        raise ValueError(text, args)

        return ArgumentBuilder(
            name=name,
            flags=flags,
            type=_factory,
            default=decl.default,
            help=help,
            required=required,
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


class ArgumentBuilder(_argparse.Namespace):
    name: str
    flags: list[str]
    type: Factory
    default: "None | object | _inspect.NotDefined"
    help: str
    required: "bool | None" = None
    action: "str | _type[_argparse.Action] | None" = None
    nargs: "str|int|None" = None

    def _kwargs(self):
        kwargs = getattr(self, "kwargs", None) or {}

        if self.nargs != None:
            kwargs["nargs"] = self.nargs

        if self.default is not _inspect.NOT_DEFINED:
            kwargs["default"] = self.default

        if self.type is bool and not self.action:
            kwargs["action"] = "store_true"
        if self.action:
            kwargs["action"] = self.action

        if kwargs.get("action") not in ["count", "store_true"]:
            kwargs["type"] = self.type

        if kwargs.get("action") == "store_true" and "default" not in kwargs:
            kwargs["default"] = False

        flags = self.flags
        dest = self.name
        if len(flags) == 1 and not flags[0].startswith("-"):
            dest = None

        if dest:
            kwargs["dest"] = dest

        if self.required is not None:
            kwargs["required"] = self.required
        elif dest is not None:
            kwargs["required"] = "default" not in kwargs

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
        return args

    @classmethod
    def build_parser(
        cls,
        subparser: "_argparse._SubParsersAction | None" = None,
        name: "str | None" = None,  # type:ignore
        parents: _ty.Sequence[_argparse.ArgumentParser] = [],
        init=True,
        **kwargs,
    ) -> "_Parser[_Self]":
        if subparser:
            method = subparser.add_parser
        else:
            method = _argparse.ArgumentParser

        name: str = name or getattr(cls, "_parsername_", None) or cls.__name__
        setattr(cls,'_parsername_', name)
        kwargs.setdefault("description", cls.__doc__ or "")
        parser = _ty.cast(
            "_Parser[_ty.Self]",
            method(name, parents=parents, **kwargs),
        )
        
        if init:
            cls.initparser(parser)

        return parser

    @classmethod
    def initparser(
        cls, parser: _argparse.ArgumentParser, exclusive_groups: dict = None
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
            _cls: "type[_ty.Self]" = getattr(parsed, "#cls")
            return _cls(**parsed.__dict__), unk

        parser.parse_known_args = parse_known_args  # type:ignore
        exclusive_groups = exclusive_groups or {}
        for arg in cls._getargs_():
            _action = None
            for action in parser._actions:
                if action.dest == arg.name:
                    _action = action
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
            setattr(cls, f"_action_{_action.dest}", _action)

        return parser
