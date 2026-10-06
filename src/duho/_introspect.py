from __future__ import annotations

import ast as _ast
import functools as _functools
import inspect as _inspect
import io as _io
import linecache as _linecache
import logging as _logging
import os as _os
import re as _re
import sys as _sys
import textwrap as _textwrap
import tokenize as _tokenize
import types as _types
import typing as _ty
from dataclasses import dataclass as _data
from pathlib import Path as _Path

from . import _compat

_LOGGER = _logging.getLogger(__name__)

# Classes from these modules are never user-defined Args mixins; skip scanning
# their source entirely (stops re-parsing e.g. argparse.py for Namespace).
_SKIP_MODULES = frozenset({"argparse", "builtins", "typing"})

# ``try/except*`` (PEP 654, 3.11+) has the same body fields as ``Try``; looked
# up with ``getattr`` so it is skipped on 3.9/3.10, where the type is absent.
_TRY_TYPES = tuple(
    t for t in (_ast.Try, getattr(_ast, "TryStar", None)) if t is not None
)

#: PEP 695 ``type X = ...`` aliases (3.12+); ``None`` (never matches
#: ``isinstance``) on older floors.
_TypeAliasType = getattr(_ty, "TypeAliasType", None)


@_functools.lru_cache(maxsize=None)
def _module_index(filename: str) -> dict[str, list[_ast.ClassDef]]:
    """Parse a source file once and index every ClassDef by qualname.

    Qualnames match ``__qualname__`` (``Name.`` for a class scope,
    ``name.<locals>.`` for a function), so nested and function-local classes
    resolve. A qualname maps to a list: an if/else or try/except fallback can
    define it twice (see ``getclsdef``). Source is decoded with
    ``tokenize.detect_encoding``, which honors a BOM and a PEP 263 cookie.
    """
    index: dict[str, list[_ast.ClassDef]] = {}
    raw = _Path(filename).read_bytes()
    encoding, _lines = _tokenize.detect_encoding(_io.BytesIO(raw).readline)
    src = raw.decode(encoding)
    tree = _ast.parse(src)

    def walk(body, prefix: str):
        # Walk only statement containers (a ClassDef or FunctionDef occurs only
        # as a statement), skipping the expression subtrees that
        # ``iter_child_nodes`` descends.
        for child in body:
            if isinstance(child, _ast.ClassDef):
                qualname = prefix + child.name
                index.setdefault(qualname, []).append(child)
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


# A block read costs a fixed amount per class, the whole-file index per line
# once. A file gets one block read per this many bytes; beyond that the index
# serves its classes.
_BYTES_PER_BLOCK_READ = 12_000

#: file -> (mtime and size, block-read budget, first lines already read by
#: block); the file is stat-ed once, not per class.
_BLOCK_READS: dict[str, tuple[tuple, int, set[int]]] = {}


@_functools.lru_cache(maxsize=None)
def _classdef_from_block(
    filename: str, firstlineno: int, name: str, nested: bool, stamp: tuple = ()
) -> _ast.ClassDef | None:
    """The ClassDef whose statement starts at line ``firstlineno`` of
    ``filename``, parsed from that block alone; ``None`` on any doubt.

    The block keeps its own indentation (parsed under ``if 1:``, never
    dedented, so string literals are untouched) and the node's line numbers are
    shifted back to the file's. ``stamp`` is the file's mtime and size, so an
    edited file is read again.
    """
    try:
        _linecache.checkcache(filename)
        lines = _linecache.getlines(filename)
        if not 0 < firstlineno <= len(lines):
            return None
        block = _inspect.getblock(lines[firstlineno - 1 :])
        if not block:
            return None
        first = block[0]
        indented = first[:1] in (" ", "\t")
        if nested and not indented:
            return None
        source = "".join(block)
        if indented:
            source = "if 1:\n" + source
        body = _ast.parse(source).body
        if indented:
            if len(body) != 1 or not isinstance(body[0], _ast.If):
                return None
            body = body[0].body
        if len(body) != 1 or not isinstance(body[0], _ast.ClassDef):
            return None
        node = body[0]
        if node.name != name:
            return None
        _ast.increment_lineno(node, firstlineno - (2 if indented else 1))
        return node
    except (OSError, SyntaxError, ValueError, TypeError):
        return None


