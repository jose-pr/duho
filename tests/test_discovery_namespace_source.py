"""A bare-string source naming a directory without ``__init__.py`` is a directory of loose files."""

import importlib
import sys

from duho.args._naming import _command_name
from duho.discovery import discover_commands


def _make_cmds(root, name):
    cmds = root / name
    cmds.mkdir()
    (cmds / "_helpers.py").write_text("def greeting():\n    return 'hi'\n")
    (cmds / "hello.py").write_text(
        "from _helpers import greeting\n" "def main(args):\n    return greeting()\n"
    )
    return cmds


def test_namespace_directory_on_sys_path_discovers_loose_files(tmp_path, monkeypatch):
    name = "nsdir_cmds_a"
    _make_cmds(tmp_path, name)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    try:
        commands = discover_commands(name)
    finally:
        sys.modules.pop(name, None)
    assert [_command_name(c) for c in commands] == ["hello"]


def test_regular_package_still_imports_as_package(tmp_path, monkeypatch):
    name = "nsdir_pkg_b"
    pkg = tmp_path / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "hello.py").write_text("def main(args):\n    return 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    try:
        commands = discover_commands(name)
    finally:
        for key in [k for k in sys.modules if k == name or k.startswith(name + ".")]:
            sys.modules.pop(key, None)
    assert [_command_name(c) for c in commands] == ["hello"]
