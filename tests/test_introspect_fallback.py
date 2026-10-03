"""getclsdef fallback robustness.

Covers the "never raises" contract of ``_introspect.getclsdef``:

* a class built via ``exec`` with a fake ``__module__`` (no real source file)
  returns ``None`` without raising;
* a class whose source lives in a non-ASCII (UTF-8) file parses correctly.
* a module whose ``__file__`` doesn't exist on disk (as in a zipapp, where
  ``__file__`` is a zip-internal path) still resolves via the
  ``inspect.getsource`` fallback instead of giving up at the first ``OSError``
  (GH #1).

Also covers: without source, a class with annotated fields must WARN
(not silently DEBUG-log, which duho's own -v/--loglevel can't raise until
AFTER the parser -- the very thing that lost its shape -- is already built),
and ``LoggingArgs`` itself (duho's own ``-v``/``-q``/``--loglevel``) must keep
working even when its own source can't be found (e.g. a PyInstaller/.pyc-only/
Nuitka build that ships no .py source for duho).
"""

import importlib.util
import logging
import sys
import types
from unittest import mock

from duho import Args, _introspect


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
    """A class defined in a UTF-8 (non-ASCII) source file resolves + scans."""
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


class _NoSourceWithFields:
    """A real class (defined in this real .py file) used only to have its
    ``getclsdef`` lookup monkeypatched to ``None``, simulating a frozen/
    .pyc-only build that ships no source for it."""

    name: str = "x"
    "A name."


def test_missing_source_warns_for_a_class_with_annotated_fields(caplog):
    """The diagnostic must be WARNING, not DEBUG -- it fires while the
    parser is still being built, before an app's own -v/--loglevel could
    possibly raise the level high enough to see a DEBUG record."""
    no_source = mock.patch.object(
        _introspect, "_module_source_readable", return_value=False
    )
    with mock.patch.object(_introspect, "getclsdef", return_value=None):
        with no_source:
            with caplog.at_level(logging.WARNING, logger="duho._introspect"):
                _introspect.get_clsargs(_NoSourceWithFields)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "_NoSourceWithFields" in r.getMessage() and "no source" in r.getMessage()
        for r in warnings
    )


def test_runtime_created_class_in_a_source_module_only_logs_debug(caplog):
    """A class built with `type(...)` in an ordinary module has no class body
    to read, so nothing is lost: no WARNING per class, only a DEBUG note."""
    dyn = type(
        "_RuntimeMadeWithFields",
        (Args,),
        {"__annotations__": {"n": int}, "n": 1, "__module__": __name__},
    )
    with caplog.at_level(logging.DEBUG, logger="duho._introspect"):
        _introspect.get_clsargs(dyn)

    mine = [r for r in caplog.records if "_RuntimeMadeWithFields" in r.getMessage()]
    assert mine and all(r.levelno == logging.DEBUG for r in mine)


def test_runtime_created_class_in_a_module_without_source_still_warns(caplog):
    """A module with no readable source (frozen, `.pyc`-only, REPL) keeps the
    WARNING: there the class-body declarations really are lost."""
    fake = types.ModuleType("duho_test_no_source_mod")
    sys.modules[fake.__name__] = fake
    try:
        dyn = type(
            "_NoSourceModuleClass",
            (Args,),
            {"__annotations__": {"n": int}, "n": 1, "__module__": fake.__name__},
        )
        with caplog.at_level(logging.WARNING, logger="duho._introspect"):
            _introspect.get_clsargs(dyn)
    finally:
        sys.modules.pop(fake.__name__, None)
        _introspect._MODULE_SOURCE_READABLE.pop(fake.__name__, None)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("_NoSourceModuleClass" in r.getMessage() for r in warnings)


class _NoSourceNoFields(_NoSourceWithFields):
    """A plain subclass declaring NO fields of its own -- only inherits
    `_NoSourceWithFields.name` via the MRO. On Python 3.9,
    `getattr(cls, "__annotations__", None)` follows the MRO and returns the
    BASE's annotations here, spuriously warning about a class that has
    nothing of its own to lose."""


def test_missing_source_does_not_warn_for_a_subclass_with_no_own_fields(caplog):
    with mock.patch.object(_introspect, "getclsdef", return_value=None):
        with caplog.at_level(logging.WARNING, logger="duho._introspect"):
            _introspect.get_clsargs(_NoSourceNoFields)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert not any("_NoSourceNoFields" in r.getMessage() for r in warnings)