def _classdef_by_firstlineno(
    cls: type, file: str, qualname: str
) -> _ast.ClassDef | None:
    """Read only ``cls``'s own statement when the interpreter recorded where
    it starts (``__firstlineno__``, 3.13+); ``None`` sends the caller to the
    whole-file index.
    """
    firstlineno = vars(cls).get("__firstlineno__")
    if not isinstance(firstlineno, int):
        return None
    entry = _BLOCK_READS.get(file)
    if entry is None:
        try:
            stat = _os.stat(file)
        except OSError:
            return None
        stamp = (stat.st_mtime_ns, stat.st_size)
        entry = _BLOCK_READS[file] = (
            stamp,
            stat.st_size // _BYTES_PER_BLOCK_READ,
            set(),
        )
    stamp, budget, reads = entry
    if firstlineno not in reads:
        if len(reads) >= budget:
            return None
        reads.add(firstlineno)
    name = qualname.rpartition(".")[2]
    return _classdef_from_block(file, firstlineno, name, "." in qualname, stamp)


def _own_annotations(cls: type) -> dict[str, object]:
    """``cls``'s own (never inherited) annotations, without raising on one that
    cannot be evaluated: on 3.14 (lazy annotations) such a value comes back as
    a ``ForwardRef``; before that ``vars(cls)`` holds them already evaluated.
    """
    try:
        import annotationlib  # type: ignore[import-not-found]  # 3.14+
    except ImportError:
        return dict(vars(cls).get("__annotations__", {}))
    return dict(
        annotationlib.get_annotations(cls, format=annotationlib.Format.FORWARDREF)
    )


def _pick_live_classdef(
    cls: type, candidates: list[_ast.ClassDef]
) -> _ast.ClassDef | None:
    """Pick the ClassDef Python bound to ``cls`` among several sharing one
    qualname (an if/else or try/except fallback defining the same name).

    ``cls.__firstlineno__`` (3.13+) settles it exactly. Before 3.13, candidates
    are matched on ``cls``'s own annotated field names and docstring; if
    several still match, the last in source order wins.
    """
    if len(candidates) == 1:
        return candidates[0]

    firstlineno = getattr(cls, "__firstlineno__", None)
    if firstlineno is not None:
        # `__firstlineno__` is the first decorator's line for a decorated
        # class on 3.13+, but the `class` line of its ClassDef node.
        for node in candidates:
            lines = {node.lineno, *(d.lineno for d in node.decorator_list)}
            if firstlineno in lines:
                return node

    own_annotations = set(_own_annotations(cls))
    own_doc = cls.__doc__

    def _annotated_names(node: _ast.ClassDef) -> set[str]:
        return {
            stmt.target.id
            for stmt in node.body
            if isinstance(stmt, _ast.AnnAssign) and isinstance(stmt.target, _ast.Name)
        }

    matches = [
        node
        for node in candidates
        if _annotated_names(node) == own_annotations
        and _ast.get_docstring(node) == own_doc
    ]
    if matches:
        return matches[-1]
    return candidates[-1]


def getclsdef(cls: type) -> _ty.Optional[_ast.ClassDef]:
    """Locate the ClassDef AST node for cls. Never raises."""
    try:
        module = _sys.modules.get(getattr(cls, "__module__", None))
        file = getattr(module, "__file__", None)
        if file:
            qualname = getattr(cls, "__qualname__", cls.__name__)
            # OSError (a zipapp's ``__file__``), SyntaxError and ValueError (a bad
            # encoding cookie) are caught here so the ``getsource`` fallback below
            # still runs, not the outer ``except``.
            node = _classdef_by_firstlineno(cls, file, qualname)
            if node is not None:
                return node
            try:
                index = _module_index(file)
            except (OSError, SyntaxError, ValueError):
                index = None
            if index is not None:
                found = index.get(qualname)
                if found is not None:
                    return _pick_live_classdef(cls, found)
                # Indexed but absent: the class was created dynamically, and
                # ``inspect.getsource`` would fail the same lookup more slowly
                # (up to ~23 ms per class). The fallback is for no readable file.
                return None

        src = _inspect.getsource(cls)
        src = _textwrap.dedent(src)
        for node in _ast.walk(_ast.parse(src)):
            if isinstance(node, _ast.ClassDef) and node.name == cls.__name__:
                return node
        return None
    except (OSError, TypeError, SyntaxError, ValueError):
        # ValueError covers UnicodeDecodeError from the getsource fallback (which
        # reads via tokenize/linecache) as well as other malformed-source cases.
        return None


