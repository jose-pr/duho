"""Tests for duho.runtime: the ``app``/``run_command`` multi-command driver.

Exercises the capstone driver that wires discovered commands into a runnable
app on top of the shipped ``_parser_``/``"#cls"`` machinery:

* class command + module command both dispatch via ``app(...)``;
* module lifecycle order (``init -> main -> success -> finally`` on success;
  ``init -> main -> finally`` + exception propagation on error, with the ``init``
  sentinel threaded as ``ctx``);
* a module ``register(parser, args)`` hook adds a ``--flag`` visible in that
  subcommand's help and parsed into the instance;
* ``_passthrough_`` (argv after ``--``) reaches the dispatched command;
* resilience end-to-end (one unimportable command doesn't stop a good one);
* return codes (success -> 0, a ``main`` returning 2 -> ``app`` returns 2).

Every command source is a REAL ``.py`` file under ``tmp_path`` -- never
``python -c`` (a ``Cmd`` defined in a ``-c`` string gets no AST-derived flags /
docstrings, per the project's AST/-c limitation). The fixtures double as
readable documentation of the supported command shapes.
"""

import sys

import pytest

import duho
from duho.discovery import ModuleCommand
from duho.runtime import app, run_command


# --------------------------------------------------------------------------
# Fixture-file helpers
# --------------------------------------------------------------------------

# A class command: a Cmd subclass with an AST-derived --name flag.
_CLASS_CMD_DEPLOY = '''\
"""Deploy the thing to an environment."""
import duho
from duho import Cmd


class Deploy(Cmd):
    """Deploy the thing to an environment."""

    name: str = "world"
    "Deploy target name"
    ("--name",)

    def __call__(self):
        return "deployed " + self.name
'''

# A module command: top-level main(args) is the entrypoint, with a full set of
# lifecycle hooks that append to a shared trace list so ordering is observable.
# The init() returns a sentinel context object that main/success/finally see.
_MODULE_CMD_LIFECYCLE = '''\
"""Back up the thing."""

TRACE = []


class _Ctx:
    marker = "the-context"


def init(args):
    TRACE.append("init")
    return _Ctx()


def main(args):
    TRACE.append(("main", args is not None))
    return None


def success(ctx, args):
    TRACE.append(("success", ctx.marker))


def finally_(ctx, args):
    TRACE.append(("finally", ctx.marker))
'''

# A module command whose main() raises, to prove finally_ still runs and the
# exception propagates (success is skipped).
_MODULE_CMD_RAISES = '''\
"""A module command that fails."""

TRACE = []


def init(args):
    TRACE.append("init")
    return "ctx"


def main(args):
    TRACE.append("main")
    raise RuntimeError("boom")


def success(ctx, args):
    TRACE.append("success")


def finally_(ctx, args):
    TRACE.append("finally")
'''

# A module command that adds its own argument via a register(parser, args) hook.
_MODULE_CMD_REGISTER = '''\
"""A module command that registers a custom flag."""

SEEN = {}


def register(parser, args):
    parser.add_argument("--flag", default="unset")


def main(args):
    SEEN["flag"] = getattr(args, "flag", None)
    return None
'''

# A class command that returns its passthrough argv.
_CLASS_CMD_PASSTHROUGH = '''\
"""A class command that reports passthrough argv."""
import duho
from duho import Cmd


class Echo(Cmd):
    """Echo passthrough."""

    def __call__(self):
        return list(self._passthrough_)
'''

# A module command that echoes its passthrough argv (globals carry it).
_MODULE_CMD_PASSTHROUGH = '''\
"""A module command that reports passthrough argv."""

SEEN = {}


def main(args):
    SEEN["passthrough"] = list(getattr(args, "_passthrough_", []))
    return None
'''

# A module command whose return value is a nonzero int (exit code passthrough).
_MODULE_CMD_RC2 = '''\
"""A module command returning exit code 2."""


def main(args):
    return 2
'''

# A file that fails to import (missing dependency) -- must be *skipped* by
# resilient discovery, never abort the good commands. ImportError is the
# skippable class.
_BAD_IMPORT_CMD = '''\
"""A command that cannot be imported."""
import this_module_does_not_exist_anywhere  # noqa: F401


def main(args):
    return "never"
'''


def _write(dir_path, name, source):
    path = dir_path / name
    path.write_text(source)
    return path


