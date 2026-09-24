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

import logging
import os
import sys
from pathlib import Path

import pytest

import duho
from duho.discovery import ModuleCommand
from duho.env import Env
from duho.runtime import _module_args_cls, _resolve_commands, app, run_command

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


class PlainRoot(duho.Cmd):
    """A plain Cmd root with no _logger_ (not LoggingArgs)."""

    def __call__(self):  # pragma: no cover
        return 0


_MODULE_CMD_RECORDS_VERBOSE = '''\
"""A simple module command."""

SEEN = {}


def main(args):
    SEEN["verbose"] = getattr(args, "verbose", None)
    return 0
'''


# --------------------------------------------------------------------------
# A global declared on the root survives past dispatch to the subcommand
# --------------------------------------------------------------------------


def test_verbose_before_class_subcommand_survives(tmp_path):
    _write(tmp_path, "deploy.py", _CLASS_CMD_DEPLOY)
    captured = {}

    def capture(command, instance):
        captured["verbose"] = getattr(instance, "verbose", None)
        return 0

    app(
        Root,
        source=tmp_path,
        argv=["-v", "Deploy", "--name", "x"],
        setup_logging=False,
        dispatch=capture,
    )
    assert captured["verbose"] == 1


class _RootEnv(duho.LoggingArgs, duho.Cli):
    """Root with an env-backed global field."""

    token: duho.Arg[str, duho.NS(env="DUHO_TEST_ROOT_TOKEN")] = "class-default"
    "Auth token"
    ("--token",)

    def __call__(self):  # pragma: no cover - root not dispatched
        return 0


def test_root_env_survives_through_subcommand(tmp_path, monkeypatch):
    monkeypatch.setenv("DUHO_TEST_ROOT_TOKEN", "from-env")
    _write(tmp_path, "deploy.py", _CLASS_CMD_DEPLOY)
    captured = {}

    def capture(command, instance):
        captured["token"] = getattr(instance, "token", None)
        return 0

    app(
        _RootEnv,
        source=tmp_path,
        argv=["Deploy", "--name", "x"],
        setup_logging=False,
        dispatch=capture,
    )
    assert captured["token"] == "from-env"


class _CliReq(duho.LoggingArgs, duho.Cli):
    """Root with a required global (no class default)."""

    dsn: str
    "Database DSN"
    ("--dsn",)

    def __call__(self):  # pragma: no cover - root not dispatched
        return 0


@pytest.mark.requires_toml
def test_config_required_global_with_module_command(tmp_path):
    """A config-supplied required global must not hard-exit the advisory
    prepass, which runs before config layering applies."""
    cfg = tmp_path / "app.toml"
    cfg.write_text('dsn = "postgres://x"\n')
    _write(tmp_path, "backup.py", _MODULE_CMD_RECORDS_VERBOSE)
    rc = app(
        _CliReq,
        source=tmp_path,
        argv=["backup"],
        config=cfg,
        setup_logging=False,
    )
    assert rc == 0


# --------------------------------------------------------------------------
# A class command wins a name collision against a module of the same name
# --------------------------------------------------------------------------

_COLLISION_RAN = {}


class _DeployClass(duho.Cmd):
    """Class command colliding with a module named 'deploy'."""

    _parsername_ = "deploy"

    def __call__(self):
        _COLLISION_RAN["who"] = "class"
        return 0


def test_name_collision_last_wins_and_dispatch_agrees(tmp_path, caplog):
    _COLLISION_RAN.clear()
    _write(tmp_path, "deploy.py", _MODULE_CMD_RECORDS_VERBOSE)
    module_cmds = duho.discover_commands(tmp_path)
    # Put the class command LAST so it is the last registered -> should win.
    commands = list(module_cmds) + [_DeployClass]

    with caplog.at_level("WARNING", logger="duho"):
        rc = app(
            Root,
            commands=commands,
            argv=["deploy"],
            setup_logging=False,
        )
    assert rc == 0
    assert _COLLISION_RAN.get("who") == "class"
    assert any("deploy" in rec.getMessage() for rec in caplog.records)


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
    _write(tmp_path, "backup.py", _MODULE_CMD_LIFECYCLE)
    rc = app(Root, source=tmp_path, argv=["backup"], setup_logging=False)
    assert rc == 0
    # Read the recorded TRACE off the module discovery already imported --
    # importing the fixture file again here would be a SEPARATE fresh module,
    # not the one that actually ran.
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


# --------------------------------------------------------------------------
# Module commands can declare their own Args class (declarative fields)
# --------------------------------------------------------------------------

_MODULE_CMD_ARGS_CLASS = '''\
"""A module command declaring its own Args class instead of register()."""

import duho

SEEN = {}


class Args(duho.LoggingArgs):
    """Subclasses the app's LoggingArgs-based root shape."""

    method: str
    "The method to call."

    params: "list[str]" = []
    "Extra params."
    ("--params",)


def main(args):
    SEEN["method"] = args.method
    SEEN["params"] = args.params
    return None
'''

_MODULE_CMD_PLAIN_ARGS_CLASS = '''\
"""A module command whose Args does NOT subclass anything duho-related."""

SEEN = {}


class Args:
    """A plain class -- no import of duho.Args, no subclassing the app root."""

    method: str


def main(args):
    SEEN["method"] = args.method
    SEEN["has_verbose"] = hasattr(args, "verbose")
    return None
'''

_MODULE_CMD_ARGS_CLASS_PLUS_REGISTER = '''\
"""Args declares `method`; register() adds a positional AFTER it."""

SEEN = {}


class Args:
    method: str


def register(parser, args):
    parser.add_argument("extra")


def main(args):
    SEEN["method"] = args.method
    SEEN["extra"] = args.extra
    return None
'''


def test_module_args_class_adds_declared_fields(tmp_path):
    """A module's own `Args` class fields are added declaratively."""
    _write(tmp_path, "callit.py", _MODULE_CMD_ARGS_CLASS)
    rc = app(
        Root,
        source=tmp_path,
        argv=[
            "callit",
            "--method",
            "system.info",
            "--params",
            "a",
            "--params",
            "b",
        ],
        setup_logging=False,
    )
    assert rc == 0
    discovered = [
        m
        for name, m in sys.modules.items()
        if name.startswith("duho._discovered.") and name.endswith("callit")
    ][0]
    assert discovered.SEEN["method"] == "system.info"
    assert discovered.SEEN["params"] == ["a", "b"]