_MODULE_SOURCE_READABLE: dict[str, bool] = {}


def _module_source_readable(module_name: str | None) -> bool:
    """True when `module_name`'s source text can be read (cached per module).

    Tells a class created at runtime in an ordinary source module (its module
    reads fine; the class just has no body there) apart from a class whose
    whole module has no readable source (a frozen app, a `.pyc`-only install,
    a zipapp, the REPL), where class-body declarations are really lost.
    """
    if not module_name:
        return False
    cached = _MODULE_SOURCE_READABLE.get(module_name)
    if cached is not None:
        return cached
    module = _sys.modules.get(module_name)
    try:
        readable = module is not None and bool(_inspect.getsource(module))
    except (OSError, TypeError, ValueError):
        readable = False
    _MODULE_SOURCE_READABLE[module_name] = readable
    return readable


class NotDefined: ...


NOT_DEFINED = NotDefined()


@_data
class ClsArgDeclaration:
    default: object
    type: type
    annotations: list
    docstring: str
    exprs: list


#: A lone help literal that is only a flag (`-x`, `--long-name`, `--`): a
#: one-element flag tuple written without its trailing comma.
_FLAG_SHAPED = _re.compile(r"--?[A-Za-z][\w-]*|--")


def _merge_help_literals(cls: type, name: str, literals: list) -> list:
    """Reduce a field's literal statements to ``[help?, *non-string literals]``.

    The help is the first run of consecutive string literals, joined with one
    space, wherever that run stands among the field's other literals. A help
    that is a single flag-shaped token is a build-time error.
    """
    run: list[str] | None = None
    in_run = False
    others: list = []
    for value in literals:
        if not isinstance(value, str):
            in_run = False
            others.append(value)
        elif run is None:
            run, in_run = [value], True
        elif in_run:
            run.append(value)
    if run is None:
        return others
    if len(run) == 1 and _FLAG_SHAPED.fullmatch(run[0]):
        raise ValueError(
            f"argument {name!r} on {cls.__name__!r}: the string {run[0]!r} "
            f"after the field would become its help text; a one-element flag "
            f"tuple needs a trailing comma: ({run[0]!r},)"
        )
    return [" ".join(run)] + others


def _class_constants(cls: type) -> dict[str, list]:
    """Scan a single class body for name -> [docstring?, *exprs] lists.

    Cached on the class itself (checked via vars(), not getattr, so
    inheritance can't false-hit a parent's cache).
    """
    if cls is object:
        return {}

    if "_duho_constants_" in vars(cls):
        return cls._duho_constants_  # type: ignore

    result: dict[str, list] = {}
    if cls.__module__ not in _SKIP_MODULES:
        clsdef = getclsdef(cls)
        if clsdef is None:
            # `getattr(cls, "__annotations__")` would follow the MRO on 3.9 and
            # report an ancestor's fields as this class's own.
            has_own_annotations = bool(_own_annotations(cls))
            if has_own_annotations:
                # No locatable source (freeze, .pyc-only, REPL, zipapp) silently
                # loses flags, env and help, and this runs before ``-v`` applies,
                # so warn; a class made at runtime in readable source loses nothing.
                level = (
                    _logging.DEBUG
                    if _module_source_readable(getattr(cls, "__module__", None))
                    else _logging.WARNING
                )
                _LOGGER.log(
                    level,
                    "duho: no source ClassDef found for %s.%s; class-body flags, "
                    "env, and attribute docstrings will be unavailable",
                    getattr(cls, "__module__", "?"),
                    getattr(cls, "__qualname__", getattr(cls, "__name__", cls)),
                )
        else:
            argument = None
            for node in clsdef.body:
                if isinstance(node, (_ast.Assign, _ast.AnnAssign)):
                    if isinstance(node, _ast.Assign):
                        if len(node.targets) == 1 and isinstance(
                            node.targets[0], _ast.Name
                        ):
                            argument = node.targets[0].id
                        else:
                            argument = None
                    else:
                        target = node.target
                        argument = target.id if isinstance(target, _ast.Name) else None
                elif isinstance(node, _ast.Expr) and argument:
                    try:
                        value = _ast.literal_eval(node.value)
                    except (ValueError, TypeError, SyntaxError):
                        # A non-literal expression (a call, a name) ends the
                        # current field's metadata run: reset attribution so a
                        # LATER literal/docstring is not misattributed to it.
                        argument = None
                    else:
                        result.setdefault(argument, []).append(value)
                else:
                    argument = None
            for name in result:
                result[name] = _merge_help_literals(cls, name, result[name])

    try:
        setattr(cls, "_duho_constants_", result)
    except TypeError:
        pass  # some builtin/extension types forbid attribute assignment
    return result


