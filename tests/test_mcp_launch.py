"""Tests for launching MCP from the CLI itself.

Two built-in triggers:

* the ``<PREFIX>MCP``/``<NAME>_MCP`` environment variable, checked first
  thing by both ``duho.main`` and ``duho.app`` (default on, opt-out via
  ``_mcp_ = False`` / ``app(mcp=False)``);
* an OPT-IN subcommand (``_mcp_command_`` on a ``Cli``, or ``app(mcp_command=
  ...)``) that registers ``duho.mcp.McpCmd`` under a chosen name.

Both ultimately call ``duho.mcp.serve_running_app``, which reads the
currently-dispatching app's context from a ``ContextVar`` set by
``duho.main``/``duho.app`` around their own dispatch step.

Real-subprocess tests (PYTHONPATH set via ``conftest.subprocess_env``) verify
the actual end-to-end wiring; name-derivation and validation rules are
exercised in-process where a subprocess adds nothing but noise.
"""

import json
import os
import subprocess
import sys

import pytest

from conftest import subprocess_env
from duho import Cli, Cmd, LoggingArgs
from duho.args import _default_mcp_app_name, _mcp_env_var_name
from duho.env import Env
from duho.runtime import _resolve_mcp_command_name, app


def _lines(text):
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _write(dir_path, name, source):
    path = dir_path / name
    path.write_text(source)
    return path


# --------------------------------------------------------------------------
# Name derivation (g) -- in-process, no subprocess needed
# --------------------------------------------------------------------------


def test_env_var_name_from_env_prefix():
    assert _mcp_env_var_name(None, env=Env("dotagents")) == "DOTAGENTS_MCP"


def test_env_var_name_from_parsername():
    class Named(Cli):
        """Has an explicit name."""

        _parsername_ = "myapp"

    assert _mcp_env_var_name(Named) == "MYAPP_MCP"


def test_env_var_name_from_name_kwarg():
    assert _mcp_env_var_name(None, name="myapp") == "MYAPP_MCP"


def test_env_var_name_from_class_name_fallback(monkeypatch):
    class SomeApp(Cli):
        """No _parsername_, no name kwarg, and no usable argv[0]."""

    # An empty/absent argv[0] (never usable as a program name) falls all the
    # way through to the class name.
    monkeypatch.setattr(sys, "argv", [""])
    assert _mcp_env_var_name(SomeApp) == "SOMEAPP_MCP"
    monkeypatch.setattr(sys, "argv", [])
    assert _default_mcp_app_name(SomeApp) == "SomeApp"


def test_env_var_name_normalizes_hyphen_to_underscore():
    assert _mcp_env_var_name(None, name="my-app") == "MY_APP_MCP"


def test_env_var_name_from_argv0_stem(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["/usr/local/bin/myapp.py"])
    assert _mcp_env_var_name(None) == "MYAPP_MCP"


def test_env_var_name_from_argv0_under_python_dash_m(monkeypatch, tmp_path):
    pkg_dir = tmp_path / "mypkg"
    pkg_dir.mkdir()
    monkeypatch.setattr(sys, "argv", [str(pkg_dir / "__main__.py")])
    assert _mcp_env_var_name(None) == "MYPKG_MCP"


# --------------------------------------------------------------------------
# mcp_command resolution/validation (e) -- in-process
# --------------------------------------------------------------------------


def test_resolve_mcp_command_name_false_by_default():
    assert _resolve_mcp_command_name(None, None) is None


def test_resolve_mcp_command_name_true_is_mcp():
    assert _resolve_mcp_command_name(None, True) == "mcp"


def test_resolve_mcp_command_name_explicit_string():
    assert _resolve_mcp_command_name(None, "serve-mcp") == "serve-mcp"


def test_resolve_mcp_command_name_class_attribute():
    class WithAttr(Cli):
        """Opts in via the class attribute."""

        _mcp_command_ = "mcp"

    assert _resolve_mcp_command_name(WithAttr, None) == "mcp"


