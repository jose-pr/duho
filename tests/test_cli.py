"""Tests for ``duho.Cli``: the application-root layer over ``Cmd``.

``Cli`` is an opt-in mixin a *root* command subclasses to expose the app-wide,
sandwich-named config attributes a plain ``Cmd`` does not declare (``_version_``,
``_completion_``, ``_config_``, ``_subcommands_``, ``_distribution_``), plus
self-registration (``_register_subcmd_`` / ``@subcommand``) so leaf command
files can attach themselves to the root's subcommand tree.

Covers:

* the documented distinction (a ``Cli`` declares the app-root attrs; a plain
  ``Cmd`` does not);
* ``--version`` / ``--print-completion`` work on a ``Cli`` root (proving it
  inherits/exposes the shipped behavior cleanly);
* self-registration -- decorator + classmethod both attach, dedup, per-class
  isolation (two ``Cli`` subclasses don't cross-contaminate), and union with a
  statically declared ``_subcommands_`` (a child in both appears once);
* ``class App(LoggingArgs, Cli)`` MRO (verbosity + ``__call__`` + app-root attrs
  all resolve);
* Phase-2 env/config-file thread-down through ``app()`` (real TOML + real ``.py``
  command fixtures under ``tmp_path`` -- never ``python -c``).
"""

import sys

import pytest

import duho
from duho.args import Cli, Cmd
from duho.env import Env
from duho.runtime import app


# --------------------------------------------------------------------------
# Fixture-file helpers (real .py files -- never -c: AST-derived flags need a file)
# --------------------------------------------------------------------------

_CLASS_CMD_DEPLOY = '''\
"""Deploy to a region."""
from duho import Cmd


class Deploy(Cmd):
    """Deploy to a region."""

    region: str = "local"
    "Target region"
    ("--region",)

    replicas: int = 1
    "Replica count"
    ("--replicas",)

    def __call__(self):
        # Report the config-applied fields and whether env threaded through.
        return "region=%s replicas=%s env=%s" % (
            self.region,
            self.replicas,
            self._env_ is not None,
        )
'''


def _write(dir_path, name, source):
    path = dir_path / name
    path.write_text(source)
    return path


@pytest.fixture(autouse=True)
def _clean_discovered_modules():
    """Drop synthesized discovery modules between tests so fixtures re-import."""
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        if name.startswith("duho._discovered."):
            sys.modules.pop(name, None)


# --------------------------------------------------------------------------
# App-root attribute distinction
# --------------------------------------------------------------------------


def test_cli_declares_app_root_attrs_a_plain_cmd_does_not():
    """A leaf ``Cmd`` doesn't declare the app-root attrs; a ``Cli`` does."""

    class PlainCmd(Cmd):
        def __call__(self):
            return 0

    for attr in ("_version_", "_completion_", "_config_", "_distribution_"):
        assert attr not in vars(PlainCmd), attr
        # But they resolve (with defaults) on Cli.
        assert hasattr(Cli, attr), attr

    # Defaults documented on Cli.
    assert Cli._version_ is None
    assert Cli._completion_ is False
    assert Cli._config_ is None
    assert Cli._distribution_ is None
    assert Cli._subcommands_ is None


def test_cli_app_root_attrs_are_settable_on_a_subclass():
    class App(Cli):
        _version_ = "9.9.9"
        _completion_ = True

    assert App._version_ == "9.9.9"
    assert App._completion_ is True
    # Inherited defaults remain for the untouched attrs.
    assert App._config_ is None


# --------------------------------------------------------------------------
# --version / --print-completion on a Cli root
# --------------------------------------------------------------------------


def test_cli_version_flag(capsys):
    class App(Cli):
        _version_ = "1.2.3"

        def __call__(self):
            return 0

    parser = App._parser_()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "1.2.3" in out


def test_cli_print_completion_flag(capsys):
    class App(Cli):
        _completion_ = True

        def __call__(self):
            return 0

    parser = App._parser_()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--print-completion", "bash"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    # A bash completion script mentions the shell builtin `complete`.
    assert "complete" in out


# --------------------------------------------------------------------------
# Self-registration: decorator + classmethod, dedup, isolation, union
# --------------------------------------------------------------------------


def test_subcommand_decorator_registers_child():
    class App(Cli):
        pass

    @App.subcommand
    class Foo(Cmd):
        def __call__(self):
            return 0

    # The decorator returns the class unchanged.
    assert Foo.__name__ == "Foo"
    assert App._subcommands_ == [Foo]
    # It landed in App's OWN list, not an inherited one.
    assert "_subcommands_" in vars(App)


def test_register_subcmd_classmethod_registers_and_dedups():
    class App(Cli):
        pass

    class Foo(Cmd):
        def __call__(self):
            return 0

    assert App._register_subcmd_(Foo) is Foo
    assert App._subcommands_ == [Foo]
    # Registering again is a no-op (dedup).
    App._register_subcmd_(Foo)
    assert App._subcommands_ == [Foo]


