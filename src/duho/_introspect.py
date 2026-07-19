import ast as _ast
import functools as _functools
import inspect as _inspect
import sys as _sys
import textwrap as _textwrap
import typing as _ty
from dataclasses import dataclass as _data
from pathlib import Path as _Path

# Classes from these modules are never user-defined Args mixins; skip scanning
# their source entirely (stops re-parsing e.g. argparse.py for Namespace).
_SKIP_MODULES = frozenset({"argparse", "builtins", "typing"})


@_functools.lru_cache(maxsize=None)
def _module_index(filename: str) -> "dict[str, _ast.ClassDef]":
    """Parse a source file once and index all ClassDefs by qualname.

    Qualname is reconstructed by walking the tree while tracking the
    enclosing scope chain: entering a ClassDef appends "Name.", entering a
    Function/AsyncFunctionDef appends "name.<locals>." — this reproduces
    __qualname__ exactly, so nested and function-local classes resolve.
    """
    index: "dict[str, _ast.ClassDef]" = {}
    src = _Path(filename).read_text()
    tree = _ast.parse(src)

    def walk(node, prefix: str):
        for child in _ast.iter_child_nodes(node):
            if isinstance(child, _ast.ClassDef):
                qualname = prefix + child.name
                index[qualname] = child
                walk(child, qualname + ".")
            elif isinstance(child, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                walk(child, prefix + child.name + ".<locals>.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return index


def getclsdef(cls: type) -> "_ast.ClassDef | None":
    """Locate the ClassDef AST node for cls. Never raises."""
    try:
        module = _sys.modules.get(getattr(cls, "__module__", None))
        file = getattr(module, "__file__", None)
        if file:
            qualname = getattr(cls, "__qualname__", cls.__name__)
            found = _module_index(file).get(qualname)
            if found is not None:
                return found

        src = _inspect.getsource(cls)
        src = _textwrap.dedent(src)
        for node in _ast.walk(_ast.parse(src)):
            if isinstance(node, _ast.ClassDef) and node.name == cls.__name__:
                return node
        return None
    except (OSError, TypeError, SyntaxError):
        return None


class NotDefined: ...


NOT_DEFINED = NotDefined()


@_data
class ClsArgDeclaration:
    default: object
    type: type
    annotations: list
    docstring: str
    exprs: list


def _class_constants(cls: type) -> "dict[str, list]":
    """Scan a single class body for name -> [docstring?, *exprs] lists.

    Cached on the class itself (checked via vars(), not getattr, so
    inheritance can't false-hit a parent's cache).
    """
    if cls is object:
        return {}

    if "_duho_constants_" in vars(cls):
        return cls._duho_constants_  # type:ignore

    if cls.__module__ in _SKIP_MODULES:
        result: "dict[str, list]" = {}
    else:
        result = {}
        clsdef = getclsdef(cls)
        if clsdef is not None:
            argument = None
            for node in clsdef.body:
                if isinstance(node, (_ast.Assign, _ast.AnnAssign)):
                    if isinstance(node, _ast.Assign):
                        if len(node.targets) == 1 and isinstance(node.targets[0], _ast.Name):
                            argument = node.targets[0].id
                        else:
                            argument = None
                    else:
                        target = node.target
                        argument = target.id if isinstance(target, _ast.Name) else None
                    continue
                elif isinstance(node, _ast.Expr) and argument:
                    try:
                        value = _ast.literal_eval(node.value)
                    except ValueError:
                        continue
                    props = result.setdefault(argument, [])
                    props.append(value)
                else:
                    argument = None

    try:
        setattr(cls, "_duho_constants_", result)
    except TypeError:
        pass  # some builtin/extension types forbid attribute assignment
    return result


def get_clsargs_constants(cls: type) -> "dict[str, list]":
    argsexprs: "dict[str, list]" = {}
    for base in cls.__mro__:
        for name, exprs in _class_constants(base).items():
            argsexprs.setdefault(name, []).extend(exprs)
    return argsexprs


def _is_routine_or_descriptor(value) -> bool:
    if _inspect.isroutine(value):
        return True
    return hasattr(type(value), "__get__")


def get_clsargs(cls: type) -> "dict[str, ClsArgDeclaration]":
    if "_duho_clsargs_" in vars(cls):
        return cls._duho_clsargs_  # type:ignore

    typehints = _ty.get_type_hints(cls, include_extras=True)
    constants = get_clsargs_constants(cls)
    args: "dict[str, ClsArgDeclaration]" = {}
    for name, type in typehints.items():
        if name.startswith("_"):
            continue

        # ClassVar/Final are declarations, not CLI fields: a `count: ClassVar[int]`
        # or `MAX: Final[int]` must never become a `--count`/`--max` flag (C9).
        # `get_origin(ClassVar[int]) is ClassVar` on 3.9+; a bare `ClassVar`/
        # `Final` (unsubscripted) is caught by the identity check.
        if type is _ty.ClassVar or type is _ty.Final:
            continue
        if _ty.get_origin(type) in (_ty.ClassVar, _ty.Final):
            continue

        annotations = []
        if hasattr(type, "__metadata__"):
            annotations.extend(type.__metadata__)
            type = type.__origin__

        argconstant = constants.get(name, [])
        if argconstant and isinstance(argconstant[0], str):
            docstring = argconstant[0]
            argconstant = argconstant[1:]
        else:
            docstring = ""

        default = _inspect.getattr_static(cls, name, NOT_DEFINED)
        if default is not NOT_DEFINED and _is_routine_or_descriptor(default):
            default = NOT_DEFINED

        args[name] = ClsArgDeclaration(
            type=type,
            default=default,
            annotations=annotations,
            docstring=docstring,
            exprs=argconstant,
        )

    try:
        setattr(cls, "_duho_clsargs_", args)
    except TypeError:
        pass  # some builtin/extension types forbid attribute assignment
    return args


__all__ = ["getclsdef", "NotDefined", "NOT_DEFINED", "ClsArgDeclaration", "get_clsargs", "get_clsargs_constants"]
