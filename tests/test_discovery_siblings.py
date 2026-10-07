"""Helper modules of a command directory: one module object per directory."""

from __future__ import annotations

import sys
import textwrap
import threading
from pathlib import Path

import pytest

import duho
from duho.discovery import discover_commands

_BASE = '''\
from duho import Cmd

REGISTRY = []


class Base(Cmd):
    """Base."""

    target: str = "t"
    """Where to point."""
    ("-t", "--target")
    retries: int = 1
    """How many times."""
    ("-r",)

    def __call__(self):
        REGISTRY.append(type(self).__name__)
        return [self.target, self.retries, list(REGISTRY)]
'''

_BASE_FUTURE = '''\
from __future__ import annotations

from pathlib import Path

from duho import Cmd

REGISTRY = []


class Base(Cmd):
    """Base."""

    target: Path = Path("t")
    """Where to point."""
    ("-t", "--target")

    def __call__(self):
        REGISTRY.append(type(self).__name__)
        return [str(self.target), list(REGISTRY)]
'''

_SUB = '''\
from _base import Base


class {name}(Base):
    """Run {name}."""
'''

_EXT_USER = '''\
import extlib
from duho import Cmd


class {name}(Cmd):
    """Uses extlib."""

    mod = extlib
'''


def _write(directory: Path, name: str, source: str) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source))
    return path


def _by_name(commands) -> dict:
    return {c.__name__: c for c in commands}


def _two_commands(tmp_path: Path, base: str = _BASE) -> dict:
    _write(tmp_path, "_base.py", base)
    _write(tmp_path, "alpha.py", _SUB.format(name="Alpha"))
    _write(tmp_path, "beta.py", _SUB.format(name="Beta"))
    return _by_name(discover_commands(tmp_path))


def _run(cmd, argv):
    return duho.main(cmd, argv, setup_logging=False)


def test_helper_base_class_keeps_flags_and_help(tmp_path, capsys):
    cmds = _two_commands(tmp_path)
    with pytest.raises(SystemExit):
        _run(cmds["Alpha"], ["-h"])
    out = capsys.readouterr().out
    assert "--target TARGET" in out
    assert "-t" in out
    assert "Where to point." in out
    assert "How many times." in out
    assert _run(cmds["Alpha"], ["-t", "x", "-r", "5"])[:2] == ["x", 5]


def test_helper_base_class_with_deferred_annotations(tmp_path):
    cmds = _two_commands(tmp_path, _BASE_FUTURE)
    assert _run(cmds["Alpha"], ["-t", "x"])[0] == "x"


def test_command_files_share_one_helper_module(tmp_path):
    cmds = _two_commands(tmp_path)
    alpha, beta = cmds["Alpha"], cmds["Beta"]
    assert alpha.__mro__[1] is beta.__mro__[1]
    assert _run(alpha, [])[2] == ["Alpha"]
    assert _run(beta, [])[2] == ["Alpha", "Beta"]
    assert (
        sys.modules[alpha.__mro__[1].__module__]
        is sys.modules[beta.__mro__[1].__module__]
    )


def test_import_inside_a_function_works_at_dispatch(tmp_path):
    _write(tmp_path, "_base.py", "REGISTRY = ['seed']\n")
    _write(
        tmp_path,
        "lazy.py",
        '''\
        from duho import Cmd


        class Lazy(Cmd):
            """Lazy."""

            def __call__(self):
                from _base import REGISTRY

                import _base

                return [REGISTRY, _base.REGISTRY is REGISTRY]
        ''',
    )
    (lazy,) = discover_commands(tmp_path)
    assert _run(lazy, []) == [["seed"], True]


def _extlib_project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    _write(proj / "site", "extlib/__init__.py", "class Err(Exception):\n    pass\n")
    for name in ("First", "Second"):
        _write(proj, name.lower() + ".py", _EXT_USER.format(name=name))
    sys.path.append(str(proj / "site"))
    return proj


def test_package_imported_before_discovery_stays_one_module(tmp_path):
    proj = _extlib_project(tmp_path)
    import extlib

    cmds = _by_name(discover_commands(proj))
    assert cmds["First"].mod is extlib
    assert cmds["Second"].mod is extlib


def test_package_under_scanned_directory_is_one_module_for_all(tmp_path):
    proj = _extlib_project(tmp_path)
    cmds = _by_name(discover_commands(proj))
    import extlib

    assert "extlib" in sys.modules
    assert cmds["First"].mod is extlib
    assert cmds["Second"].mod is extlib


