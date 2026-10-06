"""Guards on what ``discover_commands`` accepts as a source, and which errors it lets through."""

import sys

import pytest

from duho.discovery import discover_commands
from duho.runtime import app

_CANARY = '''\
import pathlib

pathlib.Path(__file__).resolve().parent.parent.joinpath("MARKER").write_text("x")


def main(args=None):
    return "evil"
'''


@pytest.fixture
def canary_cwd(tmp_path, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "evil.py").write_text(_CANARY)
    monkeypatch.chdir(cwd)
    return tmp_path


@pytest.mark.parametrize("drive", ["C:", "c:", "Z:"])
def test_bare_drive_source_is_rejected_and_imports_nothing(canary_cwd, drive):
    with pytest.raises(ValueError, match="bare drive"):
        discover_commands(drive)
    assert not (canary_cwd / "MARKER").exists()


def test_app_bare_drive_source_is_rejected_and_imports_nothing(canary_cwd):
    with pytest.raises(ValueError, match="bare drive"):
        app(source="C:", argv=["evil"], setup_logging=False)
    assert not (canary_cwd / "MARKER").exists()


@pytest.fixture
def package_with(tmp_path, monkeypatch):
    name = "pkg_syntax_guard"
    pkg = tmp_path / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield name, pkg
    for key in [k for k in sys.modules if k == name or k.startswith(name + ".")]:
        del sys.modules[key]


def test_dotted_package_does_not_swallow_syntax_error(package_with):
    name, pkg = package_with
    (pkg / "good.py").write_text("def main(args):\n    return 0\n")
    (pkg / "broken.py").write_text("def main(args)\n    return 0\n")
    with pytest.raises(SyntaxError):
        discover_commands(name)


def test_dotted_package_skips_import_error_and_keeps_the_rest(package_with):
    name, pkg = package_with
    (pkg / "good.py").write_text("def main(args):\n    return 0\n")
    (pkg / "needy.py").write_text("import no_such_module_for_duho_tests\n")
    commands = discover_commands(name)
    assert len(commands) == 1