class Root(duho.LoggingArgs, duho.Cmd):
    """A root command supplying global options (verbosity)."""

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


# --------------------------------------------------------------------------
# Class + module command both dispatch
# --------------------------------------------------------------------------


def test_class_command_dispatches(tmp_path):
    """app(root, source=dir, argv=[name, ...]) runs the Cmd's __call__()."""
    _write(tmp_path, "deploy.py", _CLASS_CMD_DEPLOY)
    rc = app(Root, source=tmp_path, argv=["Deploy", "--name", "x"], setup_logging=False)
    # Deploy.__call__ returns a string; run_command propagates a non-None return.
    assert rc == "deployed x"


def test_module_command_dispatches_with_init_context(tmp_path):
    """app(root, source=dir, argv=[backup]) runs the module main(ctx-threaded)."""
    mod = _write(tmp_path, "backup.py", _MODULE_CMD_LIFECYCLE)
    rc = app(Root, source=tmp_path, argv=["backup"], setup_logging=False)
    assert rc == 0
    # Import the fixture to read its recorded TRACE.
    import importlib.util

    spec = importlib.util.spec_from_file_location("_probe_backup", mod)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    # The discovered module ran (its own TRACE); re-importing here is a fresh
    # module, so assert via the *discovered* module instead:
    discovered = [
        m
        for name, m in sys.modules.items()
        if name.startswith("duho._discovered.") and name.endswith("backup")
    ]
    assert discovered, "backup module should have been discovered/imported"
    trace = discovered[0].TRACE
    assert trace[0] == "init"
    assert trace[1] == ("main", True)  # main received the parsed args instance
    assert trace[2] == ("success", "the-context")
    assert trace[3] == ("finally", "the-context")


# --------------------------------------------------------------------------
# Lifecycle order (success and error)
# --------------------------------------------------------------------------


def test_lifecycle_order_on_success(tmp_path):
    """On success the order is init, main, success, finally."""
    _write(tmp_path, "backup.py", _MODULE_CMD_LIFECYCLE)
    app(Root, source=tmp_path, argv=["backup"], setup_logging=False)
    discovered = [
        m
        for name, m in sys.modules.items()
        if name.startswith("duho._discovered.") and name.endswith("backup")
    ][0]
    order = [step if isinstance(step, str) else step[0] for step in discovered.TRACE]
    assert order == ["init", "main", "success", "finally"]


def test_lifecycle_order_on_error_propagates(tmp_path):
    """On error: init, main, finally (no success) and the exception propagates."""
    _write(tmp_path, "fails.py", _MODULE_CMD_RAISES)
    with pytest.raises(RuntimeError, match="boom"):
        app(Root, source=tmp_path, argv=["fails"], setup_logging=False)
    discovered = [
        m
        for name, m in sys.modules.items()
        if name.startswith("duho._discovered.") and name.endswith("fails")
    ][0]
    assert discovered.TRACE == ["init", "main", "finally"]


# --------------------------------------------------------------------------
# register(parser, args) hook
# --------------------------------------------------------------------------


def test_register_hook_adds_flag_parsed_into_instance(tmp_path):
    """A module register() adds --flag; it parses into the dispatched instance."""
    _write(tmp_path, "reg.py", _MODULE_CMD_REGISTER)
    rc = app(
        Root,
        source=tmp_path,
        argv=["reg", "--flag", "value"],
        setup_logging=False,
    )
    assert rc == 0
    discovered = [
        m
        for name, m in sys.modules.items()
        if name.startswith("duho._discovered.") and name.endswith("reg")
    ][0]
    assert discovered.SEEN["flag"] == "value"


def test_register_hook_flag_shows_in_subcommand_help(tmp_path, capsys):
    """The register()-added --flag appears in that subcommand's --help."""
    _write(tmp_path, "reg.py", _MODULE_CMD_REGISTER)
    with pytest.raises(SystemExit):
        app(Root, source=tmp_path, argv=["reg", "--help"], setup_logging=False)
    out = capsys.readouterr().out
    assert "--flag" in out


# --------------------------------------------------------------------------
# _passthrough_ reaches the dispatched command
# --------------------------------------------------------------------------