def get_clsargs_constants(cls: type) -> dict[str, list]:
    """Resolve each field's ``[docstring?, *exprs]`` list across ``cls``'s MRO.

    The docstring and the non-docstring expressions (flags tuple, ``env()``
    call, ...) are resolved INDEPENDENTLY, each by the first class in MRO
    order (``cls`` itself first, then its bases) that supplies one -- never by
    flattening every class's own list together and reading position 0 of the
    result. A subclass that overrides only the flags still inherits the
    base's help text, and one that overrides only the help text still
    inherits the base's flags.
    """
    per_class: dict[type, dict[str, list]] = {}
    names: set[str] = set()
    for base in cls.__mro__:
        own = _class_constants(base)
        per_class[base] = own
        names.update(own)

    result: dict[str, list] = {}
    for name in names:
        docstring = None
        exprs: list = []
        exprs_found = False
        for base in cls.__mro__:
            own = per_class[base].get(name)
            if not own:
                continue
            has_doc = isinstance(own[0], str)
            if docstring is None and has_doc:
                docstring = own[0]
            if not exprs_found:
                rest = own[1:] if has_doc else own
                if rest:
                    exprs = rest
                    exprs_found = True
            if docstring is not None and exprs_found:
                break
        result[name] = ([docstring] if docstring is not None else []) + exprs
    return result


def _is_routine_or_descriptor(value) -> bool:
    if _inspect.isroutine(value):
        return True
    return hasattr(type(value), "__get__")


def _looks_like_a_resolved_type(value: object) -> bool:
    """True if ``value`` is a plausible resolved type hint (a type or typing
    construct), False for any other kind of object.

    Detects a field named like its own annotation (``bool: bool = False``):
    the assignment stores the value before the annotation is evaluated, so the
    raw ``__annotations__`` entry is ``False``. :func:`_resolve_public_type_hints`
    recovers the type from source; without source the caller reports it.
    """
    if isinstance(value, type):
        return True
    if isinstance(value, str):
        # An unresolved forward-ref string is a legitimate (if unusual at
        # this point) intermediate value, not the shadow symptom.
        return True
    # A typing construct has ``__origin__`` or lives in ``typing``/``types``;
    # anything but a plain builtin scalar instance passes.
    if _ty.get_origin(value) is not None:
        return True
    if type(value).__module__ in ("typing", "types"):
        return True
    # The actual symptom: a bare bool/int/float/str/bytes/NoneType instance
    # sitting where a type was expected -- exactly what `name: name = <that
    # same value>` self-shadowing produces.
    return not isinstance(value, (bool, int, float, str, bytes, type(None)))