def test_helper_importing_another_helper(tmp_path):
    _write(tmp_path, "_a.py", "from _b import VALUE\nDOUBLE = VALUE * 2\n")
    _write(tmp_path, "_b.py", "VALUE = 21\n")
    _write(
        tmp_path,
        "use.py",
        '''\
        import _a
        from duho import Cmd


        class Use(Cmd):
            """Use."""

            def __call__(self):
                return _a.DOUBLE
        ''',
    )
    (use,) = discover_commands(tmp_path)
    assert _run(use, []) == 42


def test_sibling_package_forms(tmp_path):
    _write(tmp_path, "_pkg/__init__.py", "TOP = 'top'\n")
    _write(tmp_path, "_pkg/sub.py", "VALUE = 7\nfrom . import sibling\n")
    _write(tmp_path, "_pkg/sibling.py", "NAME = 'sibling'\n")
    _write(
        tmp_path,
        "use.py",
        '''\
        import _pkg.sub
        from _pkg import sub as sub2
        from _pkg.sub import VALUE
        from duho import Cmd


        class Use(Cmd):
            """Use."""

            def __call__(self):
                return [
                    _pkg.TOP,
                    _pkg.sub.VALUE,
                    sub2 is _pkg.sub,
                    VALUE,
                    _pkg.sub.sibling.NAME,
                ]
        ''',
    )
    (use,) = discover_commands(tmp_path)
    assert _run(use, []) == ["top", 7, True, 7, "sibling"]


def test_a_file_named_like_a_stdlib_module_does_not_shadow_it(tmp_path):
    _write(tmp_path, "fractions/__init__.py", "raise RuntimeError('shadowed')\n")
    _write(
        tmp_path,
        "use.py",
        '''\
        import fractions
        from duho import Cmd


        class Use(Cmd):
            """Use."""

            frac = fractions
        ''',
    )
    (use,) = discover_commands(tmp_path)
    assert hasattr(use.frac, "Fraction")


def test_missing_sibling_names_the_plain_module(tmp_path):
    _write(
        tmp_path,
        "use.py",
        '''\
        from duho import Cmd


        class Use(Cmd):
            """Use."""

            def __call__(self):
                import _not_there_anywhere
        ''',
    )
    (use,) = discover_commands(tmp_path)
    with pytest.raises(ModuleNotFoundError) as info:
        _run(use, [])
    assert info.value.name == "_not_there_anywhere"
    assert "duho._discovered" not in str(info.value)


def test_two_directories_keep_their_own_helper_state(tmp_path):
    cmds = {}
    for label in ("a", "b"):
        directory = tmp_path / label
        directory.mkdir()
        _write(directory, "_base.py", "REGISTRY = []\nLABEL = %r\n" % label)
        for stem in ("one", "two"):
            _write(
                directory,
                stem + ".py",
                '''\
                import _base
                from duho import Cmd


                class {cls}(Cmd):
                    """Run."""

                    def __call__(self):
                        _base.REGISTRY.append("{stem}")
                        return [_base.LABEL, list(_base.REGISTRY)]
                '''.format(cls=stem.title(), stem=stem),
            )
        cmds[label] = _by_name(discover_commands(directory))
    assert _run(cmds["a"]["One"], []) == ["a", ["one"]]
    assert _run(cmds["b"]["One"], []) == ["b", ["one"]]
    assert _run(cmds["a"]["Two"], []) == ["a", ["one", "two"]]
    assert _run(cmds["b"]["Two"], []) == ["b", ["one", "two"]]


def test_helper_is_private_to_its_directory(tmp_path):
    _write(tmp_path, "_helpers.py", "X = 1\n")
    _write(
        tmp_path,
        "use.py",
        "import _helpers\nfrom duho import Cmd\n\n\nclass Use(Cmd):\n"
        '    """Use."""\n\n    mod = _helpers\n',
    )
    (use,) = discover_commands(tmp_path)
    assert "_helpers" not in sys.modules
    assert str(tmp_path) not in sys.path
    assert use.mod.__name__.endswith("._helpers")
    assert sys.modules[use.mod.__name__] is use.mod
    with pytest.raises(ModuleNotFoundError):
        __import__("_helpers")


def test_helper_body_runs_once_when_threads_discover_together(tmp_path):
    _write(
        tmp_path,
        "_counted.py",
        "import time\nimport builtins\n"
        "builtins._duho_sibling_runs = getattr(builtins, '_duho_sibling_runs', 0) + 1\n"
        "time.sleep(0.05)\n",
    )
    for stem in ("one", "two", "three"):
        _write(
            tmp_path,
            stem + ".py",
            "import _counted\nfrom duho import Cmd\n\n\nclass %s(Cmd):\n"
            '    """x."""\n' % stem.title(),
        )
    import builtins

    builtins._duho_sibling_runs = 0
    errors = []

    def go():
        try:
            discover_commands(tmp_path)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(6)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert builtins._duho_sibling_runs == 1
    finally:
        del builtins._duho_sibling_runs