def test_passthrough_reaches_module_command(tmp_path):
    """argv after `--` reaches the dispatched module command as _passthrough_."""
    _write(tmp_path, "echo.py", _MODULE_CMD_PASSTHROUGH)
    rc = app(
        Root,
        source=tmp_path,
        argv=["echo", "--", "extra", "args"],
        setup_logging=False,
    )
    assert rc == 0
    discovered = [
        m
        for name, m in sys.modules.items()
        if name.startswith("duho._discovered.") and name.endswith("echo")
    ][0]
    assert discovered.SEEN["passthrough"] == ["extra", "args"]


def test_passthrough_reaches_class_command(tmp_path):
    """argv after `--` reaches a dispatched class command via _passthrough_."""
    _write(tmp_path, "echo.py", _CLASS_CMD_PASSTHROUGH)
    rc = app(
        Root,
        source=tmp_path,
        argv=["Echo", "--", "a", "b"],
        setup_logging=False,
    )
    assert rc == ["a", "b"]


# --------------------------------------------------------------------------
# Resilience end-to-end
# --------------------------------------------------------------------------


def test_resilient_discovery_skips_bad_command(tmp_path):
    """One unimportable command doesn't stop a good one from dispatching."""
    _write(tmp_path, "deploy.py", _CLASS_CMD_DEPLOY)
    _write(tmp_path, "broken.py", _BAD_IMPORT_CMD)
    # The bad file is skipped during discovery (ImportError); Deploy still runs.
    rc = app(Root, source=tmp_path, argv=["Deploy", "--name", "z"], setup_logging=False)
    assert rc == "deployed z"


# --------------------------------------------------------------------------
# Return codes
# --------------------------------------------------------------------------


def test_return_code_success_is_zero(tmp_path):
    """A command returning None maps to exit code 0."""
    _write(tmp_path, "backup.py", _MODULE_CMD_LIFECYCLE)
    assert app(Root, source=tmp_path, argv=["backup"], setup_logging=False) == 0


def test_return_code_int_is_propagated(tmp_path):
    """A module main returning 2 makes app return 2."""
    _write(tmp_path, "rc2.py", _MODULE_CMD_RC2)
    assert app(Root, source=tmp_path, argv=["rc2"], setup_logging=False) == 2


# --------------------------------------------------------------------------
# commands=[...] path (no discovery / no prepass) + parent-arg inheritance
# --------------------------------------------------------------------------


def test_commands_arg_class_command(tmp_path):
    """Passing commands=[ClassCmd] directly dispatches without discovery."""
    _write(tmp_path, "deploy.py", _CLASS_CMD_DEPLOY)
    import importlib.util

    spec = importlib.util.spec_from_file_location("_direct_deploy", tmp_path / "deploy.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_direct_deploy"] = mod
    try:
        spec.loader.exec_module(mod)
        rc = app(
            Root,
            commands=[mod.Deploy],
            argv=["Deploy", "--name", "direct"],
            setup_logging=False,
        )
        assert rc == "deployed direct"
    finally:
        sys.modules.pop("_direct_deploy", None)


def test_parent_args_inherited_by_subcommand(tmp_path):
    """Global root options (-v) are accepted on a subcommand (parents=)."""
    _write(tmp_path, "backup.py", _MODULE_CMD_LIFECYCLE)
    # -v is a Root/LoggingArgs global; it must be accepted after the subcommand
    # name because each subparser inherits the root parser via parents=.
    rc = app(Root, source=tmp_path, argv=["backup", "-v"], setup_logging=False)
    assert rc == 0


# --------------------------------------------------------------------------
# run_command direct (unit) -- module lifecycle + class command
# --------------------------------------------------------------------------


def test_run_command_module_lifecycle_direct(tmp_path):
    """run_command runs a ModuleCommand's full lifecycle with the init context."""
    mod_path = _write(tmp_path, "backup.py", _MODULE_CMD_LIFECYCLE)
    import importlib.util

    spec = importlib.util.spec_from_file_location("_rc_backup", mod_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_rc_backup"] = module
    try:
        spec.loader.exec_module(module)
        command = ModuleCommand(module, name="backup")
        rc = run_command(command, duho.NS())
        assert rc == 0
        order = [s if isinstance(s, str) else s[0] for s in module.TRACE]
        assert order == ["init", "main", "success", "finally"]
    finally:
        sys.modules.pop("_rc_backup", None)


def test_run_command_class_command_direct():
    """run_command on a class command calls the instance via __call__()."""

    class Inline(duho.Cmd):
        def __call__(self):
            return 7

    inst = Inline()
    assert run_command(Inline, inst) == 7
