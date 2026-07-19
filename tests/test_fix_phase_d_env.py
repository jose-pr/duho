"""Phase D regression tests: Env.list contract (C11) + autoload hardening (M3)."""

import sys

import pytest

from duho.env import Env
from duho.runtime import _resolve_commands


# -- C11: missing CMDS_PATH must not glob-import the CWD ----------------------


def test_env_list_missing_returns_empty():
    assert Env("zzznope").list("CMDS_PATH", ty=str) == []


def test_resolve_commands_without_cmds_path_does_not_import_cwd(tmp_path, monkeypatch):
    # A canary module that raises on import if duho ever glob-imports the CWD.
    (tmp_path / "canary.py").write_text("raise RuntimeError('CWD import happened')\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CANARY_CMDS_PATH", raising=False)

    env = Env("canary")  # no CANARY_CMDS_PATH set
    # Pre-fix: env.list yielded [Path(".")] and this globbed/imported the CWD,
    # raising RuntimeError from canary.py. Post-fix: [] -> no CWD import.
    assert _resolve_commands(None, None, None, env) == []


# -- M3: companion-module autoload hardening ----------------------------------


def test_autoload_filters_and_coerces(tmp_path, monkeypatch):
    module = tmp_path / "foo_env.py"
    module.write_text(
        "DEBUG = True\n"
        "_private = 1\n"
        "helper = object()\n"
        "import os as _os_alias\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "foo_env", raising=False)

    e = Env("foo")
    # str()-coerced (a real bool would crash env.bool otherwise).
    assert e["DEBUG"] == "True"
    assert isinstance(e["DEBUG"], str)
    # env.bool does not crash on a real bool and reads True.
    assert e.bool("DEBUG") is True
    # Private/lower-case/dunder module vars are not exposed.
    assert "_private" not in e._env
    assert "helper" not in e._env
    assert not any(k.startswith("__") for k in e._env)


def test_autoload_false_skips_import(tmp_path, monkeypatch):
    module = tmp_path / "bar_env.py"
    module.write_text("raise RuntimeError('should not be imported')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "bar_env", raising=False)

    # autoload=False must not import the (raising) canary module.
    e = Env("bar", autoload=False)
    assert e._env == {}
