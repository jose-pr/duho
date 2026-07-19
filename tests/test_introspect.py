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
import typing

import duho
from duho import _introspect


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
    src = open(filename, encoding="utf-8").read()
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
    # Args/Cmd/Cli carry their OWN empty _duho_constants_ in vars(), so the AST
    # scan short-circuits for them (never re-parses args.py on a build).
    assert "_duho_constants_" in vars(duho.Args)
    assert "_duho_constants_" in vars(duho.Cmd)
    assert "_duho_constants_" in vars(duho.Cli)
    assert vars(duho.Args)["_duho_constants_"] == {}


def test_logging_args_preset_still_scanned():
    # LoggingArgs declares real fields in its class body and must NOT be seeded,
    # so its flags/docstrings still come through.
    constants = _introspect.get_clsargs_constants(duho.LoggingArgs)
    assert constants, "LoggingArgs class-body metadata must still be scanned"


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
