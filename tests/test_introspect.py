"""Tests for `duho._introspect` source-scanning: the statement-body-only
qualname walk (P3) and the framework `_duho_constants_` seed (P2).

These cover that a `ClassDef` nested under any statement container -- an
`if`/`else`, a `try`/`except`/`else`/`finally`, a function (`<locals>`), or
another class -- still resolves via `getclsdef`/`_module_index`, and that user
`Args` subclasses whose class body declares real fields (flags-tuples,
attribute docstrings) still get scanned even though the framework bases are
seeded to skip scanning.
"""

import ast
import importlib.util
import sys
import typing

import duho
from duho import Args, _introspect

# --- classes nested under every statement container -------------------------
# Each is defined at module scope in THIS file (which has a real __file__), so
# getclsdef resolves them through _module_index, exercising the P3 walk.

if typing.TYPE_CHECKING or True:

    class _UnderIf(duho.Args):
        """Under an if."""

        alpha: str = "a"
        "The alpha field."
        ("--alpha",)

else:  # pragma: no cover - the else branch also defines a class to index

    class _UnderElse(duho.Args):
        """Under an else."""

        beta: str = "b"


try:

    class _UnderTry(duho.Args):
        """Under a try."""

        gamma: str = "g"
        ("--gamma",)

except Exception:  # pragma: no cover

    class _UnderExcept(duho.Args):
        """Under an except."""

else:

    class _UnderTryElse(duho.Args):
        """Under a try/else."""

        delta: str = "d"
        ("--delta",)

finally:

    class _UnderFinally(duho.Args):
        """Under a finally."""

        epsilon: str = "e"
        ("--epsilon",)


def _make_local():
    class _LocalCls(duho.Args):
        """A function-local class."""

        zeta: str = "z"
        ("--zeta",)

        class _Inner(duho.Args):
            """Class nested inside a function-local class."""

            eta: str = "h"

    return _LocalCls


class _Outer(duho.Args):
    """Outer class."""

    class _Nested(duho.Args):
        """Nested class."""

        theta: str = "t"
        ("--theta",)


def test_class_under_if_resolves():
    node = _introspect.getclsdef(_UnderIf)
    assert isinstance(node, ast.ClassDef) and node.name == "_UnderIf"
    # And its class-body flag/docstring metadata is scanned (P2 seed on the
    # framework base does not stop scanning a real field-declaring subclass).
    decl = _introspect.get_clsargs(_UnderIf)["alpha"]
    assert decl.docstring == "The alpha field."


def test_class_under_try_resolves():
    assert _introspect.getclsdef(_UnderTry).name == "_UnderTry"
    assert "gamma" in _introspect.get_clsargs(_UnderTry)


def test_class_under_try_else_and_finally_resolve():
    assert _introspect.getclsdef(_UnderTryElse).name == "_UnderTryElse"
    assert _introspect.getclsdef(_UnderFinally).name == "_UnderFinally"
    assert "delta" in _introspect.get_clsargs(_UnderTryElse)
    assert "epsilon" in _introspect.get_clsargs(_UnderFinally)


def test_function_local_class_resolves():
    local = _make_local()
    node = _introspect.getclsdef(local)
    assert node is not None and node.name == "_LocalCls"
    assert "zeta" in _introspect.get_clsargs(local)


def test_class_in_class_resolves():
    assert _introspect.getclsdef(_Outer._Nested).name == "_Nested"
    assert "theta" in _introspect.get_clsargs(_Outer._Nested)


def test_deeply_nested_class_resolves():
    local = _make_local()
    inner = local._Inner
    assert _introspect.getclsdef(inner).name == "_Inner"
    assert "eta" in _introspect.get_clsargs(inner)


# --- P3: identical _module_index output vs the exhaustive iter_child_nodes ---