def _raw_public_annotations(base: type) -> dict[str, object]:
    """``base``'s own public annotations, unevaluated where possible.

    With source, each value is the annotation's source text (``ast.unparse``),
    never executed, so a field named like its type (``bool: bool = False``)
    resolves correctly and each field can be evaluated alone on 3.14.
    ``annotationlib``'s STRING format is not used: after a VALUE-format
    evaluation it can embed a lambda's ``repr()`` (``<function ... at 0x...>``),
    which never evaluates. Without source it falls back to ``__annotations__``.
    Private names are dropped before resolution.
    """
    clsdef = None if base.__module__ in _SKIP_MODULES else getclsdef(base)
    if clsdef is not None:
        raw: dict[str, object] = {}
        for node in clsdef.body:
            if isinstance(node, _ast.AnnAssign) and isinstance(node.target, _ast.Name):
                raw[node.target.id] = _ast.unparse(node.annotation)
    else:
        raw = _own_annotations(base)
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def _resolve_public_type_hints(cls: type) -> dict[str, object]:
    """Resolve every PUBLIC annotation on ``cls``.

    The primary path is ``typing.get_type_hints(cls, include_extras=True)``, so
    a function-local class in an annotation resolves. If it raises, the
    isolated fallback resolves each public field alone, which survives a
    private field's unresolvable annotation and, on 3.14 (PEP 649), a field
    name shadowing an earlier annotation (``names: list[str]`` then
    ``list: bool``). The fallback sees module globals only, so a function-local
    name is not resolvable there.
    """
    try:
        hints = _ty.get_type_hints(cls, include_extras=True)
    except Exception:
        return _resolve_public_type_hints_isolated(cls)
    public = {name: hint for name, hint in hints.items() if not name.startswith("_")}
    if all(_looks_like_a_resolved_type(hint) for hint in public.values()):
        return public
    # A field named like its own annotation: the source text still says what
    # it was meant to be. Without readable source the caller reports the error.
    try:
        isolated = _resolve_public_type_hints_isolated(cls)
    except Exception:
        return public
    if set(isolated) == set(public) and all(
        _looks_like_a_resolved_type(hint) for hint in isolated.values()
    ):
        return isolated
    return public


def _resolve_public_type_hints_isolated(cls: type) -> dict[str, object]:
    """Resolve every PUBLIC annotation on ``cls`` (its MRO), one field at a time
    (see :func:`_resolve_public_type_hints`, the only caller).

    Each name is resolved by ``typing.get_type_hints`` on a throwaway
    single-field class with the same ``__module__``, so nothing in its namespace
    shadows or is shadowed. A failure is re-raised naming the real class and
    field.
    """
    names: dict[str, tuple[str, object]] = {}
    for base in reversed(cls.__mro__):
        if base is object:
            continue
        for name, raw in _raw_public_annotations(base).items():
            names[name] = (getattr(base, "__module__", None), raw)

    hints: dict[str, object] = {}
    for name, (module_name, raw) in names.items():
        probe = type(
            "_duho_annotation_probe_",
            (),
            {"__annotations__": {name: raw}, "__module__": module_name},
        )
        # Explicit globalns: with both left None, ``get_type_hints`` swaps
        # globals and locals, so a lambda in the annotation would get the probe's
        # empty namespace and raise NameError when called.
        module_globals = getattr(_sys.modules.get(module_name), "__dict__", {})
        try:
            resolved = _ty.get_type_hints(
                probe, module_globals, {}, include_extras=True
            )
        except Exception as exc:
            raise TypeError(
                f"argument {name!r} on {cls.__name__!r}: its annotation "
                f"{raw!r} could not be resolved ({exc.__class__.__name__}: "
                f"{exc}) -- a string (or `from __future__ import "
                f"annotations`) annotation is evaluated against "
                f"{module_name!r}'s MODULE-level names only; a class, enum, "
                f"or alias defined locally (inside a function, or only "
                f"under `if TYPE_CHECKING:`) is not resolvable here"
            ) from exc
        hints[name] = resolved[name]
    return hints


def _unwrap_annotated(hint, name: str, cls: type) -> tuple[object, list]:
    """Peel a PEP 695 alias and/or ``Annotated`` metadata off ``hint``.

    Handles a bare ``Annotated[T, ...]``, a PEP 695 ``type X = Annotated[...]``
    (3.12+, via ``__value__``), and a Union/Optional with exactly one
    ``Annotated`` member, whose metadata is lifted; more than one raises.
    Returns ``(bare_type, metadata_list)``.
    """
    if _TypeAliasType is not None and isinstance(hint, _TypeAliasType):
        hint = hint.__value__

    if hasattr(hint, "__metadata__"):
        return hint.__origin__, list(hint.__metadata__)

    if _ty.get_origin(hint) in _compat.UNION_ORIGINS:
        members = _ty.get_args(hint)
        annotated = [m for m in members if hasattr(m, "__metadata__")]
        if len(annotated) == 1:
            carrier = annotated[0]
            metadata = list(carrier.__metadata__)
            bare_members = tuple(m.__origin__ if m is carrier else m for m in members)
            return _ty.Union[bare_members], metadata
        if len(annotated) > 1:
            raise TypeError(
                f"argument {name!r} on {cls.__name__!r}: more than one "
                f"member of its Union/Optional annotation carries "
                f"Arg[...]/NS(...) metadata -- put it on a single member "
                f"(e.g. Optional[Arg[int, NS(...)]]), not several"
            )

    return hint, []


