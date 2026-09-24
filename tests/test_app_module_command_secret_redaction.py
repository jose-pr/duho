"""A duho.app module command's own env/config-bound field must never show a
live value in its --help or agent-help description, the same guarantee a
class command already has.

The command source is a REAL ``.py`` file under ``tmp_path`` (never
``python -c``) -- duho's flags-tuple/docstring introspection is AST-based and
needs one, same as ``test_runtime.py``.
"""

import json
import sys

import pytest

import duho
from duho.runtime import app

_MODULE_CMD_WITH_SECRET = '''\
"""Deploy something using a secret."""
from duho import Arg, Args, NS


class Args(Args):
    token: Arg[str, NS(env="DUHO_TEST_MODULE_CMD_SECRET")] = ""
    "Auth token"
    ("--token",)


def main(args):
    return 0
'''


class Root(duho.LoggingArgs, duho.Cmd):
    """A root command supplying global options."""

    def __call__(self):  # pragma: no cover - root is not dispatched in these tests
        return 0


@pytest.fixture(autouse=True)
def _clean_discovered_modules():
    """Drop synthesized discovery modules between tests so fixtures re-import."""
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        if name.startswith("duho._discovered."):
            sys.modules.pop(name, None)


def _write(dir_path, name, source):
    path = dir_path / name
    path.write_text(source)
    return path


def test_module_command_help_never_shows_env_secret(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DUHO_TEST_MODULE_CMD_SECRET", "s3cr3t-module-value")
    _write(tmp_path, "deploy.py", _MODULE_CMD_WITH_SECRET)
    with pytest.raises(SystemExit):
        app(Root, source=tmp_path, argv=["deploy", "--help"], setup_logging=False)
    out = capsys.readouterr().out
    assert "s3cr3t-module-value" not in out


def test_module_command_agent_help_never_shows_env_secret(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("AGENT_HELP", "1")
    monkeypatch.setenv("DUHO_TEST_MODULE_CMD_SECRET", "s3cr3t-module-value")
    _write(tmp_path, "deploy.py", _MODULE_CMD_WITH_SECRET)
    with pytest.raises(SystemExit):
        app(Root, source=tmp_path, argv=["--help"], setup_logging=False)
    out = capsys.readouterr().out

    assert "s3cr3t-module-value" not in out
    doc = json.loads(out)
    dep = next(s for s in doc["subcommands"] if s["name"] == "deploy")
    token = next(o for o in dep["options"] if o["dest"] == "token")
    assert token["default"] is None
    assert token["default_source"] == "env DUHO_TEST_MODULE_CMD_SECRET"


def test_module_command_agent_help_no_secret_leaves_default_untouched(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("AGENT_HELP", "1")
    monkeypatch.delenv("DUHO_TEST_MODULE_CMD_SECRET", raising=False)
    _write(tmp_path, "deploy.py", _MODULE_CMD_WITH_SECRET)
    with pytest.raises(SystemExit):
        app(Root, source=tmp_path, argv=["--help"], setup_logging=False)
    doc = json.loads(capsys.readouterr().out)
    dep = next(s for s in doc["subcommands"] if s["name"] == "deploy")
    token = next(o for o in dep["options"] if o["dest"] == "token")
    assert token["default"] == ""
    assert "default_source" not in token
