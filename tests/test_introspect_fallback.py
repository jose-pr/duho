"""getclsdef fallback robustness (Plan 03 T6).

Covers the "never raises" contract of ``_introspect.getclsdef``:

* a class built via ``exec`` with a fake ``__module__`` (no real source file)
  returns ``None`` without raising;
* a class whose source lives in a non-ASCII (UTF-8) file parses correctly.
* a module whose ``__file__`` doesn't exist on disk (as in a zipapp, where
  ``__file__`` is a zip-internal path) still resolves via the
  ``inspect.getsource`` fallback instead of giving up at the first ``OSError``
  (GH #1).
"""

import importlib.util
import sys

from duho import _introspect


def test_getclsdef_exec_fake_module_returns_none():
    """A class created by exec with a bogus __module__ resolves to None cleanly."""
    ns: dict = {}
    exec("class Made:\n    x = 1\n", ns)
    made = ns["Made"]
    # Point __module__ at a name that has no importable source file.
    made.__module__ = "duho_no_such_module_xyz"
    assert _introspect.getclsdef(made) is None


def test_getclsdef_exec_no_module_returns_none():
    """A class whose __module__ is missing entirely still returns None, no raise."""
    ns: dict = {}
    exec("class Made2:\n    y = 2\n", ns)
    made = ns["Made2"]
    made.__module__ = None  # type: ignore[assignment]
    assert _introspect.getclsdef(made) is None


_UTF8_SOURCE = '''\
"""Módulo con acentos y emoji 🚀 — UTF-8 source."""
import duho
from duho import Args


class Ciudad(Args):
    """Configuración de la ciudad."""

    nombre: str = "Bogotá"
    "Nombre de la ciudad (ñ, á, é)"
    ("--nombre",)
'''


def test_getclsdef_non_ascii_source_parses(tmp_path):
    """A class defined in a UTF-8 (non-ASCII) source file resolves + scans (M11)."""
    mod_path = tmp_path / "ciudad_mod.py"
    mod_path.write_text(_UTF8_SOURCE, encoding="utf-8")

    spec = importlib.util.spec_from_file_location("ciudad_mod", mod_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ciudad_mod"] = module
    try:
        spec.loader.exec_module(module)
        node = _introspect.getclsdef(module.Ciudad)
        assert node is not None
        assert node.name == "Ciudad"
        # The AST-derived flag from the UTF-8 body is picked up.
        args = _introspect.get_clsargs(module.Ciudad)
        assert "nombre" in args
    finally:
        sys.modules.pop("ciudad_mod", None)


_ZIPAPP_SHAPED_SOURCE = '''\
"""A module whose __file__ will be repointed at a nonexistent path."""
import duho
from duho import Args


class Greet(Args):
    """Greet a target."""

    target: str = "world"
    "Who to greet."
    ("target",)
'''


def test_getclsdef_falls_back_when_module_index_hits_oserror(tmp_path):
    """A module.__file__ that doesn't exist on disk still resolves (GH #1).

    Reproduces the zipapp shape: ``module.__file__`` points somewhere
    ``Path(...).read_text()`` can't reach (a zip-internal path in the real
    bug; here, simply repointed after import), so ``_module_index`` raises
    ``OSError``. Before the fix this made ``getclsdef`` return ``None``
    without ever trying the ``inspect.getsource`` fallback -- which DOES
    still work here, because Python caches the source (linecache) from the
    original, real import. That silently dropped the ``("target",)``
    positional declaration and the docstring, degrading `target` to a
    `--target` option.
    """
    mod_path = tmp_path / "greet_mod.py"
    mod_path.write_text(_ZIPAPP_SHAPED_SOURCE, encoding="utf-8")

    spec = importlib.util.spec_from_file_location("greet_mod", mod_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["greet_mod"] = module
    try:
        spec.loader.exec_module(module)
        # Simulate the zipapp condition: __file__ no longer resolves on disk.
        module.__file__ = str(tmp_path / "zipapp.pyz" / "greet_mod.py")

        node = _introspect.getclsdef(module.Greet)
        assert node is not None
        assert node.name == "Greet"

        clsargs = _introspect.get_clsargs(module.Greet)
        assert clsargs["target"].docstring == "Who to greet."
        assert clsargs["target"].exprs == [("target",)]
    finally:
        sys.modules.pop("greet_mod", None)