def _reference_index(filename):
    """The pre-P3 exhaustive walk (iter_child_nodes on every node)."""
    index = {}
    with open(filename, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                qualname = prefix + child.name
                index[qualname] = child.name
                walk(child, qualname + ".")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, prefix + child.name + ".<locals>.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return set(index)


def test_module_index_matches_reference_walk():
    # Cover duho's own sources plus this test file (which nests classes under
    # every statement kind). The statement-only walk must index exactly the same
    # qualnames as the exhaustive iter_child_nodes walk.
    for module in (duho.args, duho._introspect, duho.discovery, duho.logging):
        filename = module.__file__
        _introspect._module_index.cache_clear()
        assert set(_introspect._module_index(filename)) == _reference_index(
            filename
        ), filename
    _introspect._module_index.cache_clear()


# --- P2: framework bases are seeded, user fields still scanned ---------------


def test_framework_bases_have_seeded_constants():
    # Args/Cmd/Cli/LoggingArgs carry their OWN empty _duho_constants_ in
    # vars(), so the AST scan short-circuits for all of them (never re-parses
    # args.py/presets.py on a build).
    assert "_duho_constants_" in vars(duho.Args)
    assert "_duho_constants_" in vars(duho.Cmd)
    assert "_duho_constants_" in vars(duho.Cli)
    assert "_duho_constants_" in vars(duho.LoggingArgs)
    assert vars(duho.Args)["_duho_constants_"] == {}
    assert vars(duho.LoggingArgs)["_duho_constants_"] == {}


def test_logging_args_preset_is_source_independent():
    # LoggingArgs used to be deliberately left UNSEEDED so its class
    # body would be AST-scanned for a trailing docstring + flags-tuple after
    # each field. It now declares every field's flags/help directly as
    # NS(...) metadata instead (read from the live Annotated object, not
    # source), and is seeded like every other framework base -- so its own
    # -v/-q/--loglevel/--verbose/--quiet keep working even when duho's own
    # source can't be found (a PyInstaller/.pyc-only/Nuitka build).
    constants = _introspect.get_clsargs_constants(duho.LoggingArgs)
    assert constants == {}

    clsargs = _introspect.get_clsargs(duho.LoggingArgs)
    assert set(clsargs) == {"loglevels", "verbose", "quiet"}


# --- P5: guard the getsource fallback for dynamically-created classes --------


class _DynArgs(duho.Args):
    """A plain data Args used as a base for command(...)-generated classes."""

    port: int = 8000
    ("--port",)


def test_dynamic_class_build_skips_getsource(monkeypatch):
    # A duho.command(...)-generated class has no literal ClassDef in any source
    # file. _module_index of its (module) file succeeds but the qualname is
    # absent; getclsdef must return None WITHOUT re-parsing via inspect.getsource
    # (which would fail the same lookup, only slower -- P5).
    calls = []
    real_getsource = _introspect._inspect.getsource

    def counting_getsource(obj):
        calls.append(obj)
        return real_getsource(obj)

    monkeypatch.setattr(_introspect._inspect, "getsource", counting_getsource)

    # Build a small tree of generated command classes and their parsers.
    for i in range(5):
        generated = duho.command(_DynArgs, lambda self: None, name="gen%d" % i)
        _introspect._module_index.cache_clear()
        # get_clsargs -> get_clsargs_constants -> _class_constants -> getclsdef
        args = _introspect.get_clsargs(generated)
        assert "port" in args  # inherited field still resolves via the base
        generated._parser_()

    assert calls == [], "getsource must not be called for dynamic classes (P5)"


# --- The same qualname defined twice (if/else, try/except) must pick -------
# --- the branch Python actually ran, never just "the last one in the file" --

_DUP_QUALNAME_SOURCE = '''\
"""Two classes each declared twice under the same qualname."""
import sys

import duho
from duho import Args


if sys.version_info >= (3, 0):

    class Cond(Args):
        """Live branch."""

        name: str = "x"
        ("-n", "--name")

else:  # pragma: no cover - never taken; source only

    class Cond(Args):
        """Dead branch."""

        name: str = "y"
        ("-N", "--dead-name")


try:
    import json  # noqa: F401 - always succeeds; the except branch is dead

    class Tried(Args):
        """Live via try."""

        mode: str = "a"
        ("--rich-mode",)

except ImportError:  # pragma: no cover - never taken; source only

    class Tried(Args):
        """Dead via except."""

        mode: str = "b"
        ("--fallback-mode",)
'''


def test_duplicate_qualname_if_else_picks_the_live_branch(tmp_path):
    """`Cond` is declared once under `if` (the branch that actually runs) and
    again under `else` (dead code, later in the file). Indexing both under
    the same qualname used to let the LATER (dead) ClassDef silently
    overwrite the live one; `getclsdef` must pick the one whose `lineno`
    matches `inspect.getsourcelines(cls)` -- the branch Python actually
    executed."""
    mod_path = tmp_path / "dupmod.py"
    mod_path.write_text(_DUP_QUALNAME_SOURCE, encoding="utf-8")

    spec = importlib.util.spec_from_file_location("dupmod", mod_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["dupmod"] = module
    try:
        spec.loader.exec_module(module)

        node = _introspect.getclsdef(module.Cond)
        assert node is not None and node.name == "Cond"
        assert ast.get_docstring(node) == "Live branch."

        clsargs = _introspect.get_clsargs(module.Cond)
        assert clsargs["name"].exprs == [("-n", "--name")]

        parser = module.Cond._parser_()
        option_strings = {s for a in parser._actions for s in a.option_strings}
        assert {"-n", "--name"} <= option_strings
        assert "-N" not in option_strings
        assert "--dead-name" not in option_strings
    finally:
        sys.modules.pop("dupmod", None)


def test_duplicate_qualname_try_except_picks_the_live_branch(tmp_path):
    """Same as above for a `try`/`except ImportError` fallback shape -- the
    `except` branch never runs (the import always succeeds), but its
    ClassDef comes LAST in the file and used to win."""
    mod_path = tmp_path / "dupmod2.py"
    mod_path.write_text(_DUP_QUALNAME_SOURCE, encoding="utf-8")

    spec = importlib.util.spec_from_file_location("dupmod2", mod_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["dupmod2"] = module
    try:
        spec.loader.exec_module(module)

        node = _introspect.getclsdef(module.Tried)
        assert node is not None and node.name == "Tried"
        assert ast.get_docstring(node) == "Live via try."

        clsargs = _introspect.get_clsargs(module.Tried)
        assert clsargs["mode"].exprs == [("--rich-mode",)]

        parser = module.Tried._parser_()
        option_strings = {s for a in parser._actions for s in a.option_strings}
        assert "--rich-mode" in option_strings
        assert "--fallback-mode" not in option_strings
    finally:
        sys.modules.pop("dupmod2", None)


# On 3.13+ `cls.__firstlineno__` settles a duplicate qualname exactly and the
# two tests above already exercise that. Before 3.13, `getclsdef` instead
# matches candidates by field-name/docstring shape -- but when BOTH branches
# declare the exact SAME shape (no annotation or docstring difference to go
# on), it cannot discriminate either and falls back to "last in source-walk
# order wins". This must resolve consistently on every supported version,
# including the 3.9 floor where the fallback path is actually exercised.
_IDENTICAL_SHAPE_DUP_SOURCE = '''\
"""Two classes, each declared twice under the same qualname, with IDENTICAL
annotated fields and no class docstring on either branch -- nothing for
shape-matching to discriminate on."""
import sys

import duho
from duho import Args

if sys.version_info >= (99,):

    class D(Args):
        a: str = "x"
        "wrong help"
        ("--wrong",)

else:

    class D(Args):
        a: str = "x"
        "right help"
        ("--right",)

try:
    import no_such_module_xyz_zzz  # noqa: F401 - always fails

    class F(Args):
        a: str = "x"
        ("--wrong",)

except ImportError:

    class F(Args):
        a: str = "x"
        ("--right",)
'''


def test_duplicate_qualname_identical_shape_falls_back_consistently(tmp_path):
    mod_path = tmp_path / "dupmod3.py"
    mod_path.write_text(_IDENTICAL_SHAPE_DUP_SOURCE, encoding="utf-8")

    spec = importlib.util.spec_from_file_location("dupmod3", mod_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["dupmod3"] = module
    try:
        spec.loader.exec_module(module)

        for cls, expected_flag in ((module.D, "--right"), (module.F, "--right")):
            parser = cls._parser_()
            option_strings = {s for a in parser._actions for s in a.option_strings}
            assert expected_flag in option_strings
    finally:
        sys.modules.pop("dupmod3", None)


# --- A private field's unresolvable annotation must never crash a ----------
# --- public field's own resolution -------------------------------------------


def _make_class_with_unresolvable_private_annotation():
    class _LocalOnlyType:
        """Visible only inside this function -- not a module global."""

    class _WithBadPrivate(duho.Args):
        """A public field alongside a private one whose annotation can only
        be resolved with access to this function's own locals."""

        public: str = "ok"
        ("--public",)

        _cache_: "_LocalOnlyType" = None

    return _WithBadPrivate


def test_private_field_unresolvable_annotation_does_not_crash_public_fields():
    cls = _make_class_with_unresolvable_private_annotation()
    clsargs = _introspect.get_clsargs(cls)
    assert "public" in clsargs
    assert clsargs["public"].exprs == [("--public",)]
    # The private field never becomes CLI-visible metadata regardless.
    assert "_cache_" not in clsargs

    parser = cls._parser_()
    ns = parser.parse_args(["--public", "x"])
    assert ns.public == "x"


# --- A subclass overriding only flags (or only help) still inherits --------
# --- the base's help (or flags) -- never flattened, positional element 0 ----


class _MroBase(duho.Args):
    """Base declaring both help and flags."""

    level: int = 0
    "How loud to be"
    ("-l", "--level")


class _MroFlagsOnly(_MroBase):
    """Overrides only the flags."""

    level: int = 1
    ("-L",)


class _MroHelpOnly(_MroBase):
    """Overrides only the help text."""

    level: int = 2
    "New help"


class _MroBare(_MroBase):
    """Overrides neither -- inherits both."""

    level: int = 3


def test_mro_docstring_and_flags_resolve_independently():
    base = _introspect.get_clsargs(_MroBase)["level"]
    assert base.docstring == "How loud to be"
    assert base.exprs == [("-l", "--level")]

    flags_only = _introspect.get_clsargs(_MroFlagsOnly)["level"]
    assert flags_only.docstring == "How loud to be"  # inherited from the base
    assert flags_only.exprs == [("-L",)]  # overridden

    help_only = _introspect.get_clsargs(_MroHelpOnly)["level"]
    assert help_only.docstring == "New help"  # overridden
    assert help_only.exprs == [("-l", "--level")]  # inherited from the base

    bare = _introspect.get_clsargs(_MroBare)["level"]
    assert bare.docstring == "How loud to be"
    assert bare.exprs == [("-l", "--level")]


class _Misattr(Args):
    a: int = 1
    int  # bare-name Expr (non-literal); must reset docstring attribution
    "must-not-attach-to-a"
    ("--a",)

    b: str = "x"
    "b doc"
    ("--b",)


def test_docstring_not_misattributed_after_a_non_literal_expression():
    """A bare-name expression statement between a field and a trailing string
    literal must reset docstring attribution, not let the string attach to
    the PRECEDING field."""
    builders = {b.name: b for b in _Misattr._getargs_()}
    assert builders["a"].help != "must-not-attach-to-a"
    assert builders["b"].help == "b doc"
