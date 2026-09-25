import ast as _ast
import functools as _functools
import inspect as _inspect
import io as _io
import logging as _logging
import sys as _sys
import textwrap as _textwrap
import tokenize as _tokenize
import typing as _ty
from dataclasses import dataclass as _data
from pathlib import Path as _Path

from . import _compat

_LOGGER = _logging.getLogger(__name__)

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

#: PEP 695 ``type X = ...`` aliases (3.12+); ``None`` (never matches
#: ``isinstance``) on older floors.
_TypeAliasType = getattr(_ty, "TypeAliasType", None)


@_functools.lru_cache(maxsize=None)
def _module_index(filename: str) -> "dict[str, list[_ast.ClassDef]]":
    """Parse a source file once and index every ClassDef by qualname.

    Qualname is reconstructed by walking the tree while tracking the
    enclosing scope chain: entering a ClassDef appends "Name.", entering a
    Function/AsyncFunctionDef appends "name.<locals>." -- this reproduces
    __qualname__ exactly, so nested and function-local classes resolve.

    A qualname maps to a LIST, not a single node: the same qualname can be
    defined more than once in one file (an if/else branch, a
    try/except ImportError fallback) -- see ``getclsdef`` for how the live
    one is picked.

    Source is read as BYTES and decoded via ``tokenize.detect_encoding``,
    which honors both a UTF-8 BOM and a PEP 263 ``# -*- coding: ... -*-``
    cookie (this previously forced a plain UTF-8 ``read_text``, which raised
    on either).
    """
    index: "dict[str, list[_ast.ClassDef]]" = {}
    raw = _Path(filename).read_bytes()
    encoding, _lines = _tokenize.detect_encoding(_io.BytesIO(raw).readline)
    src = raw.decode(encoding)
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


def _pick_live_classdef(
    cls: type, candidates: "list[_ast.ClassDef]"
) -> "_ast.ClassDef | None":
    """Pick the ClassDef Python actually bound to ``cls`` out of several
    sharing one qualname (an if/else or try/except fallback both defining the
    same name).

    Matches by ``node.lineno`` against ``inspect.getsourcelines(cls)[1]`` (the
    real class's own start line); falls back to the LAST candidate (the prior
    behavior) when that can't be determined.
    """
    if len(candidates) == 1:
        return candidates[0]
    try:
        start_line = _inspect.getsourcelines(cls)[1]
    except (OSError, TypeError):
        start_line = None
    if start_line is not None:
        for node in candidates:
            if node.lineno == start_line:
                return node
    return candidates[-1]


