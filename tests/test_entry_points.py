"""Tests for entry-points plugin discovery (F6).

``duho.app(root, entry_points="group")`` -- and its underlying
``duho.discover_entry_points(group)`` -- load commands advertised by installed
distributions' entry points, coercing each to a Command through the same paths
as the other sources, and skipping a broken plugin with a warning.

The fixtures build a REAL ``*.dist-info`` directory on ``sys.path`` (suite
convention: real files, not monkeypatched ``entry_points()``) so the stdlib
``importlib.metadata`` machinery discovers them exactly as it would a pip-installed
distribution.
"""

import importlib
import logging
import sys
import textwrap

import pytest

import duho


# A plugin module exposing a class command (a Cmd subclass) and a module-command
# entrypoint, plus a broken entry-point target that does not exist.
_PLUGIN_MODULE = '''\
"""A plugin package contributing commands via entry points."""

import duho
from duho import Cmd

RAN = []


class HelloCmd(Cmd):
    """Say hello (class-command plugin)."""

    _parsername_ = "hello"

    name: str = "world"
    "who to greet"
    ("--name",)

    def __call__(self):
        RAN.append(("hello", self.name))
        return 0


def main(args):
    """Top-level entrypoint plugin (module command)."""
    RAN.append(("bye", None))
    return 0
'''

_GROUP = "duho_test_plugins.commands"


def _install_fake_distribution(tmp_path, module_name, entry_points_txt):
    """Write a plugin module + a ``*.dist-info`` on ``tmp_path`` and return it.

    Registers the module file and a minimal dist-info directory whose
    ``entry_points.txt`` advertises ``entry_points_txt`` under ``tmp_path`` so
    ``importlib.metadata`` (reading ``sys.path``) discovers it.
    """
    (tmp_path / (module_name + ".py")).write_text(_PLUGIN_MODULE)
    dist_info = tmp_path / "duho_test_plugins-1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: duho-test-plugins\nVersion: 1.0\n"
    )
    (dist_info / "entry_points.txt").write_text(
        textwrap.dedent(entry_points_txt)
    )
    return dist_info


@pytest.fixture
def fake_plugins(tmp_path, monkeypatch):
    module_name = "duho_test_plugin_mod"
    _install_fake_distribution(
        tmp_path,
        module_name,
        f"""\
        [{_GROUP}]
        hello = {module_name}:HelloCmd
        bye = {module_name}
        broken = {module_name}:DoesNotExist
        """,
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    yield module_name
    sys.modules.pop(module_name, None)


def test_discover_entry_points_loads_and_skips_broken(fake_plugins, caplog):
    with caplog.at_level(logging.WARNING, logger="duho"):
        commands = duho.discover_entry_points(_GROUP)

    names = sorted(duho.discovery._command_name(c) for c in commands)
    # hello (class command) + bye (module command) load; broken is skipped.
    assert names == ["bye", "hello"]
    assert any("broken" in rec.message for rec in caplog.records)


def test_app_dispatches_class_command_plugin(fake_plugins):
    module = importlib.import_module(fake_plugins)
    module.RAN.clear()

    rc = duho.app(entry_points=_GROUP, argv=["hello", "--name", "there"])

    assert rc == 0
    assert module.RAN == [("hello", "there")]


def test_app_dispatches_module_command_plugin(fake_plugins):
    module = importlib.import_module(fake_plugins)
    module.RAN.clear()

    rc = duho.app(entry_points=_GROUP, argv=["bye"])

    assert rc == 0
    assert module.RAN == [("bye", None)]


def test_missing_group_yields_no_commands():
    assert duho.discover_entry_points("duho_no_such_group_zzz.commands") == []


def test_entry_points_lazy_import():
    """discover_entry_points must not have been triggered by ``import duho``."""
    # A plain import of duho never loads importlib.metadata (P1/F6). This is the
    # per-feature guard mirrored by the plan's acceptance importtime check.
    code = "import sys, duho; print('importlib.metadata' in sys.modules)"
    import subprocess

    out = subprocess.check_output([sys.executable, "-c", code], text=True)
    assert out.strip() == "False"