def test_module_plain_args_class_mixed_with_root(tmp_path):
    """A module's plain (non-Args) `Args` class is mixed with the app root.

    Its own annotated field (`method`) works as a declared CLI field, AND the
    parsed instance still carries the root's own fields (`verbose`, from
    `Root`'s `LoggingArgs`) -- confirming the synthesized mixin actually
    combines both, not just the module's bare class alone.
    """
    _write(tmp_path, "plainargs.py", _MODULE_CMD_PLAIN_ARGS_CLASS)
    rc = app(
        Root,
        source=tmp_path,
        argv=["plainargs", "--method", "system.info"],
        setup_logging=False,
    )
    assert rc == 0
    discovered = [
        m
        for name, m in sys.modules.items()
        if name.startswith("duho._discovered.") and name.endswith("plainargs")
    ][0]
    assert discovered.SEEN["method"] == "system.info"
    assert discovered.SEEN["has_verbose"] is True


def test_module_args_class_fields_precede_register_added_ones(tmp_path):
    """Declared Args fields are added BEFORE register() runs (declared first,
    register-added positionals last -- matching the existing convention of
    calling a shared trailing-positional helper LAST inside register())."""
    _write(tmp_path, "ordered.py", _MODULE_CMD_ARGS_CLASS_PLUS_REGISTER)
    rc = app(
        Root,
        source=tmp_path,
        argv=["ordered", "--method", "system.info", "trailing-value"],
        setup_logging=False,
    )
    assert rc == 0
    discovered = [
        m
        for name, m in sys.modules.items()
        if name.startswith("duho._discovered.") and name.endswith("ordered")
    ][0]
    assert discovered.SEEN["method"] == "system.info"
    assert discovered.SEEN["extra"] == "trailing-value"


_MODULE_CMD_ARGS_CLASS_WITH_CONFLICTS = '''\
"""A module command declaring a mutually-exclusive pair via NS(conflicts=...)."""

import duho
from duho import Arg, NS

SEEN = {}


class Args:
    fast: Arg[bool, NS(conflicts="mode")] = False
    ("--fast",)

    slow: Arg[bool, NS(conflicts="mode")] = False
    ("--slow",)


def main(args):
    SEEN["fast"] = args.fast
    SEEN["slow"] = args.slow
    return None
'''


def test_module_args_class_supports_mutually_exclusive_conflicts(tmp_path):
    """A module command's own declared fields now honor ``NS(conflicts=...)``
    the same as a class command's -- this used to need ``_initparser_``'s
    fuller machinery (only available to a class command) and silently had no
    support for it at all."""
    _write(tmp_path, "modeflag.py", _MODULE_CMD_ARGS_CLASS_WITH_CONFLICTS)
    rc = app(
        Root,
        source=tmp_path,
        argv=["modeflag", "--fast"],
        setup_logging=False,
    )
    assert rc == 0
    discovered = [
        m
        for name, m in sys.modules.items()
        if name.startswith("duho._discovered.") and name.endswith("modeflag")
    ][0]
    assert discovered.SEEN["fast"] is True
    assert discovered.SEEN["slow"] is False

    with pytest.raises(SystemExit):
        app(
            Root,
            source=tmp_path,
            argv=["modeflag", "--fast", "--slow"],
            setup_logging=False,
        )


def test_module_declared_fields_share_the_same_field_wiring_as_a_class_command():
    """`runtime._add_module_declared_fields` is a thin wrapper around the same
    `duho.args._add_fields` a class command's own `_initparser_` uses, not an
    independent, hand-kept copy."""
    import duho.args as args_mod
    import duho.runtime as runtime_mod

    assert runtime_mod._add_fields is args_mod._add_fields


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

    rc = app(
        Root,
        commands=[command],
        argv=["norereg", "--wrapped", "x"],
        setup_logging=False,
    )
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

    spec = importlib.util.spec_from_file_location(
        "_direct_deploy", tmp_path / "deploy.py"
    )
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


_MODULE_CMD_NONZERO = '''\
"""A module command whose main returns a non-zero exit code."""

TRACE = []


def init(args):
    return "ctx"


def main(args):
    return 2


def success(ctx, args):
    TRACE.append("success")


def finally_(ctx, args):
    TRACE.append("finally")
'''

_MODULE_CMD_RAISES_AND_FINALLY_RAISES = '''\
"""main raises; finally_ also raises -- the original must propagate."""


def init(args):
    return "ctx"


def main(args):
    raise RuntimeError("original")


def success(ctx, args):
    pass


def finally_(ctx, args):
    raise RuntimeError("from-finally")
'''


def _module_command_from(tmp_path, name, source):
    (tmp_path / name).write_text(source)
    return duho.discover_commands(tmp_path)[0]


def test_success_not_run_on_nonzero(tmp_path):
    cmd = _module_command_from(tmp_path, "job.py", _MODULE_CMD_NONZERO)
    rc = run_command(cmd, object())
    assert rc == 2
    assert cmd.module.TRACE == ["finally"]  # success skipped, finally ran


def test_finally_does_not_mask_original_exception(tmp_path):
    cmd = _module_command_from(
        tmp_path, "job2.py", _MODULE_CMD_RAISES_AND_FINALLY_RAISES
    )
    with pytest.raises(RuntimeError, match="original"):
        run_command(cmd, object())


# --------------------------------------------------------------------------
# run_command delivers the documented "duho" logger fallback (R018)
# --------------------------------------------------------------------------

_MODULE_HOOK_READS_LOGGER = '''\
"""A module command whose hooks read args._logger_ directly (documented convention)."""
SEEN = {}


def init(args=None):
    SEEN["logger_name"] = getattr(args, "_logger_", None).name
    return None


def main(args=None):
    args._logger_.info("hello")
    return 0
'''