def test_loggingargs_flags_survive_a_missing_source(tmp_path):
    """LoggingArgs seeds `_duho_constants_` itself (like Args/Cmd/Cli),
    so its own -v/-q/--loglevel flags -- and now --verbose/--quiet too --
    never depended on an AST scan that a frozen build can't perform. A
    subclass built the same way (no source at all) must still get them.
    """
    import duho
    from duho import LoggingArgs, Cmd

    with mock.patch.object(_introspect, "getclsdef", return_value=None):

        class _NoSourceApp(LoggingArgs, Cmd):
            def __call__(self):
                return 0

        parser = _NoSourceApp._parser_()
        option_strings = [s for a in parser._actions for s in a.option_strings]
        assert "-v" in option_strings
        assert "--verbose" in option_strings
        assert "-q" in option_strings
        assert "--quiet" in option_strings
        assert "--loglevel" in option_strings

        ns = parser.parse_args(["-v", "--loglevel", "x:DEBUG"])
        assert ns.verbose == 1
        assert ns.loglevels == {"x": logging.DEBUG}
        rc = duho.main(_NoSourceApp, [], setup_logging=False)
        assert rc == 0


# --- A BOM or a PEP 263 encoding cookie must not lose flags/docstrings -----


_BOM_SOURCE = '''\
"""A module saved as UTF-8 WITH a byte-order mark."""
import duho
from duho import Args


class BomApp(Args):
    """Config with a BOM."""

    name: str = "x"
    "The name"
    ("-n", "--name")
'''


def test_module_index_bom_source_keeps_flags_and_docstring(tmp_path):
    """A UTF-8-with-BOM source file (Notepad, PowerShell 5.1 -Encoding UTF8,
    "UTF-8 with signature") used to raise SyntaxError on U+FEFF from a plain
    `read_text(encoding="utf-8")`, which is not an OSError -- getclsdef gave
    up before ever trying the inspect.getsource fallback, silently dropping
    every flag/docstring."""
    mod_path = tmp_path / "bom_mod.py"
    mod_path.write_bytes(b"\xef\xbb\xbf" + _BOM_SOURCE.encode("utf-8"))

    spec = importlib.util.spec_from_file_location("bom_mod", mod_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bom_mod"] = module
    try:
        spec.loader.exec_module(module)
        node = _introspect.getclsdef(module.BomApp)
        assert node is not None and node.name == "BomApp"

        clsargs = _introspect.get_clsargs(module.BomApp)
        assert clsargs["name"].docstring == "The name"
        assert clsargs["name"].exprs == [("-n", "--name")]

        parser = module.BomApp._parser_()
        option_strings = {s for a in parser._actions for s in a.option_strings}
        assert {"-n", "--name"} <= option_strings
    finally:
        sys.modules.pop("bom_mod", None)


_LATIN1_SOURCE = (
    "# -*- coding: latin-1 -*-\n"
    '"""M\xf3dulo con un coment\xe1rio n\xe3o-ASCII."""\n'
    "import duho\n"
    "from duho import Args\n"
    "\n"
    "\n"
    "class LatinApp(Args):\n"
    '    """Configura\xe7\xe3o em latin-1."""\n'
    "\n"
    "    level: int = 0\n"
    '    "N\xedvel de log"\n'
    '    ("--level", "-l")\n'
)


def test_module_index_latin1_cookie_source_keeps_flags_and_docstring(tmp_path):
    """A PEP 263 `# -*- coding: latin-1 -*-` source with real non-ASCII bytes
    used to raise UnicodeDecodeError from a plain `read_text(encoding="utf-8")`
    -- also not an OSError, also skipping the getsource fallback."""
    mod_path = tmp_path / "latin1_mod.py"
    mod_path.write_bytes(_LATIN1_SOURCE.encode("latin-1"))

    spec = importlib.util.spec_from_file_location("latin1_mod", mod_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["latin1_mod"] = module
    try:
        spec.loader.exec_module(module)
        node = _introspect.getclsdef(module.LatinApp)
        assert node is not None and node.name == "LatinApp"

        clsargs = _introspect.get_clsargs(module.LatinApp)
        assert clsargs["level"].docstring == "N\xedvel de log"
        assert clsargs["level"].exprs == [("--level", "-l")]

        parser = module.LatinApp._parser_()
        option_strings = {s for a in parser._actions for s in a.option_strings}
        assert {"-l", "--level"} <= option_strings
    finally:
        sys.modules.pop("latin1_mod", None)