def _resolve_string_members(hint, cls: type, name: str):
    """Replace a quoted member inside a builtin generic or Union (``list["Color"]``,
    ``dict[str, "int"]``) with the object it names.

    Python 3.9 leaves such a member a plain ``str`` (3.11+ resolves it); it is
    evaluated against the module globals of ``cls`` and its bases. A name that
    cannot be resolved raises a ``ValueError`` naming the field.
    """
    if isinstance(hint, str):
        namespace: dict[str, object] = {}
        for base in reversed(cls.__mro__):
            namespace.update(getattr(_sys.modules.get(base.__module__), "__dict__", {}))
        try:
            return eval(hint, namespace)  # noqa: S307 - the user's own annotation text
        except Exception as exc:
            raise ValueError(
                f"argument {name!r} on {cls.__name__!r}: the quoted type "
                f"{hint!r} could not be resolved ({exc.__class__.__name__}: "
                f"{exc}); define it at module level of {cls.__module__!r}, or "
                f"use typing.List[...]/an unquoted name"
            ) from None
    origin = _ty.get_origin(hint)
    if origin not in (list, set, frozenset, tuple, dict) and (
        origin not in _compat.UNION_ORIGINS
    ):
        return hint
    args = _ty.get_args(hint)
    resolved = tuple(
        a if a is Ellipsis else _resolve_string_members(a, cls, name) for a in args
    )
    if all(new is old for new, old in zip(resolved, args)):
        return hint
    if origin in _compat.UNION_ORIGINS:
        return _ty.Union[resolved]
    return _types.GenericAlias(origin, resolved)


def get_clsargs(cls: type) -> dict[str, ClsArgDeclaration]:
    """Build each declared field's :class:`ClsArgDeclaration` for ``cls``.

    Combines the AST-derived docstrings/flags/env exprs
    (:func:`get_clsargs_constants`) with the resolved type hints
    (:func:`_resolve_public_type_hints`), skipping ``ClassVar``/``Final``
    declarations and unwrapping ``Annotated``/PEP 695 alias metadata
    (:func:`_unwrap_annotated`). Cached on ``cls`` itself.
    """
    if "_duho_clsargs_" in vars(cls):
        return cls._duho_clsargs_  # type: ignore

    typehints = _resolve_public_type_hints(cls)
    constants = get_clsargs_constants(cls)
    args: dict[str, ClsArgDeclaration] = {}
    for name, hint in typehints.items():
        if not _looks_like_a_resolved_type(hint):
            raise TypeError(
                f"argument {name!r} on {cls.__name__!r}: its resolved "
                f"annotation is {hint!r}, not a type -- this happens when a "
                f"field's NAME is the same as its own annotation (e.g. "
                f"`{name}: {name} = ...`), which makes Python's class-body "
                f"execution shadow the annotation with the field's own "
                f"value before it's ever read (the assignment happens "
                f"before the annotation is evaluated, within the same "
                f"statement -- not a duho bug, a fundamental Python "
                f"scoping order). Rename the field so it no longer matches "
                f"its own declared type."
            )

        # ClassVar/Final are declarations, not CLI fields; a bare (unsubscripted)
        # one is caught by the identity check.
        if hint is _ty.ClassVar or hint is _ty.Final:
            continue
        if _ty.get_origin(hint) in (_ty.ClassVar, _ty.Final):
            continue

        hint, annotations = _unwrap_annotated(hint, name, cls)
        hint = _resolve_string_members(hint, cls, name)

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
            type=hint,
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


__all__ = [
    "getclsdef",
    "NotDefined",
    "NOT_DEFINED",
    "ClsArgDeclaration",
    "get_clsargs",
    "get_clsargs_constants",
]