def getclsdef(cls: type) -> "_ast.ClassDef | None":
    """Locate the ClassDef AST node for cls. Never raises."""
    try:
        module = _sys.modules.get(getattr(cls, "__module__", None))
        file = getattr(module, "__file__", None)
        if file:
            qualname = getattr(cls, "__qualname__", cls.__name__)
            # `_module_index` reads and parses the file -- `OSError` when
            # `file` isn't a real filesystem path (e.g. a zipapp's
            # `module.__file__` is a zip-internal path that doesn't exist on
            # disk), or `SyntaxError`/`ValueError` when the encoding cookie
            # names a codec that can't decode the bytes, or is otherwise
            # unrecognizable. All three are caught HERE, narrowly, so the
            # failure falls through to the `inspect.getsource` fallback below
            # (which reads through `tokenize`/`linecache` and handles a BOM
            # or PEP 263 cookie correctly) instead of propagating to the
            # outer `except`, which would return None before ever trying it.
            try:
                index = _module_index(file)
            except (OSError, SyntaxError, ValueError):
                index = None
            if index is not None:
                found = index.get(qualname)
                if found is not None:
                    return _pick_live_classdef(cls, found)
                # The module file WAS indexed successfully but this qualname is
                # absent -- the class was created dynamically (``type(...)`` /
                # ``duho.command(...)``) and has no literal ``ClassDef`` in the
                # source. ``inspect.getsource`` re-parses the exact same file and
                # fails the identical lookup, only slower (up to ~23 ms per class in
                # a large dynamically-built tree, P5). Give up now. The getsource
                # fallback below is reserved for the no-module-file case
                # (REPL/``exec``) or the unreadable/undecodable-file case above.
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
        return cls._duho_constants_  # type: ignore

    result: "dict[str, list]" = {}
    if cls.__module__ not in _SKIP_MODULES:
        clsdef = getclsdef(cls)
        if clsdef is None:
            if getattr(cls, "__annotations__", None):
                # A class with annotated fields whose source we could not locate
                # (a PyInstaller/py2exe freeze, a .pyc-only install, Nuitka, REPL/
                # exec, zipapp) silently loses its flags/env/docstrings -- every
                # field falls back to a derived `--field-name` option, a
                # no-default positional becomes a REQUIRED option, and short
                # aliases/help text vanish. This happens while the parser
                # is still being BUILT, before an app's own `-v`/`--loglevel`
                # could raise the level to see a DEBUG-level diagnostic, so it
                # must be loud enough to be seen by default (this runs once per
                # class -- the result is cached below).
                _LOGGER.warning(
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

    try:
        setattr(cls, "_duho_constants_", result)
    except TypeError:
        pass  # some builtin/extension types forbid attribute assignment
    return result


def get_clsargs_constants(cls: type) -> "dict[str, list]":
    """Resolve each field's ``[docstring?, *exprs]`` list across ``cls``'s MRO.

    The docstring and the non-docstring expressions (flags tuple, ``env()``
    call, ...) are resolved INDEPENDENTLY, each by the first class in MRO
    order (``cls`` itself first, then its bases) that supplies one -- never by
    flattening every class's own list together and reading position 0 of the
    result. A subclass that overrides only the flags still inherits the
    base's help text, and one that overrides only the help text still
    inherits the base's flags.
    """
    per_class: "dict[type, dict[str, list]]" = {}
    names: "set[str]" = set()
    for base in cls.__mro__:
        own = _class_constants(base)
        per_class[base] = own
        names.update(own)

    result: "dict[str, list]" = {}
    for name in names:
        docstring = None
        exprs: "list" = []
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


def _raw_public_annotations(base: type) -> "dict[str, object]":
    """``base``'s OWN (not inherited) public annotations, UNEVALUATED where
    possible.

    When ``base``'s source is available (the common case), each value is the
    EXACT source text of the annotation expression, extracted via AST
    (``ast.unparse`` on the ``AnnAssign`` node) -- never executed, and never
    read back off the class's own (possibly self-shadowed, possibly not-yet-
    fully-built) namespace. This is what lets :func:`_resolve_public_type_hints`
    evaluate each field in complete isolation on 3.14 (see there for why that
    matters), and as a side effect it also makes a field whose NAME equals a
    builtin type it's annotated with (``bool: bool = False``) resolve
    correctly instead of seeing its own already-reassigned VALUE -- the
    source text says "bool" regardless of what class-body execution order
    later does to the name ``bool`` in the class namespace.

    Trying ``annotationlib.get_annotations(base, format=Format.STRING)``
    instead (3.14+) was rejected: when a class body already forced VALUE-format
    evaluation (nothing unusual -- duho's own ``LoggingArgs.loglevels`` did,
    just by being imported), 3.14's own STRING-format fallback re-derives text
    via ``repr()`` of the cached values instead of real source, and a `lambda`
    inside an annotation (e.g. ``NS(help=lambda self: ...)``) evaluates for
    real even under STRING format's "stringizer", embedding a live function's
    ``repr()`` (``<function ... at 0x...>``) into the "source" -- not valid
    Python, so re-evaluating it always raises. Reading real source via AST has
    neither problem.

    When source isn't available (a frozen build, REPL/exec, a dynamically
    created class -- the existing gap, where flags/docstrings are
    already unavailable too), falls back to whatever ``__annotations__``
    already holds -- accepting, in this rare case only, both the runtime
    self-shadow risk and (3.14 only) the cross-field entanglement risk this
    function otherwise avoids.

    Private (``_``-prefixed) names are dropped here, before anything ever
    tries to resolve them: a private field's unresolvable annotation (a
    function-local type, a ``TYPE_CHECKING``-only import) must never crash
    parser build for a name nobody will ever see as a CLI flag.
    """
    clsdef = None if base.__module__ in _SKIP_MODULES else getclsdef(base)
    if clsdef is not None:
        raw: "dict[str, object]" = {}
        for node in clsdef.body:
            if isinstance(node, _ast.AnnAssign) and isinstance(node.target, _ast.Name):
                raw[node.target.id] = _ast.unparse(node.annotation)
    else:
        raw = dict(vars(base).get("__annotations__", {}))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def _resolve_public_type_hints(cls: type) -> "dict[str, object]":
    """Resolve every PUBLIC annotation on ``cls``.

    The fast, PRIMARY path is plain ``typing.get_type_hints(cls,
    include_extras=True)`` -- unchanged from before this fix, so a function-
    local class/enum referenced by a public field's annotation (a real,
    existing, exercised feature: the annotation is not a string at all in
    the common case, already evaluated eagerly -- or, on 3.14, lazily via a
    real closure that still sees the enclosing function's locals) keeps
    working exactly as it always has.

    Only if that raises does this fall back to
    :func:`_resolve_public_type_hints_isolated`, which re-resolves each
    PUBLIC field completely independently of every other -- fixing the two
    failure modes without weakening the common case:

    * A private field's annotation can't be resolved at all (a class or enum
      defined inside a function, a ``TYPE_CHECKING``-only import) and
      currently aborts the WHOLE class's ``get_type_hints`` call, even though
      that name will never become a CLI flag. The fallback filters private
      names out before ever attempting to resolve them.
    * On 3.14 (PEP 649/749), a class's annotations are evaluated together as
      ONE function, seeing the class's FINAL namespace -- so a class with
      ``names: list[str]`` followed later by ``list: bool`` (a ``--list``
      flag) resolves the EARLIER field's ``list[str]`` against the LATER
      field's own name, raising ``TypeError: 'bool' object is not
      subscriptable``. This never happens on 3.9-3.13, where annotations are
      evaluated eagerly, in source order, each seeing only what was defined
      *before* it. The fallback resolves each field in total isolation, so
      one field's name can never shadow another's.

    The fallback's own trade-off (documented, not perfect): since it
    re-evaluates each field's annotation SOURCE TEXT against its MODULE's
    globals only, a public field whose annotation itself references a
    function-local name is not resolvable there either -- but that combination
    (a class already failing the fast path for some OTHER reason, AND a
    surviving field needing function-local scope) is not a shape duho has ever
    supported cleanly, and the fallback at least names the class and field
    instead of crashing opaquely.
    """
    try:
        hints = _ty.get_type_hints(cls, include_extras=True)
    except Exception:
        return _resolve_public_type_hints_isolated(cls)
    return {name: hint for name, hint in hints.items() if not name.startswith("_")}


def _resolve_public_type_hints_isolated(cls: type) -> "dict[str, object]":
    """Resolve every PUBLIC annotation on ``cls`` (its own MRO), one field at
    a time, in complete isolation from every OTHER field. See
    :func:`_resolve_public_type_hints` (the only caller) for when and why.

    Reuses ``typing.get_type_hints``'s own forward-ref/``Optional``/
    ``include_extras`` machinery UNCHANGED, rather than reimplementing it, by
    handing it a throwaway single-field "class" per name: same ``__module__``
    (so module-level forward refs still resolve normally) holding ONLY that
    one field's own raw annotation -- nothing else in its namespace to be
    shadowed by, or to shadow. A resolution failure is re-raised naming the
    real class and field, never a bare ``NameError``/``TypeError`` pointing at
    neither.
    """
    names: "dict[str, tuple[str, object]]" = {}
    for base in reversed(cls.__mro__):
        if base is object:
            continue
        for name, raw in _raw_public_annotations(base).items():
            names[name] = (getattr(base, "__module__", None), raw)

    hints: "dict[str, object]" = {}
    for name, (module_name, raw) in names.items():
        probe = type(
            "_duho_annotation_probe_",
            (),
            {"__annotations__": {name: raw}, "__module__": module_name},
        )
        # `globalns`/`localns` are passed EXPLICITLY (never both left `None`):
        # `typing.get_type_hints`, when a caller leaves both unset, SWAPS its
        # own module-globals and class-locals internally (a documented
        # `typing` quirk letting an inner class reference an outer one) --
        # which would give a `lambda` embedded in the annotation (e.g.
        # `NS(help=lambda: f"...{_module_global}...")`) the probe's own
        # near-empty namespace as `__globals__` instead of the real module,
        # so it raises `NameError` the first time it's actually CALLED, long
        # after parser build. Passing the real module dict as `globalns`
        # explicitly bypasses that swap.
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


def _unwrap_annotated(hint, name: str, cls: type) -> "tuple[object, list]":
    """Peel a PEP 695 alias and/or ``Annotated`` metadata off ``hint``.

    Handles three shapes:

    * a bare ``Annotated[T, *metadata]`` (the common case);
    * a PEP 695 ``type X = Annotated[T, *metadata]`` alias (3.12+), unwrapped
      via ``__value__`` before the ``Annotated`` check;
    * a ``Union``/``Optional`` with exactly ONE ``Annotated`` member (e.g.
      ``Optional[Arg[int, NS(...)]]``) -- lifts that member's metadata and
      rebuilds the union from the bare types. More than one
      ``Annotated`` member is ambiguous and raises, naming the field.

    Returns ``(bare_type, metadata_list)``; ``metadata_list`` is ``[]`` when
    ``hint`` carries none.
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


def get_clsargs(cls: type) -> "dict[str, ClsArgDeclaration]":
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
    args: "dict[str, ClsArgDeclaration]" = {}
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

        # ClassVar/Final are declarations, not CLI fields: a `count: ClassVar[int]`
        # or `MAX: Final[int]` must never become a `--count`/`--max` flag.
        # `get_origin(ClassVar[int]) is ClassVar` on 3.9+; a bare `ClassVar`/
        # `Final` (unsubscripted) is caught by the identity check.
        if hint is _ty.ClassVar or hint is _ty.Final:
            continue
        if _ty.get_origin(hint) in (_ty.ClassVar, _ty.Final):
            continue

        hint, annotations = _unwrap_annotated(hint, name, cls)

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