def test_self_registered_child_appears_in_parser_tree():
    class App(Cli):
        pass

    @App.subcommand
    class Foo(Cmd):
        def __call__(self):
            return 0

    parser = App._parser_()
    import argparse

    sub_action = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    assert "Foo" in sub_action.choices


def test_per_class_isolation_no_cross_contamination():
    class App1(Cli):
        pass

    class App2(Cli):
        pass

    @App1.subcommand
    class Foo(Cmd):
        def __call__(self):
            return 0

    @App2.subcommand
    class Bar(Cmd):
        def __call__(self):
            return 0

    assert App1._subcommands_ == [Foo]
    assert App2._subcommands_ == [Bar]
    # Neither leaked into the other, nor onto the Cli base.
    assert Cli._subcommands_ is None


def test_union_with_static_subcommands_dedups():
    class Foo(Cmd):
        def __call__(self):
            return 0

    class Bar(Cmd):
        def __call__(self):
            return 0

    class App(Cli):
        _subcommands_ = [Foo]

    # Copy-on-write: registering seeds from the inherited/declared list without
    # mutating it, and a child already present stays single.
    App._register_subcmd_(Foo)
    assert App._subcommands_ == [Foo]
    App._register_subcmd_(Bar)
    assert App._subcommands_ == [Foo, Bar]


def test_register_does_not_mutate_a_parent_classes_list():
    """A subclass registering a child must not mutate the parent's list."""

    class Base(Cli):
        pass

    class Child(Base):
        pass

    class Foo(Cmd):
        def __call__(self):
            return 0

    class Bar(Cmd):
        def __call__(self):
            return 0

    Base._register_subcmd_(Foo)
    assert Base._subcommands_ == [Foo]
    # Child inherits Foo; registering Bar on Child copies-on-write.
    Child._register_subcmd_(Bar)
    assert Child._subcommands_ == [Foo, Bar]
    # Parent is untouched.
    assert Base._subcommands_ == [Foo]


# --------------------------------------------------------------------------
# MRO: class App(LoggingArgs, Cli)
# --------------------------------------------------------------------------


def test_logging_args_cli_mro_resolves_all_members():
    class App(duho.LoggingArgs, Cli):
        _version_ = "2.0.0"

        def __call__(self):
            return 0

    # App-root attr from Cli.
    assert App._version_ == "2.0.0"
    # Verbosity machinery from LoggingArgs.
    assert hasattr(App, "_set_loglevels_")
    assert hasattr(App, "_verbose_loglevel_")
    # __call__ from the subclass (executable contract from Cmd -> Cli).
    parser = App._parser_()
    inst = parser.parse_args(["-v"])
    assert inst.verbose == 1
    assert inst() == 0


# --------------------------------------------------------------------------
# env/config-file thread-down through app()
# --------------------------------------------------------------------------


def test_app_threads_config_and_env_to_subcommand(tmp_path):
    """A Cli root's ``_config_`` applies to a discovered subcommand's fields,
    and the resolved ``Env`` reaches the dispatched command via ``_env_``."""
    cmds = tmp_path / "cmds"
    cmds.mkdir()
    _write(cmds, "deploy.py", _CLASS_CMD_DEPLOY)
    (tmp_path / "app.toml").write_text(
        "[Deploy]\nregion = \"eu-west\"\nreplicas = 5\n"
    )

    class MyApp(Cli):
        _config_ = str(tmp_path / "app.toml")

    env = Env("myapp", DEBUG="1")
    rc = app(
        MyApp,
        source=str(cmds),
        argv=["Deploy"],
        env=env,
        setup_logging=False,
    )
    # Config values applied (region/replicas from TOML), env threaded (_env_).
    assert rc == "region=eu-west replicas=5 env=True"


def test_app_config_kwarg_overrides_cli_config_attr(tmp_path):
    """An explicit ``config=`` to app() overrides the root's ``_config_``."""
    cmds = tmp_path / "cmds"
    cmds.mkdir()
    _write(cmds, "deploy.py", _CLASS_CMD_DEPLOY)
    (tmp_path / "override.toml").write_text("[Deploy]\nregion = \"us-east\"\n")

    class MyApp(Cli):
        _config_ = None

        def __call__(self):
            return 0

    rc = app(
        MyApp,
        source=str(cmds),
        argv=["Deploy"],
        config=str(tmp_path / "override.toml"),
        setup_logging=False,
    )
    assert rc == "region=us-east replicas=1 env=False"


def test_app_cli_dispatches_two_self_registered_command_files(tmp_path):
    """End-to-end: a Cli root with commands discovered from a dir dispatches
    each; CLI overrides still win over config."""
    cmds = tmp_path / "cmds"
    cmds.mkdir()
    _write(cmds, "deploy.py", _CLASS_CMD_DEPLOY)
    (tmp_path / "app.toml").write_text("[Deploy]\nregion = \"cfg\"\n")

    class MyApp(Cli):
        _config_ = str(tmp_path / "app.toml")

    # CLI --region beats the config value (precedence CLI > config).
    rc = app(
        MyApp,
        source=str(cmds),
        argv=["Deploy", "--region", "cli"],
        setup_logging=False,
    )
    assert rc == "region=cli replicas=1 env=False"
