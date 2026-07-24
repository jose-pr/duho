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

# A module command whose register hook adds a flag (-q) already owned by the
# root's LoggingArgs. Under app's parent-arg inheritance the subparser already
# carries -q, so this collides -- app must re-raise a clear, command-named error.
_MODULE_CMD_REGISTER_COLLIDES = '''\
"""A module command whose register reuses a global short flag."""


def register(parser, args):
    parser.add_argument("-q", "--query", help="the query")


def main(args):
    return None
'''

# A module command whose register hook takes the 3-arg (parser, args, logger)
# shape: it records that it got a real logger and still adds a --flag argument.
_MODULE_CMD_REGISTER_3ARG = '''\
"""A module command with a 3-arg register(parser, args, logger)."""
import logging

SEEN = {}


def register(parser, args, logger):
    SEEN["logger_is_logger"] = isinstance(logger, logging.Logger)
    SEEN["logger_name"] = getattr(logger, "name", None)
    parser.add_argument("--flag", default="unset")


def main(args):
    SEEN["flag"] = getattr(args, "flag", None)
    return None
'''

# A module command whose register hook is *args-variadic: it must be treated as
# 3-arg-capable and thus receive the logger as the third positional.
_MODULE_CMD_REGISTER_VARARGS = '''\
"""A module command with a *args register hook."""
import logging

SEEN = {}


def register(*args):
    SEEN["argc"] = len(args)
    SEEN["third_is_logger"] = len(args) >= 3 and isinstance(args[2], logging.Logger)
    parser = args[0]
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


def test_register_hook_global_flag_collision_error_names_command(tmp_path):
    """A register() reusing a global flag raises a clear, command-named error.

    Because every subcommand parser inherits the root's globals (parent-arg
    inheritance), a hook that adds an inherited flag (-q here, owned by
    LoggingArgs) collides. app must re-raise argparse's ArgumentError with a
    message naming the command and pointing at the global-flag cause, instead of
    argparse's bare "conflicting option string".
    """
    import argparse

    _write(tmp_path, "query.py", _MODULE_CMD_REGISTER_COLLIDES)
    with pytest.raises(argparse.ArgumentError) as excinfo:
        app(Root, source=tmp_path, argv=["query", "--help"], setup_logging=False)
    msg = str(excinfo.value)
    assert "query" in msg  # names the command
    assert "global" in msg  # explains the cause
    assert "-q" in msg  # preserves argparse's original detail


def test_register_hook_3arg_gets_logger_and_adds_flag(tmp_path):
    """A 3-arg register(parser, args, logger) receives a real logger + adds --flag."""
    _write(tmp_path, "reg3.py", _MODULE_CMD_REGISTER_3ARG)
    rc = app(
        Root,
        source=tmp_path,
        argv=["reg3", "--flag", "three"],
        setup_logging=False,
    )
    assert rc == 0
    discovered = [
        m
        for name, m in sys.modules.items()
        if name.startswith("duho._discovered.") and name.endswith("reg3")
    ][0]
    # The hook got a real logging.Logger as its third positional...
    assert discovered.SEEN["logger_is_logger"] is True
    # ...and (Root is LoggingArgs-based) it is the args instance's own _logger_,
    # whose name is the root parser's name ("Root"), not the fallback "duho".
    assert discovered.SEEN["logger_name"] == "Root"
    # ...and the flag it added parsed into the instance.
    assert discovered.SEEN["flag"] == "three"


def test_register_hook_varargs_treated_as_3arg(tmp_path):
    """A *args register hook is treated as 3-arg-capable and gets the logger."""
    _write(tmp_path, "regv.py", _MODULE_CMD_REGISTER_VARARGS)
    rc = app(
        Root,
        source=tmp_path,
        argv=["regv", "--flag", "var"],
        setup_logging=False,
    )
    assert rc == 0
    discovered = [
        m
        for name, m in sys.modules.items()
        if name.startswith("duho._discovered.") and name.endswith("regv")
    ][0]
    assert discovered.SEEN["argc"] == 3
    assert discovered.SEEN["third_is_logger"] is True
    assert discovered.SEEN["flag"] == "var"


def test_register_hook_2arg_still_works(tmp_path):
    """The historical 2-arg register(parser, args) is called unchanged (no logger)."""
    _write(tmp_path, "reg.py", _MODULE_CMD_REGISTER)
    rc = app(
        Root,
        source=tmp_path,
        argv=["reg", "--flag", "two"],
        setup_logging=False,
    )
    assert rc == 0
    discovered = [
        m
        for name, m in sys.modules.items()
        if name.startswith("duho._discovered.") and name.endswith("reg")
    ][0]
    assert discovered.SEEN["flag"] == "two"


def test_register_hook_wrapper_on_module_with_no_own_register_is_called(tmp_path):
    """Wrapping `command.register` on a module with NO register of its own still fires.

    Regression: the gating/arity check used to re-derive from
    `getattr(module, "register", ...)` instead of the SAME object being
    called (`command.register`). A module defining no `register` hook has
    `module.register` -> None -> not callable, so a caller's wrapper
    assigned directly to `command.register` was silently skipped for
    exactly this shape (a real consumer scenario: wrapping `command.register`
    app-wide to add a shared positional every command needs).
    """
    from duho.discovery import discover_commands

    _write(tmp_path, "norereg.py", _MODULE_CMD_RC2)
    commands = discover_commands(tmp_path)
    command = commands[0]

    calls = []

    def wrapper(parser, args):
        calls.append(True)
        parser.add_argument("--wrapped", default=None)

    command.register = wrapper

    rc = app(Root, commands=[command], argv=["norereg", "--wrapped", "x"], setup_logging=False)
    assert rc == 2
    assert calls == [True]


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


# --------------------------------------------------------------------------
# app(dispatch=...) seam
# --------------------------------------------------------------------------


def test_dispatch_seam_invoked_with_command_and_instance(tmp_path):
    """A custom dispatch is called with (command, instance) and its int propagates."""
    _write(tmp_path, "deploy.py", _CLASS_CMD_DEPLOY)
    seen = {}

    def my_dispatch(command, instance):
        seen["command"] = command
        seen["instance"] = instance
        return 42

    rc = app(
        Root,
        source=tmp_path,
        argv=["Deploy", "--name", "x"],
        setup_logging=False,
        dispatch=my_dispatch,
    )
    assert rc == 42
    # For a class command the dispatched command is the Cmd class and the
    # instance is the parsed command instance (which IS a Cmd).
    assert isinstance(seen["instance"], duho.Cmd)
    assert seen["command"] is type(seen["instance"])
    # The parsed instance really carries the parsed value (proves app did the
    # full resolve/parse before handing off to dispatch).
    assert seen["instance"].name == "x"


def test_dispatch_seam_receives_module_command(tmp_path):
    """For a module command, dispatch receives the ModuleCommand + root instance."""
    _write(tmp_path, "backup.py", _MODULE_CMD_LIFECYCLE)
    seen = {}

    def my_dispatch(command, instance):
        seen["command"] = command
        seen["instance"] = instance
        return 3

    rc = app(
        Root,
        source=tmp_path,
        argv=["backup"],
        setup_logging=False,
        dispatch=my_dispatch,
    )
    assert rc == 3
    assert isinstance(seen["command"], ModuleCommand)
    assert seen["command"]._parsername_ == "backup"
    # A custom dispatch that does NOT call run_command means the module
    # lifecycle never runs -- the seam fully owns the run step.
    discovered = [
        m
        for name, m in sys.modules.items()
        if name.startswith("duho._discovered.") and name.endswith("backup")
    ][0]
    assert discovered.TRACE == []


def test_dispatch_none_is_identical_to_default(tmp_path):
    """dispatch=None behaves exactly as omitting it (default run_command path)."""
    _write(tmp_path, "deploy.py", _CLASS_CMD_DEPLOY)
    rc_default = app(
        Root, source=tmp_path, argv=["Deploy", "--name", "d"], setup_logging=False
    )
    rc_none = app(
        Root,
        source=tmp_path,
        argv=["Deploy", "--name", "d"],
        setup_logging=False,
        dispatch=None,
    )
    assert rc_default == rc_none == "deployed d"


def test_dispatch_can_delegate_to_run_command(tmp_path):
    """A dispatch may call run_command itself (module lifecycle still runs)."""
    _write(tmp_path, "backup.py", _MODULE_CMD_LIFECYCLE)

    def my_dispatch(command, instance):
        return run_command(command, instance)

    rc = app(
        Root,
        source=tmp_path,
        argv=["backup"],
        setup_logging=False,
        dispatch=my_dispatch,
    )
    assert rc == 0
    discovered = [
        m
        for name, m in sys.modules.items()
        if name.startswith("duho._discovered.") and name.endswith("backup")
    ][0]
    order = [s if isinstance(s, str) else s[0] for s in discovered.TRACE]
    assert order == ["init", "main", "success", "finally"]


def test_dispatch_can_fan_out_over_targets(tmp_path):
    """A dispatch that fans a command out over targets works end-to-end."""
    import duho.fanout as fanout

    _write(tmp_path, "deploy.py", _CLASS_CMD_DEPLOY)
    ran = []

    def my_dispatch(command, instance):
        targets = list(duho.expand("t[1-3]"))

        def run_for(target):
            ran.append(target)
            return 0

        return fanout.run_targets(run_for, targets)

    rc = app(
        Root,
        source=tmp_path,
        argv=["Deploy", "--name", "x"],
        setup_logging=False,
        dispatch=my_dispatch,
    )
    assert rc == 0
    assert sorted(ran) == ["t1", "t2", "t3"]


# --------------------------------------------------------------------------
# CMDS_PATH extends _subcommands_ (it does not replace them)
# --------------------------------------------------------------------------

_MODULE_CMD_GREET = '''\
"""A discovered command."""