def test_resolve_mcp_command_name_kwarg_overrides_class_attribute():
    class WithAttr(Cli):
        """Class attr says True; explicit kwarg turns it back off."""

        _mcp_command_ = True

    assert _resolve_mcp_command_name(WithAttr, False) is None


@pytest.mark.parametrize("bad", ["", "has space", "-leading-dash"])
def test_resolve_mcp_command_name_rejects_invalid_names(bad):
    with pytest.raises(ValueError):
        _resolve_mcp_command_name(None, bad)


def test_app_mcp_command_without_other_subcommands_raises():
    class Solo(Cli):
        """No subcommands at all."""

    with pytest.raises(ValueError, match="at least one other subcommand"):
        app(Solo, mcp_command=True, argv=[], setup_logging=False)


def test_app_mcp_command_colliding_name_raises():
    class Other(Cmd):
        """An existing command named mcp."""

        def __call__(self):
            return 0

    Other._parsername_ = "mcp"

    class Root(Cli):
        """Root with a real command already named mcp."""

        _subcommands_ = [Other]

    with pytest.raises(ValueError, match="collides"):
        app(Root, mcp_command=True, argv=[], setup_logging=False)


# --------------------------------------------------------------------------
# Defect regression tests -- in-process.
# --------------------------------------------------------------------------


def test_mcp_command_help_row_is_not_blank(capsys):
    # The dynamically-built `_McpCmd` subclass has no source of its own
    # for AST docstring introspection, so its --help row used to come up
    # blank.
    class Show(Cmd):
        """Show something."""

        def __call__(self):  # pragma: no cover
            return 0

    class Root(Cli):
        """Root app."""

        _subcommands_ = [Show]

    with pytest.raises(SystemExit):
        app(Root, mcp_command=True, argv=["--help"], setup_logging=False)
    out = capsys.readouterr().out
    assert "Serve this CLI as an MCP server" in out


def test_app_name_kwarg_is_the_root_tool_name_segment():
    # Tool names must reflect app(name=...) rather than the root class's
    # own derived name -- dotagents calls app(Dotagents, name="dotagents"),
    # and its tools must come out "dotagents.*", not "Dotagents.*".
    from duho.mcp import describe_tools

    class Env(Cmd):
        """Report env stuff."""

        def __call__(self):  # pragma: no cover
            return 0

    class Dotagents(Cli):
        """A root whose class name deliberately differs from app(name=)."""

        _subcommands_ = [Env]

    core = _core_for_app_helper(Dotagents, name="dotagents")
    names = {t["name"] for t in describe_tools(core)}
    assert names == {"dotagents.Env"}


def _core_for_app_helper(root, **kwargs):
    from duho.mcp import _core_for_app

    return _core_for_app(root, **kwargs)


def test_serverinfo_reports_the_apps_own_name_and_version():
    # `initialize` must report the served app's own identity, not a fixed
    # {"name": "duho.mcp", "version": <duho version>} for every app.
    import io
    import json as _json

    from duho.mcp import serve

    class Env(Cmd):
        """Report env stuff."""

        def __call__(self):  # pragma: no cover
            return 0

    class Dotagents(Cli):
        """A root reporting its own name and version."""

        _version_ = "9.9.9"
        _subcommands_ = [Env]

    core = _core_for_app_helper(Dotagents, name="dotagents")
    stdin = io.StringIO(
        _json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        + "\n"
    )
    stdout = io.StringIO()
    serve(core, stdin=stdin, stdout=stdout)
    response = _json.loads(stdout.getvalue().splitlines()[0])
    assert response["result"]["serverInfo"] == {"name": "dotagents", "version": "9.9.9"}