def test_run_command_delivers_duho_logger_fallback_to_module_hooks(tmp_path):
    """A module command's hooks/entrypoint may read ``args._logger_`` directly,
    per the documented convention -- even against a PLAIN root with no
    ``LoggingArgs`` (e.g. ``duho.app(root=None, source=...)``, a common
    plugin-only shape). ``run_command`` must deliver the ``"duho"`` fallback
    logger itself, rather than letting a bare ``argparse.Namespace`` (no
    ``_logger_`` of its own) raise ``AttributeError`` on first use.
    """
    mod_path = _write(tmp_path, "plain_hook.py", _MODULE_HOOK_READS_LOGGER)
    import importlib.util

    spec = importlib.util.spec_from_file_location("_r018_plain_hook", mod_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_r018_plain_hook"] = module
    try:
        spec.loader.exec_module(module)
        command = ModuleCommand(module, name="plain-hook")
        instance = duho.NS()  # no _logger_ of its own
        rc = run_command(command, instance)
        assert rc == 0
        assert module.SEEN["logger_name"] == "duho"
        assert instance._logger_.name == "duho"
    finally:
        sys.modules.pop("_r018_plain_hook", None)


def test_run_command_does_not_override_an_existing_logger(tmp_path):
    """A root that already has a real ``_logger_`` (e.g. ``LoggingArgs``-based)
    keeps its own -- the fallback only fills a gap, it never overrides."""
    mod_path = _write(tmp_path, "has_logger.py", _MODULE_HOOK_READS_LOGGER)
    import importlib.util

    spec = importlib.util.spec_from_file_location("_r018_has_logger", mod_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_r018_has_logger"] = module
    try:
        spec.loader.exec_module(module)
        command = ModuleCommand(module, name="has-logger")
        instance = duho.NS()
        instance._logger_ = duho.logging.getLogger("scoped.logger")
        run_command(command, instance)
        assert module.SEEN["logger_name"] == "scoped.logger"
    finally:
        sys.modules.pop("_r018_has_logger", None)


def test_register_hook_logger_uses_module_commands_own_resolution():
    """The 3-arg ``register`` hook's logger and ``ModuleCommand._logger_for``
    must be ONE shared resolution (D047), not two independently-maintained
    copies that could silently diverge -- a bare structural check that the
    duplicate ``runtime._HOOK_LOGGER`` is gone and the call site reuses
    ``command._logger_for``.
    """
    import inspect

    from duho import runtime as _runtime

    assert not hasattr(_runtime, "_HOOK_LOGGER")
    source = inspect.getsource(_runtime._register_module_command)
    assert "_logger_for" in source


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


def test_resolve_commands_without_cmds_path_does_not_import_cwd(tmp_path, monkeypatch):
    """A missing CMDS_PATH env value must never glob-import the CWD."""
    # A canary module that raises on import if duho ever glob-imports the CWD.
    (tmp_path / "canary.py").write_text("raise RuntimeError('CWD import happened')\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CANARY_CMDS_PATH", raising=False)

    env = duho.env.Env("canary")  # no CANARY_CMDS_PATH set
    assert _resolve_commands(None, None, None, env) == []


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

    assert (
        app(RootWithBuiltins, env=env, argv=["greet"], setup_logging=False) == "greeted"
    )
    # The built-in still works -- this is the half that regressed before.
    assert (
        app(RootWithBuiltins, env=env, argv=["hello"], setup_logging=False)
        == "built-in"
    )


def test_cmds_path_command_overrides_same_named_builtin(tmp_path, monkeypatch):
    """A discovered command wins over a built-in of the same name."""
    _write(tmp_path, "hello.py", _MODULE_CMD_HELLO_OVERRIDE)
    monkeypatch.setenv("DUHO_CMDS_PATH", str(tmp_path))

    rc = app(
        RootWithBuiltins, env=duho.env.Env("DUHO"), argv=["hello"], setup_logging=False
    )
    assert rc == "overridden"


def test_builtin_subcommands_survive_without_cmds_path():
    """No CMDS_PATH set -> the root's own _subcommands_ are the command set."""
    rc = app(
        RootWithBuiltins, env=duho.env.Env("DUHO"), argv=["hello"], setup_logging=False
    )
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

    rc = app(
        RootWithBuiltins,
        source=builtins_dir,
        env=env,
        argv=["greet"],
        setup_logging=False,
    )
    assert rc == "greeted"
    rc = app(
        RootWithBuiltins,
        source=builtins_dir,
        env=env,
        argv=["hello"],
        setup_logging=False,
    )
    assert rc == "from-source"


# --------------------------------------------------------------------------
# Overriding a command deregisters its aliases too (D024)
# --------------------------------------------------------------------------


class _AliasedDeploy(duho.LoggingArgs, duho.Cmd):
    """A built-in with an alias, carried on the root class."""

    _parsername_ = "deploy"
    _parseraliases_ = ["d"]

    def __call__(self):
        return "built-in-deploy"


class RootWithAliasedBuiltin(duho.LoggingArgs, duho.Cli):
    """A root whose only built-in subcommand declares an alias."""

    _subcommands_ = [_AliasedDeploy]

    def __call__(self):  # pragma: no cover - root is not dispatched here
        return 0


_MODULE_CMD_DEPLOY_OVERRIDE = '''\
"""Shadows the built-in deploy."""


def main(args=None):
    return "overridden-deploy"
'''


def test_cmds_path_override_deregisters_the_shadowed_commands_aliases(
    tmp_path, monkeypatch, capsys
):
    """Overriding a built-in via CMDS_PATH also drops its stale aliases.

    Before the fix, `_deregister_subparser` popped only the primary name from
    argparse's `_name_parser_map`; the shadowed command's alias (`d`) stayed
    registered and kept SILENTLY dispatching to the OLD command even though
    `deploy` itself now ran the override (D024). The module override declares
    no alias of its own, so the correct post-fix outcome for `d` is an
    ordinary "invalid choice" (the alias is gone, not secretly re-pointed) --
    never a silent run of the shadowed built-in.
    """
    _write(tmp_path, "deploy.py", _MODULE_CMD_DEPLOY_OVERRIDE)
    monkeypatch.setenv("DUHO_CMDS_PATH", str(tmp_path))
    env = duho.env.Env("DUHO")

    rc = app(RootWithAliasedBuiltin, env=env, argv=["deploy"], setup_logging=False)
    assert rc == "overridden-deploy"
    with pytest.raises(SystemExit):
        app(RootWithAliasedBuiltin, env=env, argv=["d"], setup_logging=False)
    assert "invalid choice: 'd'" in capsys.readouterr().err


class _DeployPatch(duho.LoggingArgs, duho.Cmd):
    """An explicit override reusing the SAME name and alias as the built-in."""

    _parsername_ = "deploy"
    _parseraliases_ = ["d"]

    def __call__(self):
        return "patched-deploy"


def test_commands_override_reusing_the_same_alias_does_not_crash():
    """A class-command override that reuses the shadowed command's own alias
    must not raise argparse's `conflicting subparser alias` (3.11+) nor
    silently leave the alias pointing at the old command (3.9) (D024)."""
    rc = app(
        RootWithAliasedBuiltin,
        commands=[_DeployPatch],
        argv=["deploy"],
        setup_logging=False,
    )
    assert rc == "patched-deploy"
    rc = app(
        RootWithAliasedBuiltin,
        commands=[_DeployPatch],
        argv=["d"],
        setup_logging=False,
    )
    assert rc == "patched-deploy"


# --------------------------------------------------------------------------
# commands=/source=/entry_points= are additive with a root's own
# _subcommands_, not a replacement for them (D025)
# --------------------------------------------------------------------------


class _Extra(duho.Cmd):
    """An explicitly-passed command unrelated to any built-in."""

    _parsername_ = "extra"

    def __call__(self):
        return "extra"


def test_commands_arg_is_additive_with_root_builtins():
    """commands=[...] adds to root._subcommands_; both remain callable."""
    rc = app(RootWithBuiltins, commands=[_Extra], argv=["hello"], setup_logging=False)
    assert rc == "built-in"
    rc = app(RootWithBuiltins, commands=[_Extra], argv=["extra"], setup_logging=False)
    assert rc == "extra"


def test_source_arg_is_additive_with_root_builtins(tmp_path):
    """source=dir adds to root._subcommands_; both remain callable."""
    _write(tmp_path, "extra.py", _MODULE_CMD_GREET)
    rc = app(RootWithBuiltins, source=tmp_path, argv=["hello"], setup_logging=False)
    assert rc == "built-in"
    rc = app(RootWithBuiltins, source=tmp_path, argv=["extra"], setup_logging=False)
    assert rc == "greeted"


# --------------------------------------------------------------------------
# Override/collision logging: deferred, once, and INFO vs WARNING (D042)
# --------------------------------------------------------------------------


def test_cmds_path_override_logs_info_once_never_a_warning(
    tmp_path, monkeypatch, caplog
):
    """The documented CMDS_PATH-overrides-a-builtin story logs INFO exactly
    once; it must never ALSO trip the generic 'registered by more than one
    source' WARNING (that one is for a genuinely separate collision)."""
    _write(tmp_path, "hello.py", _MODULE_CMD_HELLO_OVERRIDE)
    monkeypatch.setenv("DUHO_CMDS_PATH", str(tmp_path))
    env = duho.env.Env("DUHO")

    with caplog.at_level("INFO", logger="duho.runtime"):
        rc = app(RootWithBuiltins, env=env, argv=["hello"], setup_logging=False)
    assert rc == "overridden"
    info_messages = [r.message for r in caplog.records if r.levelname == "INFO"]
    warning_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert sum("overrides the built-in" in m for m in info_messages) == 1
    assert not warning_messages


def test_genuine_collision_between_two_explicit_sources_still_warns(tmp_path, caplog):
    """Two independently-resolved commands sharing a name (not the documented
    CMDS_PATH override) is a real ambiguity and must still warn."""
    _write(tmp_path, "hello.py", _MODULE_CMD_HELLO_OVERRIDE)

    with caplog.at_level("WARNING", logger="duho.runtime"):
        rc = app(RootWithBuiltins, source=tmp_path, argv=["hello"], setup_logging=False)
    assert rc == "overridden"
    assert any(
        "registered by more than one source" in r.message for r in caplog.records
    )


# --------------------------------------------------------------------------
# register()'s ArgumentError rewrap only blames a global when one actually
# conflicted (D043)
# --------------------------------------------------------------------------

_MODULE_CMD_REGISTER_SELF_COLLISION = '''\
"""A register() hook that collides with the module's OWN declared field."""


class Args:
    """A plain Args class with a --url field."""

    url: str = "default"
    "The url."
    ("--url",)


def register(parser, args):
    parser.add_argument("--url")


def main(args):
    return None
'''


def test_register_hook_self_collision_error_does_not_blame_a_global(tmp_path):
    """A register() hook colliding with the module's OWN declared field (not
    an inherited global) must not be told to rename a nonexistent global
    flag."""
    import argparse

    _write(tmp_path, "selfcollide.py", _MODULE_CMD_REGISTER_SELF_COLLISION)
    with pytest.raises(argparse.ArgumentError) as excinfo:
        app(Root, source=tmp_path, argv=["selfcollide", "--help"], setup_logging=False)
    msg = str(excinfo.value)
    assert "selfcollide" in msg
    assert "global" not in msg
    assert "--url" in msg


# --------------------------------------------------------------------------
# A non-command in commands=... fails loudly (D045)
# --------------------------------------------------------------------------


def test_commands_arg_with_a_non_command_raises_typeerror():
    """A non-Cmd/non-ModuleCommand item in commands=... must fail loudly,
    naming the bad object, instead of being silently dropped."""

    class NotACommand:
        pass

    with pytest.raises(TypeError, match="NotACommand"):
        app(Root, commands=[NotACommand()], argv=["x"], setup_logging=False)


# --------------------------------------------------------------------------
# A keyword-only register() logger parameter is passed by keyword (D061)
# --------------------------------------------------------------------------

_MODULE_CMD_REGISTER_KWONLY_LOGGER = '''\
"""A register hook with a keyword-only logger parameter."""
import logging

SEEN = {}


def register(parser, args, *, logger):
    SEEN["logger_is_logger"] = isinstance(logger, logging.Logger)
    parser.add_argument("--flag", default="unset")


def main(args):
    SEEN["flag"] = getattr(args, "flag", None)
    return None
'''


def test_register_hook_keyword_only_logger_is_called_by_keyword(tmp_path):
    """`register(parser, args, *, logger)` cannot be called positionally
    (raises TypeError: too many positional arguments); it must be called
    `logger=...`."""
    _write(tmp_path, "kwreg.py", _MODULE_CMD_REGISTER_KWONLY_LOGGER)
    rc = app(Root, source=tmp_path, argv=["kwreg", "--flag", "kw"], setup_logging=False)
    assert rc == 0
    discovered = [
        m
        for name, m in sys.modules.items()
        if name.startswith("duho._discovered.") and name.endswith("kwreg")
    ][0]
    assert discovered.SEEN["logger_is_logger"] is True
    assert discovered.SEEN["flag"] == "kw"


# --------------------------------------------------------------------------
# register()'s arity detection reads its OWN signature, not a wrapped
# function's (functools.wraps guidance, mirrors runpath's D014 fix)
# --------------------------------------------------------------------------

_MODULE_CMD_REGISTER_WRAPPED = '''\
"""A register hook wrapped with functools.wraps: the WRAPPER's own signature
(3-arg, with a logger) differs from the function it wraps (2-arg)."""
import functools
import logging

SEEN = {}


def _original(parser, args):
    pass


@functools.wraps(_original)
def register(parser, args, logger):
    SEEN["logger_is_logger"] = isinstance(logger, logging.Logger)
    parser.add_argument("--flag", default="unset")


def main(args):
    SEEN["flag"] = getattr(args, "flag", None)
    return None
'''


def test_register_hook_wrapped_with_functools_wraps_uses_its_own_signature(tmp_path):
    """Inspecting the WRAPPED function (`follow_wrapped=True`, inspect's
    default) would see the 2-arg original and never pass a logger, silently
    dropping the wrapper's own extra parameter."""
    _write(tmp_path, "wrapreg.py", _MODULE_CMD_REGISTER_WRAPPED)
    rc = app(
        Root, source=tmp_path, argv=["wrapreg", "--flag", "w"], setup_logging=False
    )
    assert rc == 0
    discovered = [
        m
        for name, m in sys.modules.items()
        if name.startswith("duho._discovered.") and name.endswith("wrapreg")
    ][0]
    assert discovered.SEEN["logger_is_logger"] is True
    assert discovered.SEEN["flag"] == "w"


# --------------------------------------------------------------------------
# A module command's async hooks are rejected loudly, never silently skipped
# --------------------------------------------------------------------------

_MODULE_CMD_ASYNC_MAIN = '''\
"""A module command whose main is async."""


async def main(args):
    return None
'''

_MODULE_CMD_ASYNC_INIT = '''\
"""A module command whose init is async."""


async def init(args):
    return None


def main(args):
    return None
'''


def test_async_module_command_main_raises_type_error(tmp_path):
    """An `async def main()` must be rejected loudly instead of silently
    never running (mirrors runpath's async-step rejection)."""
    _write(tmp_path, "asyncmain.py", _MODULE_CMD_ASYNC_MAIN)
    with pytest.raises(TypeError, match="coroutine"):
        app(Root, source=tmp_path, argv=["asyncmain"], setup_logging=False)


def test_async_module_command_init_raises_type_error(tmp_path):
    """An `async def init()` must be rejected loudly too."""
    _write(tmp_path, "asyncinit.py", _MODULE_CMD_ASYNC_INIT)
    with pytest.raises(TypeError, match="coroutine"):
        app(Root, source=tmp_path, argv=["asyncinit"], setup_logging=False)


# --------------------------------------------------------------------------
# Module command subparsers get the positional-reorder fix too
# --------------------------------------------------------------------------

# A module command shaped like the downstream-consumer repro this regression
# test guards: a fixed positional (`ns`) declared via `Args`, plus a `register()`
# hook that adds a `-f`/`--filter` flag AND a trailing variadic `targets`
# positional. Placing `-f` BETWEEN `ns` and `targets` on argv used to break
# because module command subparsers are a plain `subparsers.add_parser(...)`
# instance, never patched with the reorder fix declarative `Args`/`Cmd`
# subcommands get.
_MODULE_CMD_QUERY_SHAPED = '''\
"""A module command with a positional, a flag, then a variadic positional."""

SEEN = {}


class Args:
    ns: str
    ("ns",)


def register(parser, args):
    parser.add_argument("-f", "--filter", default=None)
    parser.add_argument("targets", nargs="*")


def main(args):
    SEEN["ns"] = args.ns
    SEEN["filter"] = args.filter
    SEEN["targets"] = args.targets
    return None
'''


def test_module_command_reorders_flag_between_positionals(tmp_path):
    """A flag between a module command's own positional and a variadic one parses.

    Regression test for the finding that `_register_module_command` built an
    unpatched subparser, so this exact shape (`query <ns> -f <val> <targets...>`)
    raised `unrecognized arguments` even though the same shape on a declarative
    `Cmd` subcommand already worked via the positional-reorder fix.
    """
    _write(tmp_path, "query.py", _MODULE_CMD_QUERY_SHAPED)
    rc = app(
        Root,
        source=tmp_path,
        argv=["query", "user", "-f", "username=root", "nas1"],
        setup_logging=False,
    )
    assert rc == 0
    discovered = [
        m
        for name, m in sys.modules.items()
        if name.startswith("duho._discovered.") and name.endswith("query")
    ][0]
    assert discovered.SEEN["ns"] == "user"
    assert discovered.SEEN["filter"] == "username=root"
    assert discovered.SEEN["targets"] == ["nas1"]


# --------------------------------------------------------------------------
# D021: app() must thread env/config down to a class command's OWN nested
# `_subcommands_`, and to a module command's declared `Args` class -- not
# only to the root and top-level class commands.
# --------------------------------------------------------------------------

_CLASS_CMD_NESTED_D021 = '''\
"""Remote operations."""
from duho import Cmd, Arg, NS


class Push(Cmd):
    """Push to a remote."""

    url: "Arg[str, NS(env='DUHO_TEST_D021_URL')]" = "default-url"
    "Target url"
    ("--url",)

    def __call__(self):
        return "push " + self.url


class Remote(Cmd):
    """Remote command group."""

    _subcommands_ = [Push]

    def __call__(self):  # pragma: no cover - not dispatched directly
        return 0
'''


def test_app_threads_env_to_nested_class_subcommand(tmp_path, monkeypatch):
    _write(tmp_path, "remote.py", _CLASS_CMD_NESTED_D021)
    monkeypatch.setenv("DUHO_TEST_D021_URL", "from-env")
    rc = app(Root, source=tmp_path, argv=["Remote", "Push"], setup_logging=False)
    monkeypatch.delenv("DUHO_TEST_D021_URL", raising=False)
    assert rc == "push from-env"


@pytest.mark.requires_toml
def test_app_threads_config_to_nested_class_subcommand(tmp_path):
    _write(tmp_path, "remote.py", _CLASS_CMD_NESTED_D021)
    cfg = tmp_path / "app.toml"
    cfg.write_text('[Remote.Push]\nurl = "from-config"\n')
    rc = app(
        Root,
        source=tmp_path,
        argv=["Remote", "Push"],
        config=str(cfg),
        setup_logging=False,
    )
    assert rc == "push from-config"


_MODULE_CMD_ARGS_ENV_D021 = '''\
"""A module command whose declared Args field is backed by env/config."""
from duho import Arg, NS

SEEN = {}


class Args:
    token: "Arg[str, NS(env='DUHO_TEST_D021_MODTOKEN')]" = "unset"
    "Auth token"
    ("--token",)


def main(args):
    SEEN["token"] = args.token
    return None
'''


def _discovered_module(name):
    return [
        m
        for mod_name, m in sys.modules.items()
        if mod_name.startswith("duho._discovered.") and mod_name.endswith(name)
    ][0]


def test_app_threads_env_to_module_declared_args_class(tmp_path, monkeypatch):
    _write(tmp_path, "modtok.py", _MODULE_CMD_ARGS_ENV_D021)
    monkeypatch.setenv("DUHO_TEST_D021_MODTOKEN", "from-env")
    rc = app(Root, source=tmp_path, argv=["modtok"], setup_logging=False)
    monkeypatch.delenv("DUHO_TEST_D021_MODTOKEN", raising=False)
    assert rc == 0
    assert _discovered_module("modtok").SEEN["token"] == "from-env"


@pytest.mark.requires_toml
def test_app_threads_config_to_module_declared_args_class(tmp_path):
    _write(tmp_path, "modtok.py", _MODULE_CMD_ARGS_ENV_D021)
    cfg = tmp_path / "app.toml"
    cfg.write_text('[modtok]\ntoken = "from-config"\n')
    rc = app(
        Root, source=tmp_path, argv=["modtok"], config=str(cfg), setup_logging=False
    )
    assert rc == 0
    assert _discovered_module("modtok").SEEN["token"] == "from-config"


# --------------------------------------------------------------------------
# app() must keep a subcommand's DELIBERATELY redeclared default,
# both for a builtin (`_subcommands_`) and a `source=`-discovered one (whose
# subparser shares the root's Action objects via `parents=[base_parser]`).
# --------------------------------------------------------------------------


class _DeployBuiltinD022(duho.Cmd):
    """A builtin subcommand redeclaring `region` with its own default."""

    region: str = "eu"
    ("--region",)

    def __call__(self):
        return "region=" + self.region


class _RegionRootBuiltinD022(duho.Cli):
    """A root whose OWN `region` default differs from its builtin child's."""

    region: str = "us"
    ("--region",)

    _subcommands_ = [_DeployBuiltinD022]

    def __call__(self):  # pragma: no cover - root is not dispatched
        return 0


def test_app_builtin_subcommand_keeps_redeclared_default():
    rc = app(_RegionRootBuiltinD022, argv=["_DeployBuiltinD022"], setup_logging=False)
    assert rc == "region=eu"


class _RegionRootD022(duho.Cli):
    """A `commands=`/`source=`-only root -- no builtin `_subcommands_`."""

    region: str = "us"
    ("--region",)

    def __call__(self):  # pragma: no cover - root is not dispatched
        return 0


_CLASS_CMD_REGION_OVERRIDE_D022 = '''\
"""Deploy with its own region default, different from the app root's."""
from duho import Cmd


class Deploy(Cmd):
    """Deploy somewhere."""

    region: str = "eu"
    "Target region"
    ("--region",)

    def __call__(self):
        return "region=" + self.region
'''


def test_app_discovered_class_command_keeps_redeclared_default(tmp_path):
    _write(tmp_path, "deploy.py", _CLASS_CMD_REGION_OVERRIDE_D022)
    rc = app(_RegionRootD022, source=tmp_path, argv=["Deploy"], setup_logging=False)
    assert rc == "region=eu"


# --------------------------------------------------------------------------
# D023: a required global given AFTER the subcommand must be accepted, just
# like one given before it -- for both a module and a class command.
# --------------------------------------------------------------------------


class _TokenRootD023(duho.Cli):
    """A root with a REQUIRED global (no class default)."""

    token: int
    "Auth token"
    ("--token",)

    def __call__(self):  # pragma: no cover - root is not dispatched
        return 0


_MODULE_CMD_BACKUP_D023 = '''\
"""Backup command."""


def main(args):
    return "token=" + str(args.token)
'''

_CLASS_CMD_BACKUP_D023 = '''\
"""Backup command (class)."""
from duho import Cmd


class BackupCls(Cmd):
    """Backup, as a class command."""

    def __call__(self):
        return "token=" + str(self.token)
'''


def test_app_required_global_after_module_subcommand(tmp_path):
    _write(tmp_path, "backup.py", _MODULE_CMD_BACKUP_D023)
    rc = app(
        _TokenRootD023,
        source=tmp_path,
        argv=["backup", "--token", "5"],
        setup_logging=False,
    )
    assert rc == "token=5"


def test_app_required_global_after_class_subcommand(tmp_path):
    _write(tmp_path, "backupcls.py", _CLASS_CMD_BACKUP_D023)
    rc = app(
        _TokenRootD023,
        source=tmp_path,
        argv=["BackupCls", "--token", "5"],
        setup_logging=False,
    )
    assert rc == "token=5"


def test_app_required_global_before_subcommand_still_works(tmp_path):
    _write(tmp_path, "backup.py", _MODULE_CMD_BACKUP_D023)
    rc = app(
        _TokenRootD023,
        source=tmp_path,
        argv=["--token", "5", "backup"],
        setup_logging=False,
    )
    assert rc == "token=5"


def test_app_missing_required_global_reports_clear_error(tmp_path, capsys):
    _write(tmp_path, "backup.py", _MODULE_CMD_BACKUP_D023)
    with pytest.raises(SystemExit) as exc:
        app(_TokenRootD023, source=tmp_path, argv=["backup"], setup_logging=False)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--token" in err
    assert "required" in err


# --------------------------------------------------------------------------
# CMDS_PATH resolution: multi-dir joins, security, resilience, expansion
# --------------------------------------------------------------------------


def test_resolve_commands_from_cmds_path_env(tmp_path, monkeypatch):
    """env.paths('CMDS_PATH', ty=Path) with a real dir resolves its commands.

    The single-dir value is an absolute path -- on Windows it carries a
    drive-letter colon (``C:\\...``), which must NOT be split (see
    ``Env.paths`` / ``os.pathsep``).
    """
    monkeypatch.delenv("PATHSEP", raising=False)
    cmd_dir = tmp_path / "cmds"
    cmd_dir.mkdir()
    _write(cmd_dir, "deploy.py", _CLASS_CMD_DEPLOY)
    monkeypatch.setenv("MYAPP_CMDS_PATH", str(cmd_dir))
    env = Env("myapp")

    resolved = _resolve_commands(Root, None, None, env, None)
    names = {getattr(c, "__name__", "") for c in resolved}
    assert "Deploy" in names


def test_resolve_commands_from_multi_cmds_path_env(tmp_path, monkeypatch):
    """Two dirs joined by the OS path separator both resolve."""
    monkeypatch.delenv("PATHSEP", raising=False)
    dir_a = tmp_path / "a"
    dir_a.mkdir()
    _write(dir_a, "deploy.py", _CLASS_CMD_DEPLOY)
    dir_b = tmp_path / "b"
    dir_b.mkdir()
    _write(dir_b, "release.py", _CLASS_CMD_DEPLOY.replace("Deploy", "Release"))
    monkeypatch.setenv("MYAPP_CMDS_PATH", os.pathsep.join([str(dir_a), str(dir_b)]))
    env = Env("myapp")

    resolved = _resolve_commands(Root, None, None, env, None)
    names = {getattr(c, "__name__", "") for c in resolved}
    assert {"Deploy", "Release"} <= names


def test_app_dispatches_command_from_cmds_path_env(tmp_path, monkeypatch):
    """End-to-end: a CMDS_PATH-resolved command dispatches through app()."""
    cmd_dir = tmp_path / "cmds"
    cmd_dir.mkdir()
    _write(cmd_dir, "deploy.py", _CLASS_CMD_DEPLOY)
    monkeypatch.setenv("MYAPP_CMDS_PATH", str(cmd_dir))
    env = Env("myapp")

    rc = app(Root, env=env, argv=["Deploy", "--name", "x"], setup_logging=False)
    assert rc == "deployed x"


_MODULE_CMD_EVIL = '''\
"""A CWD command that must never be imported."""
from pathlib import Path

Path(r"{marker}").write_text("evil ran")


def main(args=None):
    return "evil ran"
'''


def test_cmds_path_empty_segment_never_imports_cwd(tmp_path, monkeypatch):
    """A leading, trailing, doubled, or separator-only CMDS_PATH value must
    never import (let alone execute) anything from the current directory.

    An empty CMDS_PATH segment used to become ``Path("")`` (== ``Path(".")``),
    which glob-imported and EXECUTED every top-level ``.py`` file in the CWD
    at import time -- the marker file below is written as an IMPORT-TIME side
    effect, not by calling the command, so even a bare resolution (no
    dispatch) must not trigger it. ``os.pathsep`` on this box is ``;``
    (Windows); the fix does not hard-code it.
    """
    real_cmds = tmp_path / "cmds"
    real_cmds.mkdir()
    _write(real_cmds, "deploy.py", _CLASS_CMD_DEPLOY)

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    marker = cwd / "CANARY_IMPORTED.txt"
    _write(cwd, "evil.py", _MODULE_CMD_EVIL.format(marker=marker))

    monkeypatch.chdir(cwd)
    monkeypatch.delenv("PATHSEP", raising=False)
    sep = os.pathsep

    cases = {
        "leading": f"{sep}{real_cmds}",
        "trailing": f"{real_cmds}{sep}",
        "doubled": f"{real_cmds}{sep}{sep}{real_cmds}",
        "only_sep": sep,
    }
    for label, value in cases.items():
        if marker.exists():
            marker.unlink()
        monkeypatch.setenv("MYAPP_CMDS_PATH", value)
        env = Env("myapp", autoload=False)
        resolved = _resolve_commands(None, None, None, env, None)
        assert not marker.exists(), f"{label}: evil.py was imported from the CWD"
        names = {
            getattr(c, "_parsername_", None) or getattr(c, "__name__", None)
            for c in resolved
        }
        assert "evil" not in names, f"{label}: evil registered as a command"


def test_cmds_path_explicit_dot_segment_is_still_honored(tmp_path, monkeypatch):
    """An explicit '.' segment IS still the current directory (unlike an
    empty one -- see test_cmds_path_empty_segment_never_imports_cwd)."""
    _write(tmp_path, "deploy.py", _CLASS_CMD_DEPLOY)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MYAPP_CMDS_PATH", ".")
    env = Env("myapp", autoload=False)

    resolved = _resolve_commands(None, None, None, env, None)
    names = {getattr(c, "__name__", "") for c in resolved}
    assert "Deploy" in names


def test_cmds_path_stale_entry_is_skipped_not_fatal(tmp_path, monkeypatch, caplog):
    """A deleted/nonexistent CMDS_PATH directory is skipped with a WARNING;
    the other entries (and built-ins) still resolve."""
    good_dir = tmp_path / "good"
    good_dir.mkdir()
    _write(good_dir, "deploy.py", _CLASS_CMD_DEPLOY)
    missing_dir = tmp_path / "does-not-exist"

    monkeypatch.setenv(
        "MYAPP_CMDS_PATH", os.pathsep.join([str(good_dir), str(missing_dir)])
    )
    env = Env("myapp", autoload=False)

    with caplog.at_level("WARNING", logger="duho.runtime"):
        resolved = _resolve_commands(Root, None, None, env, None)

    names = {getattr(c, "__name__", "") for c in resolved}
    assert "Deploy" in names
    assert any("is not a directory" in rec.message for rec in caplog.records)


def test_cmds_path_tilde_is_expanded(tmp_path, monkeypatch):
    """A ``~``-prefixed CMDS_PATH entry is expanded to the home directory."""
    home = tmp_path / "home"
    cmds = home / "cmds"
    cmds.mkdir(parents=True)
    _write(cmds, "deploy.py", _CLASS_CMD_DEPLOY)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("MYAPP_CMDS_PATH", str(Path("~") / "cmds"))
    env = Env("myapp", autoload=False)

    resolved = _resolve_commands(None, None, None, env, None)
    names = {getattr(c, "__name__", "") for c in resolved}
    assert "Deploy" in names


# --------------------------------------------------------------------------
# A module Args that already subclasses the app root is used as-is
# --------------------------------------------------------------------------


def test_module_args_cls_already_subclassing_root_is_used_as_is():
    """When a module's own ``Args`` already subclasses the app root (the
    documented convention: ``class Args(MyAppRoot): ...``), `_module_args_cls`
    must return it AS-IS rather than wrapping it in a second synthesized
    mixin class."""

    class _FakeModuleCommand:
        pass

    class SubclassesRoot(Root):
        method: str

    fake_command = _FakeModuleCommand()
    fake_command.args_cls = SubclassesRoot

    result = _module_args_cls(fake_command, Root)
    assert result is SubclassesRoot


# --------------------------------------------------------------------------
# setup_logging=True installs a handler once, never stacks
# --------------------------------------------------------------------------


@pytest.fixture
def _clean_root_logger():
    root = logging.getLogger()
    saved = list(root.handlers)
    saved_level = root.level
    yield root
    root.handlers[:] = saved
    root.setLevel(saved_level)


def test_setup_logging_installs_handler_once(tmp_path, _clean_root_logger):
    """setup_logging=True installs a stderr handler; a second app() does not stack.

    A module command leaves the dispatched ``instance`` as the LoggingArgs root,
    so it exposes ``_set_loglevels_`` and the ``setup_logging`` path runs.
    """
    _write(tmp_path, "backup.py", _MODULE_MAIN)
    root = _clean_root_logger
    root.handlers[:] = []  # start from a clean slate

    app(Root, source=tmp_path, argv=["backup"], setup_logging=True)
    count_after_first = len(root.handlers)
    assert count_after_first >= 1

    app(Root, source=tmp_path, argv=["backup"], setup_logging=True)
    # The `if not root_logger.handlers` guard means the second run does not add
    # another handler.
    assert len(root.handlers) == count_after_first


# --------------------------------------------------------------------------
# A non-Cmd selected leaf raises NotImplementedError through app()'s own
# dispatch path (distinct from duho.main's root-level check)
# --------------------------------------------------------------------------


class _DataLeaf(duho.Args):
    """A data-only leaf (not a Cmd)."""

    y: int = 2
    ("--y",)


class _CmdParent(duho.Cmd):
    """A Cmd parent whose subcommand leaf is a plain data Args."""

    _parsername_ = "parent"
    _subcommands_ = [_DataLeaf]

    def __call__(self):  # pragma: no cover
        return 0


def test_non_cmd_leaf_raises_not_implemented():
    with pytest.raises(NotImplementedError, match="holds data but is not runnable"):
        app(
            Root,
            commands=[_CmdParent],
            argv=["parent", "_DataLeaf"],
            setup_logging=False,
        )


# --------------------------------------------------------------------------
# name=/description= overrides reach the parser
# --------------------------------------------------------------------------


def test_name_and_description_overrides_reach_parser(tmp_path, capsys):
    _write(tmp_path, "deploy.py", _CLASS_CMD_DEPLOY)
    with pytest.raises(SystemExit):
        app(
            Root,
            source=tmp_path,
            argv=["--help"],
            name="myprog",
            description="My custom description.",
            setup_logging=False,
        )
    out = capsys.readouterr().out
    assert "myprog" in out
    assert "My custom description." in out


# --------------------------------------------------------------------------
# register 3-arg logger fallback when the root has no _logger_
# --------------------------------------------------------------------------


def test_register_3arg_logger_fallback_to_duho(tmp_path):
    """A 3-arg register on a non-LoggingArgs root gets the fallback 'duho' logger."""
    _write(tmp_path, "reg3.py", _MODULE_CMD_REGISTER_3ARG)
    rc = app(
        PlainRoot, source=tmp_path, argv=["reg3", "--flag", "v"], setup_logging=False
    )
    assert rc == 0
    discovered = [
        m
        for name, m in sys.modules.items()
        if name.startswith("duho._discovered.") and name.endswith("reg3")
    ][0]
    assert discovered.SEEN["logger_is_logger"] is True
    # PlainRoot has no `_logger_`, so app falls back to the module 'duho' logger.
    assert discovered.SEEN["logger_name"] == "duho"


# --------------------------------------------------------------------------
# A register hook sees real parsed globals even when the root already has
# its own _subcommands_ (a real subparsers action already exists by the time
# the advisory prepass runs)
# --------------------------------------------------------------------------

_MODULE_REG_READS_GLOBAL = '''\
"""A module command whose register reads a root global."""

SEEN = {}


def register(parser, args):
    SEEN["args"] = args
    SEEN["region"] = getattr(args, "region", "<missing>")


def main(args):
    return 0
'''


class RootWithRegionAndBuiltins(duho.Cmd):
    """A root with a real subparsers action already attached, plus a global."""

    region: str = "us"
    "Which region"
    ("--region",)

    _subcommands_ = [_Hello]

    def __call__(self):  # pragma: no cover
        return 0


def test_register_hook_sees_real_globals_when_root_has_builtin_subcommands(tmp_path):
    """The advisory prepass used to always fail with `KeyError('#cls')` when
    the root already has `_subcommands_` -- silently swallowed at DEBUG, so
    every module `register` hook got `args=None` instead of the parsed
    globals."""
    _write(tmp_path, "region_probe.py", _MODULE_REG_READS_GLOBAL)
    rc = app(
        RootWithRegionAndBuiltins,
        source=tmp_path,
        argv=["--region", "eu", "region-probe"],
        setup_logging=False,
    )
    assert rc == 0
    discovered = [
        m
        for name, m in sys.modules.items()
        if name.startswith("duho._discovered.")
        and name.endswith("region_probe")
        and hasattr(m, "SEEN")
    ][0]
    assert discovered.SEEN["args"] is not None
    assert discovered.SEEN["region"] == "eu"


# --------------------------------------------------------------------------
# The advisory prepass must never itself print/exit for real
# --------------------------------------------------------------------------

_MODULE_MAIN = '''\
"""A module command."""


def main(args):
    return 0
'''


class VersionedRoot(duho.Cli):
    """A Cli root with --version and --print-completion, and no builtins."""

    _version_ = "9.9.9"
    _completion_ = True

    def __call__(self):  # pragma: no cover
        return 0


def test_version_prints_once_with_a_module_command_present(tmp_path, capsys):
    """`--version` used to print twice through `duho.app` whenever a module
    command triggers the advisory prepass -- the prepass's OWN
    `_VersionAction` printed for real (only `-h`/`--help` was silenced), then
    the real parse printed again."""
    _write(tmp_path, "hello.py", _MODULE_MAIN)
    with pytest.raises(SystemExit) as excinfo:
        app(
            VersionedRoot,
            source=tmp_path,
            argv=["--version"],
            setup_logging=False,
        )
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert out.count("9.9.9") == 1


def test_print_completion_emits_exactly_one_script(tmp_path, capsys):
    """`--print-completion` used to run for real during the advisory prepass
    too (before any subcommand was registered), then again on the real
    parse -- writing two concatenated scripts, the first incomplete."""
    _write(tmp_path, "hello.py", _MODULE_MAIN)
    with pytest.raises(SystemExit) as excinfo:
        app(
            VersionedRoot,
            source=tmp_path,
            argv=["--print-completion", "bash"],
            setup_logging=False,
        )
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert out.count("# bash completion for") == 1
    assert "hello" in out  # the ONE script includes the discovered subcommand


@pytest.mark.requires_toml
def test_non_dict_subcommand_config_table_tolerated(tmp_path):
    """A `[subcommand]` config entry that is a scalar (not a table) is ignored."""
    _write(tmp_path, "deploy.py", _CLASS_CMD_DEPLOY)
    config = tmp_path / "app.toml"
    # `Deploy` maps to a scalar, not a table -> the sub-table branch coerces to {}.
    config.write_text('Deploy = "not-a-table"\n')
    rc = app(
        Root,
        source=tmp_path,
        argv=["Deploy", "--name", "z"],
        config=config,
        setup_logging=False,
    )
    assert rc == "deployed z"


class RequiredRoot(duho.LoggingArgs, duho.Cmd):
    """A root with a required global (no default)."""

    token: int
    "A required typed global"
    ("--token",)

    def __call__(self):  # pragma: no cover
        return 0


def test_prepass_systemexit_is_swallowed_real_parse_reports(tmp_path, capsys):
    """A bad required global with a module command present: the advisory
    prepass hits SystemExit (swallowed, silently), and the real parse reports
    the error exactly once, authoritatively.

    Asserting only `pytest.raises(SystemExit)` cannot tell the fixed code
    from the bug -- an UNSWALLOWED prepass error also raises SystemExit here,
    just with the wrong (or doubled) message. Pinning `exc.value.code == 2`
    and that the error text appears ONCE closes that gap.
    """
    _write(tmp_path, "backup.py", _MODULE_MAIN)
    # --token given a non-int: prerun_parse's quiet parse errors (silently)
    # and is swallowed; the authoritative parse below then exits 2, reporting
    # the error exactly once.
    with pytest.raises(SystemExit) as excinfo:
        app(
            RequiredRoot,
            source=tmp_path,
            argv=["--token", "notanint", "backup"],
            setup_logging=False,
        )
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert err.count("invalid int value") == 1


def test_prepass_does_not_preempt_subcommand_help(tmp_path, capsys):
    """`<module-cmd> --help` with a required root global must show the
    SUBCOMMAND's help, not a spurious "arguments are required" error from the
    advisory prepass (which used to run for real, print its own error, and
    only THEN let the real parse show help)."""
    _write(tmp_path, "backup.py", _MODULE_MAIN)
    with pytest.raises(SystemExit) as excinfo:
        app(
            RequiredRoot,
            source=tmp_path,
            argv=["backup", "--help"],
            setup_logging=False,
        )
    assert excinfo.value.code == 0
    out, err = capsys.readouterr()
    assert "arguments are required" not in err
    assert err == ""
    assert "backup" in out
