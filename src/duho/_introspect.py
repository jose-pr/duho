import ast as _ast
import functools as _functools
import inspect as _inspect
import logging as _logging
import sys as _sys
import textwrap as _textwrap
import typing as _ty
from dataclasses import dataclass as _data

_LOGGER = _logging.getLogger("duho")
from pathlib import Path as _Path

# Classes from these modules are never user-defined Args mixins; skip scanning
# their source entirely (stops re-parsing e.g. argparse.py for Namespace).
_SKIP_MODULES = frozenset({"argparse", "builtins", "typing"})

# ``try/except*`` (PEP 654, 3.11+) carries the same statement-body fields as a
# plain ``Try``; reference it via ``getattr`` so the isinstance check is a no-op
# on 3.9/3.10 where the node type does not exist. Together with ``ast.Try`` this
# lets the P3 statement-only walk descend both try forms.
_TRY_TYPES = tuple(
    t for t in (_ast.Try, getattr(_ast, "TryStar", None)) if t is not None
)


@_functools.lru_cache(maxsize=None)
def _module_index(filename: str) -> "dict[str, _ast.ClassDef]":
    """Parse a source file once and index all ClassDefs by qualname.

    Qualname is reconstructed by walking the tree while tracking the
    enclosing scope chain: entering a ClassDef appends "Name.", entering a
    Function/AsyncFunctionDef appends "name.<locals>." — this reproduces
    __qualname__ exactly, so nested and function-local classes resolve.
    """
    index: "dict[str, _ast.ClassDef]" = {}
    # Always decode as UTF-8 (Python source's default), not the locale encoding:
    # a non-ASCII source under a cp1252 locale otherwise raised UnicodeDecodeError
    # (M11).
    src = _Path(filename).read_text(encoding="utf-8")
    tree = _ast.parse(src)

    def walk(body, prefix: str):
        # Recurse only into STATEMENT containers, not `ast.iter_child_nodes` on
        # every node (P3). A ClassDef/FunctionDef can only appear as a statement
        # in some enclosing statement's body -- never inside an expression -- so
        # walking only the statement-carrying fields (`body`/`orelse`/`finalbody`
        # and each except handler's `body`) reaches every class while skipping the
        # deep expression/argument/decorator subtrees `iter_child_nodes` descends.
        # Qualname reconstruction is preserved exactly: a ClassDef appends
        # "Name.", a Function/AsyncFunctionDef appends "name.<locals>.".
        for child in body:
            if isinstance(child, _ast.ClassDef):
                qualname = prefix + child.name
                index[qualname] = child
                walk(child.body, qualname + ".")
            elif isinstance(child, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                walk(child.body, prefix + child.name + ".<locals>.")
            elif isinstance(child, _ast.If):
                walk(child.body, prefix)
                walk(child.orelse, prefix)
            elif isinstance(child, (_ast.For, _ast.AsyncFor, _ast.While)):
                walk(child.body, prefix)
                walk(child.orelse, prefix)
            elif isinstance(child, (_ast.With, _ast.AsyncWith)):
                walk(child.body, prefix)
            elif isinstance(child, _TRY_TYPES):
                walk(child.body, prefix)
                for handler in child.handlers:
                    walk(handler.body, prefix)
                walk(child.orelse, prefix)
                walk(child.finalbody, prefix)
            elif isinstance(child, getattr(_ast, "Match", ())):
                # Structural pattern matching (PEP 634, 3.10+): a class can be
                # defined inside a case body. ``getattr(..., ())`` makes the
                # isinstance check a no-op on 3.9 where ``ast.Match`` is absent.
                for case in child.cases:
                    walk(case.body, prefix)

    walk(tree.body, "")
    return index


def getclsdef(cls: type) -> "_ast.ClassDef | None":
    """Locate the ClassDef AST node for cls. Never raises."""
    try:
        module = _sys.modules.get(getattr(cls, "__module__", None))
        file = getattr(module, "__file__", None)
        if file:
            qualname = getattr(cls, "__qualname__", cls.__name__)
            index = _module_index(file)
            found = index.get(qualname)
            if found is not None:
                return found
            # The module file WAS indexed successfully but this qualname is
            # absent -- the class was created dynamically (``type(...)`` /
            # ``duho.command(...)``) and has no literal ``ClassDef`` in the
            # source. ``inspect.getsource`` re-parses the exact same file and
            # fails the identical lookup, only slower (up to ~23 ms per class in
            # a large dynamically-built tree, P5). Give up now. The getsource
            # fallback below is reserved for the no-module-file case
            # (REPL/``exec``), where there is no file to index.
            return None

        src = _inspect.getsource(cls)
        src = _textwrap.dedent(src)
        for node in _ast.walk(_ast.parse(src)):
            if isinstance(node, _ast.ClassDef) and node.name == cls.__name__:
                return node
        return None
    except (OSError, TypeError, SyntaxError, ValueError):
        # ValueError covers UnicodeDecodeError from the getsource fallback (which
        # reads with the locale encoding) as well as other malformed-source cases
        # (M11/M18).
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
        if clsdef is None and getattr(cls, "__annotations__", None):
            # A class with annotated fields whose source we could not locate
            # (REPL/exec/zipapp) silently loses its flags/env/docstrings. Leave a
            # one-time diagnostic (this runs once per class -- the result is cached
            # below) so the loss is at least discoverable (M18).
            _LOGGER.debug(
                "duho: no source ClassDef found for %s.%s; class-body flags, "
                "env, and attribute docstrings will be unavailable",
                getattr(cls, "__module__", "?"),
                getattr(cls, "__qualname__", getattr(cls, "__name__", cls)),
            )
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
                    except (ValueError, TypeError, SyntaxError):
                        # A non-literal expression (a call, a name) ends the
                        # current field's metadata run: reset attribution so a
                        # LATER literal/docstring is not misattributed to it (M18).
                        argument = None
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


def _looks_like_a_resolved_type(value: object) -> bool:
    """True if ``value`` is a plausible resolved type-hint (a type, or a
    typing construct), False if it's some OTHER kind of object entirely.

    Guards against a specific, unfixable-at-the-annotation-level Python
    footgun: a field whose NAME is identical to its own annotation (e.g.
    ``bool: bool = False``) executes the annotated assignment's VALUE-store
    BEFORE the annotation expression is evaluated (confirmed via bytecode:
    ``STORE_NAME bool`` precedes ``LOAD_NAME bool`` for that one statement),
    so the name immediately shadows itself WITHIN THE SAME STATEMENT and the
    class's own raw ``__annotations__`` entry is already wrong -- ``False``,
    not the builtin ``bool`` -- before ``typing.get_type_hints`` (or any
    other introspection) ever sees it. There is no way to recover the
    intended type from here; the best we can do is detect the symptom (a
    "type" that plainly isn't one) and raise a CLEAR, actionable error
    instead of the confusing `argparse` internals crash this used to produce
    when it later chose an action based on this bogus, non-type "type".
    """
    if isinstance(value, type):
        return True
    if isinstance(value, str):
        # An unresolved forward-ref string is a legitimate (if unusual at
        # this point) intermediate value, not the shadow symptom.
        return True
    # A typing construct (Optional[int], list[str], Literal[...], ...) has
    # __origin__ or lives under the `typing` module's machinery -- accept
    # anything that isn't a plain, mundane instance of a builtin scalar type
    # a class body could plausibly have assigned as an accidental value.
    if _ty.get_origin(value) is not None:
        return True
    if type(value).__module__ in ("typing", "types"):
        return True
    # The actual symptom: a bare bool/int/float/str/bytes/NoneType instance
    # sitting where a type was expected -- exactly what `name: name = <that
    # same value>` self-shadowing produces.
    return not isinstance(value, (bool, int, float, str, bytes, type(None)))


def get_clsargs(cls: type) -> "dict[str, ClsArgDeclaration]":
    if "_duho_clsargs_" in vars(cls):
        return cls._duho_clsargs_  # type:ignore

    typehints = _ty.get_type_hints(cls, include_extras=True)
    constants = get_clsargs_constants(cls)
    args: "dict[str, ClsArgDeclaration]" = {}
    for name, type in typehints.items():
        if name.startswith("_"):
            continue

        if not _looks_like_a_resolved_type(type):
            raise TypeError(
                f"argument {name!r} on {cls.__name__!r}: its resolved "
                f"annotation is {type!r}, not a type -- this happens when a "
                f"field's NAME is the same as its own annotation (e.g. "
                f"`{name}: {name} = ...`), which makes Python's class-body "
                f"execution shadow the annotation with the field's own "
                f"value before it's ever read (the assignment happens "
                f"before the annotation is evaluated, within the same "
                f"statement -- not a duho bug, a fundamental Python "
                f"scoping order). Rename the field so it no longer matches "
                f"its own declared type."
            )

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