def test_serverinfo_falls_back_to_duhos_own_version_when_app_declares_none():
    import io
    import json as _json

    import duho
    from duho.mcp import serve

    class Env(Cmd):
        """Report env stuff."""

        def __call__(self):  # pragma: no cover
            return 0

    class Plain(Cli):
        """A root with no _version_ of its own."""

        _subcommands_ = [Env]

    core = _core_for_app_helper(Plain)
    stdin = io.StringIO(
        _json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        + "\n"
    )
    stdout = io.StringIO()
    serve(core, stdin=stdin, stdout=stdout)
    response = _json.loads(stdout.getvalue().splitlines()[0])
    assert response["result"]["serverInfo"]["name"] == "Plain"
    assert response["result"]["serverInfo"]["version"] == duho.__version__


# --------------------------------------------------------------------------
# `duho.main`'s own `_mcp_command_` support -- in-process.
# Shares `_resolve_mcp_command_name`/`_build_mcp_command_class` with app(),
# so only the main()-specific wiring (no separate kwarg, a fresh root
# subclass carrying the extra subcommand) needs its own coverage here.
# --------------------------------------------------------------------------


def test_main_mcp_command_true_registers_mcp_subcommand():
    import duho

    class Show(Cmd):
        """Show something."""

        def __call__(self):
            return 0

    class Root(Cli):
        """True -> the default 'mcp' name."""

        _subcommands_ = [Show]
        _mcp_command_ = True

    assert duho.main(Root, ["Show"], setup_logging=False) == 0
    with pytest.raises(SystemExit):
        duho.main(Root, ["mcp", "--help"], setup_logging=False)


def test_main_mcp_command_explicit_string_name():
    import duho

    class Show(Cmd):
        """Show something."""

        def __call__(self):
            return 0

    class Root(Cli):
        """A custom subcommand name."""

        _subcommands_ = [Show]
        _mcp_command_ = "serve-mcp"

    with pytest.raises(SystemExit):
        duho.main(Root, ["serve-mcp", "--help"], setup_logging=False)


def test_main_mcp_command_false_is_unchanged(capsys):
    import duho

    class Show(Cmd):
        """Show something."""

        def __call__(self):
            print("shown")
            return 0

    class Root(Cli):
        """The default: no extra subcommand at all."""

        _subcommands_ = [Show]

    assert duho.main(Root, ["Show"], setup_logging=False) == 0
    assert "shown" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        duho.main(Root, ["mcp"], setup_logging=False)


@pytest.mark.parametrize("bad", ["", "has space", "-leading-dash"])
def test_main_mcp_command_rejects_invalid_names(bad):
    import duho

    class Show(Cmd):
        """Show something."""

        def __call__(self):
            return 0

    class Root(Cli):
        """An invalid _mcp_command_ name."""

        _subcommands_ = [Show]
        _mcp_command_ = bad

    with pytest.raises(ValueError):
        duho.main(Root, ["Show"], setup_logging=False)


def test_main_mcp_command_without_other_subcommands_raises():
    import duho

    class Solo(Cli):
        """No subcommands at all."""

        _mcp_command_ = True

        def __call__(self):  # pragma: no cover - never reached
            return 0

    with pytest.raises(ValueError, match="at least one other subcommand"):
        duho.main(Solo, [], setup_logging=False)


def test_main_mcp_command_colliding_name_raises():
    import duho

    class Other(Cmd):
        """An existing command named mcp."""

        _parsername_ = "mcp"

        def __call__(self):
            return 0

    class Root(Cli):
        """Root with a real command already named mcp."""

        _subcommands_ = [Other]
        _mcp_command_ = True

    with pytest.raises(ValueError, match="collides"):
        duho.main(Root, ["mcp"], setup_logging=False)


def test_main_mcp_command_false_does_not_mutate_original_class():
    """The dynamic root subclass main() builds when _mcp_command_ is set
    must never leak back onto the original class -- a second, unrelated
    main() call against the SAME cls must not see a stale extra
    subcommand or leftover state."""
    import duho

    class Show(Cmd):
        """Show something."""

        def __call__(self):
            return 0

    class Root(Cli):
        """Registers mcp, then is dispatched again normally."""

        _subcommands_ = [Show]
        _mcp_command_ = True

    with pytest.raises(SystemExit):
        duho.main(Root, ["mcp", "--help"], setup_logging=False)
    # `_subcommands_` on the class itself is untouched by the call above.
    assert Root._subcommands_ == [Show]