def main(args=None):
    return "greeted"
'''

_MODULE_CMD_HELLO_OVERRIDE = '''\
"""Shadows the built-in hello."""


def main(args=None):
    return "overridden"
'''


class _Hello(duho.LoggingArgs, duho.Cmd):
    """A built-in subcommand carried on the root class."""

    _parsername_ = "hello"

    def __call__(self):
        return "built-in"


class RootWithBuiltins(duho.LoggingArgs, duho.Cli):
    """A root that ships its own _subcommands_."""

    _subcommands_ = [_Hello]

    def __call__(self):  # pragma: no cover - root is not dispatched here
        return 0


def test_cmds_path_extends_builtin_subcommands(tmp_path, monkeypatch):
    """A CMDS_PATH command is ADDED to _subcommands_, not swapped in for them.

    Replacing them would make every invocation depend on the env var being
    right; the usual reason to point at a command dir is "a few extras".
    """
    _write(tmp_path, "greet.py", _MODULE_CMD_GREET)
    monkeypatch.setenv("DUHO_CMDS_PATH", str(tmp_path))
    env = duho.env.Env("DUHO")

    assert app(RootWithBuiltins, env=env, argv=["greet"], setup_logging=False) == "greeted"
    # The built-in still works -- this is the half that regressed before.
    assert app(RootWithBuiltins, env=env, argv=["hello"], setup_logging=False) == "built-in"


def test_cmds_path_command_overrides_same_named_builtin(tmp_path, monkeypatch):
    """A discovered command wins over a built-in of the same name."""
    _write(tmp_path, "hello.py", _MODULE_CMD_HELLO_OVERRIDE)
    monkeypatch.setenv("DUHO_CMDS_PATH", str(tmp_path))

    rc = app(RootWithBuiltins, env=duho.env.Env("DUHO"), argv=["hello"],
             setup_logging=False)
    assert rc == "overridden"


def test_builtin_subcommands_survive_without_cmds_path():
    """No CMDS_PATH set -> the root's own _subcommands_ are the command set."""
    rc = app(RootWithBuiltins, env=duho.env.Env("DUHO"), argv=["hello"],
             setup_logging=False)
    assert rc == "built-in"


def test_cmds_path_layers_on_top_of_explicit_commands(tmp_path, monkeypatch):
    """Regression: an explicit `commands=` list used to silently DISABLE
    CMDS_PATH entirely (an early-return branch, not a layer), even with
    `env=` also passed -- the operator's exported variable did nothing, with
    no warning. CMDS_PATH must now merge on top of `commands=` too."""
    _write(tmp_path, "greet.py", _MODULE_CMD_GREET)
    monkeypatch.setenv("DUHO_CMDS_PATH", str(tmp_path))
    env = duho.env.Env("DUHO")

    rc = app(
        RootWithBuiltins,
        commands=[_Hello],
        env=env,
        argv=["greet"],
        setup_logging=False,
    )
    assert rc == "greeted"
    # The explicitly-passed command still works alongside it.
    rc = app(
        RootWithBuiltins,
        commands=[_Hello],
        env=env,
        argv=["hello"],
        setup_logging=False,
    )
    assert rc == "built-in"


def test_cmds_path_layers_on_top_of_source(tmp_path, monkeypatch):
    """Same regression, for `source=` instead of `commands=`."""
    builtins_dir = tmp_path / "builtins"
    extra_dir = tmp_path / "extra"
    builtins_dir.mkdir()
    extra_dir.mkdir()
    _write(builtins_dir, "hello.py", "def main(args=None): return 'from-source'\n")
    _write(extra_dir, "greet.py", _MODULE_CMD_GREET)
    monkeypatch.setenv("DUHO_CMDS_PATH", str(extra_dir))
    env = duho.env.Env("DUHO")

    rc = app(RootWithBuiltins, source=builtins_dir, env=env, argv=["greet"],
             setup_logging=False)
    assert rc == "greeted"
    rc = app(RootWithBuiltins, source=builtins_dir, env=env, argv=["hello"],
             setup_logging=False)
    assert rc == "from-source"