def test_root_mcp_false_does_not_disable_the_mcp_command_subcommand():
    """`_mcp_ = False` on the ROOT keeps its separate, trigger-only meaning
    -- it must not be confused with the per-command exclusion, and must
    not stop `_mcp_command_`'s own subcommand from registering and
    serving."""
    import duho

    class Show(Cmd):
        """Show something."""

        def __call__(self):
            return 0

    class Root(Cli):
        """Env trigger disabled; the mcp subcommand is a separate opt-in."""

        _mcp_ = False
        _subcommands_ = [Show]
        _mcp_command_ = True

    with pytest.raises(SystemExit):
        duho.main(Root, ["mcp", "--help"], setup_logging=False)
    from duho.mcp import describe_tools

    names = {t["name"] for t in describe_tools(Root)}
    assert names == {"Root.Show"}


# --------------------------------------------------------------------------
# Opt-out (d) -- in-process: the variable is left untouched, no serving
# --------------------------------------------------------------------------


def test_class_attr_mcp_false_leaves_env_var_untouched_and_runs_normally(
    monkeypatch, capsys
):
    class OptOutApp(Cli):
        """Env trigger disabled via _mcp_."""

        _parsername_ = "optoutapp"
        _mcp_ = False
        _subcommands_ = []

        def __call__(self):
            print("ran normally")
            return 0

    monkeypatch.setenv("OPTOUTAPP_MCP", "stdio")
    import duho

    rc = duho.main(OptOutApp, [], setup_logging=False)
    assert rc == 0
    assert "ran normally" in capsys.readouterr().out
    assert os.environ.get("OPTOUTAPP_MCP") == "stdio"


def test_app_mcp_false_kwarg_leaves_env_var_untouched(monkeypatch):
    class Dummy(Cmd):
        """A dummy command so app() has something to dispatch."""

        def __call__(self):
            return 0

    class Root(Cli):
        """Bare root for the app(mcp=False) opt-out test."""

        _parsername_ = "optoutapp2"

    monkeypatch.setenv("OPTOUTAPP2_MCP", "stdio")
    rc = app(Root, mcp=False, commands=[Dummy], argv=["Dummy"], setup_logging=False)
    assert rc == 0
    assert os.environ.get("OPTOUTAPP2_MCP") == "stdio"


# --------------------------------------------------------------------------
# Unsupported transport (f) -- in-process
# --------------------------------------------------------------------------


def test_unsupported_transport_exits_2_with_message(monkeypatch, capsys):
    class TransportApp(Cli):
        """For the unsupported-transport test."""

        _parsername_ = "transportapp"
        _subcommands_ = []

        def __call__(self):  # pragma: no cover - never reached
            return 0

    monkeypatch.setenv("TRANSPORTAPP_MCP", "http")
    import duho

    rc = duho.main(TransportApp, [], setup_logging=False)
    assert rc == 2
    err = capsys.readouterr().err
    assert "unsupported MCP transport 'http'" in err
    assert "stdio" in err
    assert "TRANSPORTAPP_MCP" not in os.environ


# --------------------------------------------------------------------------
# serve_running_app() outside a dispatch (e) -- in-process
# --------------------------------------------------------------------------


def test_serve_running_app_outside_dispatch_raises_runtime_error():
    from duho.mcp import serve_running_app

    with pytest.raises(RuntimeError):
        serve_running_app()


def test_serve_running_app_unsupported_transport_raises_value_error():
    from duho.mcp import serve_running_app

    with pytest.raises(ValueError):
        serve_running_app("http")


def test_mcp_command_not_listed_as_a_tool():
    from duho.mcp import McpCmd, _core_for_app, describe_tools

    class Greet(Cmd):
        """Print a greeting."""

        def __call__(self):
            return 0

    class Root(LoggingArgs, Cmd):
        """Root for the not-listed-as-a-tool test."""

        def __call__(self):  # pragma: no cover
            return 0

    # `commands=` takes absolute precedence over `source=` in app()'s own
    # resolution order, so both commands this test needs go through it --
    # this test only exercises `_is_mcp_command_node` filtering, not
    # discovery (already covered by tests/test_mcp_app_tree.py). Same
    # dynamic-subclass shape `app()`'s own `mcp_command` wiring builds
    # (`runtime.py`), constructed directly here.
    mcp_cls = type("_McpCmd", (McpCmd,), {"_parsername_": "mcp"})
    core = _core_for_app(Root, commands=[Greet, mcp_cls], argv=[])
    names = {t["name"] for t in describe_tools(core)}
    assert "Root.Greet" in names
    assert not any("mcp" in n.lower() for n in names)


# --------------------------------------------------------------------------
# Zero-eager-import (h) -- real subprocess, mirrors test_mcp_server.py
# --------------------------------------------------------------------------


def test_normal_main_run_does_not_import_duho_mcp():
    code = (
        "import sys\n"
        "from duho import Cli, Cmd, main\n"
        "class Ping(Cmd):\n"
        '    """Reply pong."""\n'
        "    def __call__(self):\n"
        "        return 0\n"
        "class App(Cli):\n"
        '    """App."""\n'
        "    _subcommands_ = [Ping]\n"
        "main(App, ['Ping'], setup_logging=False)\n"
        "print('duho.mcp' in sys.modules)\n"
    )
    out = subprocess.check_output(
        [sys.executable, "-c", code], text=True, env=subprocess_env()
    )
    assert out.strip() == "False"


def test_main_mcp_command_explicit_false_does_not_import_duho_mcp():
    """An explicit `_mcp_command_ = False` (not just the unset default
    `test_normal_main_run_does_not_import_duho_mcp` above covers) must
    still take the cheap `is not False` branch and never import
    `duho.mcp` (`duho.runtime` is always already loaded by `import duho`
    itself, via `duho/__init__.py`'s own `from .runtime import app,
    run_command` -- not a signal of anything main() itself did)."""
    code = (
        "import sys\n"
        "from duho import Cli, Cmd, main\n"
        "class Ping(Cmd):\n"
        '    """Reply pong."""\n'
        "    def __call__(self):\n"
        "        return 0\n"
        "class App(Cli):\n"
        '    """App."""\n'
        "    _subcommands_ = [Ping]\n"
        "    _mcp_command_ = False\n"
        "main(App, ['Ping'], setup_logging=False)\n"
        "print('duho.mcp' in sys.modules)\n"
    )
    out = subprocess.check_output(
        [sys.executable, "-c", code], text=True, env=subprocess_env()
    )
    assert out.strip() == "False"


def test_normal_app_run_does_not_import_duho_mcp(tmp_path):
    _write(
        tmp_path,
        "greet.py",
        '"""Greet."""\n\n\ndef main(args=None):\n    return 0\n',
    )
    code = (
        "import sys\n"
        "from duho import Cli\n"
        "from duho.runtime import app\n"
        "class Root(Cli):\n"
        '    """Root."""\n'
        f"app(Root, source={str(tmp_path)!r}, argv=['greet'], setup_logging=False)\n"
        "print('duho.mcp' in sys.modules)\n"
    )
    out = subprocess.check_output(
        [sys.executable, "-c", code], text=True, env=subprocess_env()
    )
    assert out.strip() == "False"


# --------------------------------------------------------------------------
# (a) Env trigger, a Cli app with a static _subcommands_ tree -- real
# subprocess, both interpreters (whichever runs this file).
# --------------------------------------------------------------------------

_ENV_TRIGGER_CLASS_APP = '''\
import os
import sys
from duho import Cli, Cmd, main


class Ping(Cmd):
    """Reply pong."""

    def __call__(self):
        present = "ENV_TRIGGER_CLASS_MCP" in os.environ
        print("pong", "present" if present else "absent")
        return 0


class App(Cli):
    """Env-trigger test app."""

    _subcommands_ = [Ping]


if __name__ == "__main__":
    sys.exit(main(App))
'''


def test_env_trigger_serves_a_class_tree(tmp_path):
    app_file = _write(tmp_path, "env_trigger_class.py", _ENV_TRIGGER_CLASS_APP)
    env = subprocess_env()
    env["ENV_TRIGGER_CLASS_MCP"] = "stdio"
    requests = "\n".join(
        [
            json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
            ),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "App.Ping", "arguments": {}},
                }
            ),
        ]
    )
    proc = subprocess.run(
        [sys.executable, str(app_file)],
        input=requests + "\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    responses = _lines(proc.stdout)
    # serverInfo reports the served APP's own identity, not a fixed
    # "duho.mcp" -- App declares no _parsername_/_version_, so its name
    # resolves to its class name and its version falls back to duho's own
    # (never asserted here; only the name is app-specific).
    assert responses[0]["result"]["serverInfo"]["name"] == "App"
    names = {t["name"] for t in responses[1]["result"]["tools"]}
    assert names == {"App.Ping"}
    call_text = responses[2]["result"]["content"][0]["text"]
    assert "pong" in call_text
    # (c) a tool that spawns/inspects a child sees the variable unset -- here
    # Ping checks its OWN process's os.environ, which the trigger already
    # popped from before dispatch.
    assert "present" not in call_text
    assert "absent" in call_text


# --------------------------------------------------------------------------
# (b) Env trigger + mcp_command, an app() CLI with a discovered module
# command -- real subprocess.
# --------------------------------------------------------------------------

_APP_TREE_GREET_MODULE = '''\
"""Print a greeting (module command)."""


def main(args=None):
    print("hello from module command")
    return 0
'''

_ENV_TRIGGER_APP_RUNNER = '''\
import pathlib
import sys
from duho import Cli
from duho.runtime import app


class Root(Cli):
    """App-tree env-trigger test root."""


if __name__ == "__main__":
    sys.exit(app(Root, source=pathlib.Path(__file__).parent / "cmds", argv=sys.argv[1:]))
'''


def test_env_trigger_serves_an_app_tree_with_module_commands(tmp_path):
    # Discovered commands live in a SUBDIRECTORY, never alongside the
    # runner script itself -- `source=` scans every ``.py`` file in that
    # directory, and the runner also defines its own `Root(Cli)` class;
    # co-locating them would make discovery pick that class up too (as a
    # second, unrelated "Root" command).
    cmds_dir = tmp_path / "cmds"
    cmds_dir.mkdir()
    _write(cmds_dir, "greet.py", _APP_TREE_GREET_MODULE)
    runner = _write(tmp_path, "runner.py", _ENV_TRIGGER_APP_RUNNER)
    env = subprocess_env()
    env["RUNNER_MCP"] = "stdio"
    requests = "\n".join(
        [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "Root.greet", "arguments": {}},
                }
            ),
        ]
    )
    proc = subprocess.run(
        [sys.executable, str(runner)],
        input=requests + "\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    responses = _lines(proc.stdout)
    names = {t["name"] for t in responses[0]["result"]["tools"]}
    assert names == {"Root.greet"}
    assert "hello from module command" in responses[1]["result"]["content"][0]["text"]


_MCP_COMMAND_ATTR_RUNNER = '''\
import pathlib
import sys
from duho import Cli
from duho.runtime import app


class Root(Cli):
    """mcp_command via the class attribute."""

    _mcp_command_ = True


if __name__ == "__main__":
    sys.exit(
        app(Root, source=pathlib.Path(__file__).parent / "cmds", argv=sys.argv[1:])
    )
'''

_MCP_COMMAND_KWARG_RUNNER = '''\
import pathlib
import sys
from duho import Cli
from duho.runtime import app


class Root(Cli):
    """mcp_command via the app() kwarg."""


if __name__ == "__main__":
    sys.exit(
        app(
            Root,
            source=pathlib.Path(__file__).parent / "cmds",
            argv=sys.argv[1:],
            mcp_command="serve-mcp",
        )
    )
'''


@pytest.mark.parametrize(
    ("runner_source", "subcommand"),
    [
        (_MCP_COMMAND_ATTR_RUNNER, "mcp"),
        (_MCP_COMMAND_KWARG_RUNNER, "serve-mcp"),
    ],
    ids=["class-attribute-true-is-mcp", "kwarg-named-serve-mcp"],
)
def test_mcp_command_subcommand_serves_over_stdio(tmp_path, runner_source, subcommand):
    cmds_dir = tmp_path / "cmds"
    cmds_dir.mkdir()
    _write(cmds_dir, "greet.py", _APP_TREE_GREET_MODULE)
    runner = _write(tmp_path, "runner.py", runner_source)
    env = subprocess_env()
    requests = "\n".join(
        [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "Root.greet", "arguments": {}},
                }
            ),
        ]
    )
    proc = subprocess.run(
        [sys.executable, str(runner), subcommand],
        input=requests + "\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    responses = _lines(proc.stdout)
    names = {t["name"] for t in responses[0]["result"]["tools"]}
    assert names == {"Root.greet"}
    assert "hello from module command" in responses[1]["result"]["content"][0]["text"]


def test_default_cli_help_is_unchanged_with_no_mcp_opt_in(tmp_path):
    """A default app() CLI's --help output must not mention mcp at all --
    the subcommand is opt-in, never on by default."""
    cmds_dir = tmp_path / "cmds"
    cmds_dir.mkdir()
    _write(cmds_dir, "greet.py", _APP_TREE_GREET_MODULE)
    runner = _write(tmp_path, "runner.py", _ENV_TRIGGER_APP_RUNNER)
    proc = subprocess.run(
        [sys.executable, str(runner), "--help"],
        capture_output=True,
        text=True,
        env=subprocess_env(),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert "mcp" not in proc.stdout.lower()


# --------------------------------------------------------------------------
# `duho.main`'s own `_mcp_command_` subcommand serving over stdio -- real
# subprocess, mirrors the app() e2e tests above.
# --------------------------------------------------------------------------

_MAIN_MCP_COMMAND_RUNNER = '''\
import sys
from duho import Cli, Cmd, main


class Greet(Cmd):
    """Print a greeting."""

    def __call__(self):
        print("hello from main")
        return 0


class Root(Cli):
    """A tiny app served through duho.main."""

    _subcommands_ = [Greet]
    _mcp_command_ = True


if __name__ == "__main__":
    sys.exit(main(Root, sys.argv[1:], setup_logging=False))
'''


def test_main_mcp_command_subcommand_serves_over_stdio(tmp_path):
    runner = _write(tmp_path, "runner.py", _MAIN_MCP_COMMAND_RUNNER)
    requests = "\n".join(
        [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "Root.Greet", "arguments": {}},
                }
            ),
        ]
    )
    proc = subprocess.run(
        [sys.executable, str(runner), "mcp"],
        input=requests + "\n",
        capture_output=True,
        text=True,
        env=subprocess_env(),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    responses = _lines(proc.stdout)
    names = {t["name"] for t in responses[0]["result"]["tools"]}
    # The serving subcommand ("mcp") itself must never appear as a tool.
    assert names == {"Root.Greet"}
    assert "hello from main" in responses[1]["result"]["content"][0]["text"]


def test_default_main_help_is_unchanged_with_no_mcp_opt_in(tmp_path):
    """A default duho.main CLI's --help output must not mention mcp at
    all -- the subcommand is opt-in, never on by default."""
    no_opt_in_runner = _MAIN_MCP_COMMAND_RUNNER.replace(
        "    _mcp_command_ = True\n", ""
    )
    runner = _write(tmp_path, "runner_no_opt_in.py", no_opt_in_runner)
    proc = subprocess.run(
        [sys.executable, str(runner), "--help"],
        capture_output=True,
        text=True,
        env=subprocess_env(),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert "mcp" not in proc.stdout.lower()
