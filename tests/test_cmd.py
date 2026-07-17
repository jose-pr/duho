"""Tests for the Plan-13 Args/Cmd split.

Covers: Cmd.main dispatch, __call__ -> main delegation, bare data Args is
not runnable (clear error), the command() builder, _passthrough_ capture
(with/without `--`, only the first `--` splits), and the LoggingArgs+Cmd
MRO / recommended base order.

AST-dependent behavior (flag tuples derived from `("--flag",)` literals) is
exercised both from this real module file and from a `tmp_path` fixture
module (never `python -c`, which has no retrievable source).
"""

import importlib.util
import sys
import textwrap

import pytest

import duho
from duho import Args, Cmd, LoggingArgs, command


# --- Cmd.main dispatch + __call__ delegation -------------------------------


class Greeter(Cmd):
    """A minimal runnable command."""

    name: str = "world"
    "Who to greet"
    ("--name",)

    def main(self):
        return f"hello {self.name}"


def test_cmd_main_runs_via_duho_main():
    """duho.main builds+parses a Cmd and dispatches its main()."""
    assert duho.main(Greeter, ["--name", "duho"], setup_logging=False) == "hello duho"


def test_cmd_call_delegates_to_main():
    """A Cmd instance is directly callable; __call__ delegates to main()."""
    inst = duho.parse(Greeter, ["--name", "x"])
    assert isinstance(inst, Cmd)
    assert inst() == "hello x"
    assert inst.main() == "hello x"


def test_cmd_none_return_maps_to_zero():
    """A main() returning None maps to exit code 0 through duho.main."""

    class NoneCmd(Cmd):
        def main(self):
            return None

    assert duho.main(NoneCmd, [], setup_logging=False) == 0


# --- bare data Args is not runnable ----------------------------------------


class DataOnly(Args):
    """A pure data Args: no main / no __call__."""

    value: int = 3
    "A value"
    ("--value",)


def test_bare_args_is_not_runnable():
    """Dispatching a data-only Args raises a clear NotImplementedError."""
    with pytest.raises(NotImplementedError) as excinfo:
        duho.main(DataOnly, [], setup_logging=False)
    msg = str(excinfo.value)
    assert "DataOnly" in msg
    assert "Cmd" in msg  # tells the user how to make it runnable


def test_bare_args_still_parses_as_data():
    """The split keeps Args usable as pure data via duho.parse."""
    inst = duho.parse(DataOnly, ["--value", "7"])
    assert inst.value == 7
    assert not callable(getattr(inst, "__call__", None)) or not isinstance(inst, Cmd)


# --- Cmd subclass that implements neither main nor __call__ -----------------


class ForgotMain(Cmd):
    """A Cmd that forgot to implement main()."""

    x: int = 1
    "A value"
    ("--x",)


def test_cmd_without_main_raises_naming_class():
    """Cmd.main's base stub raises NotImplementedError naming the class."""
    with pytest.raises(NotImplementedError, match="ForgotMain"):
        duho.main(ForgotMain, [], setup_logging=False)


# --- command() builder -----------------------------------------------------


class BuildMe(Args):
    """A data Args to be turned into a Cmd via command()."""

    n: int = 5
    "A number"
    ("--n",)


def test_command_builder_dispatches_and_returns():
    """command(Args, func) builds a Cmd whose main calls func(self)."""
    Built = command(BuildMe, lambda self: 0)
    assert issubclass(Built, Cmd)
    assert duho.main(Built, [], setup_logging=False) == 0


def test_command_builder_func_receives_parsed_instance():
    """func(self) gets the parsed instance (fields populated from argv)."""
    Built = command(BuildMe, lambda self: self.n * 2)
    assert duho.main(Built, ["--n", "10"], setup_logging=False) == 20


def test_command_builder_name_sets_parsername():
    """The optional name= sets the built class's _parsername_."""
    Built = command(BuildMe, lambda self: 0, name="do-it")
    assert Built._parsername_ == "do-it"


def test_command_builder_on_a_cmd_subclass_does_not_double_base():
    """command() on a class already deriving Cmd doesn't add Cmd twice."""

    class AlreadyCmd(Cmd):
        v: int = 0
        "v"
        ("--v",)

        def main(self):
            return -1

    Built = command(AlreadyCmd, lambda self: 42)
    # func wins over the inherited main (Built.main is the wrapper).
    assert duho.main(Built, [], setup_logging=False) == 42
    assert Built.__mro__.count(Cmd) == 1


# --- _passthrough_ capture --------------------------------------------------


class PassCmd(Cmd):
    """A command that inspects its passthrough argv."""

    flag: bool = False
    "A flag"
    ("--flag",)

    def main(self):
        return list(self._passthrough_)


def test_passthrough_captures_args_after_double_dash():
    """argv after the first `--` lands in _passthrough_."""
    inst = duho.parse(PassCmd, ["--flag", "--", "x", "y"])
    assert inst.flag is True
    assert inst._passthrough_ == ["x", "y"]


def test_passthrough_empty_without_double_dash():
    """No `--` yields an empty passthrough list."""
    inst = duho.parse(PassCmd, ["--flag"])
    assert inst._passthrough_ == []


def test_passthrough_only_first_double_dash_splits():
    """A second `--` is part of the passthrough payload, not a new split."""
    inst = duho.parse(PassCmd, ["--", "a", "--", "b"])
    assert inst._passthrough_ == ["a", "--", "b"]


def test_passthrough_available_through_duho_main():
    """A Cmd's main() can read _passthrough_ during dispatch."""
    rc = duho.main(PassCmd, ["--", "p", "q"], setup_logging=False)
    assert rc == ["p", "q"]


# --- LoggingArgs + Cmd MRO --------------------------------------------------


class LoggedCmd(LoggingArgs, Cmd):
    """Recommended base order: data mixin first, executable base last."""

    def main(self):
        # both logging (_logger_) and the run contract must resolve
        return self._logger_.name


def test_loggingargs_cmd_mro_resolves_logger_and_main():
    """(LoggingArgs, Cmd) parses verbosity AND runs main()."""
    inst = duho.parse(LoggedCmd, ["-v"])
    assert inst.verbose == 1
    assert isinstance(inst, Cmd)
    # main() reachable and returns the scoped logger name
    assert duho.main(LoggedCmd, [], setup_logging=False) == inst._logger_.name


def test_loggingargs_cmd_reverse_order_also_resolves():
    """(Cmd, LoggingArgs) also resolves both contracts (order-independent)."""

    class OtherOrder(Cmd, LoggingArgs):
        def main(self):
            return self.verbose

    inst = duho.parse(OtherOrder, ["-vv"])
    assert inst.verbose == 2
    assert duho.main(OtherOrder, ["-v"], setup_logging=False) == 1


# --- AST-derived flags from a real fixture file -----------------------------


def _load_module_from_source(tmp_path, name, source):
    path = tmp_path / f"{name}.py"
    path.write_text(textwrap.dedent(source))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        pass
    return module


def test_cmd_from_real_fixture_file_dispatches(tmp_path):
    """A Cmd defined in a real .py file gets AST-derived flags and dispatches."""
    mod = _load_module_from_source(
        tmp_path,
        "fixture_cmd",
        '''
        import duho
        from duho import Cmd


        class FileCmd(Cmd):
            """A command declared in a real file."""

            count: int = 1
            "How many"
            ("--count",)

            def main(self):
                return self.count + 100
        ''',
    )
    try:
        assert duho.main(mod.FileCmd, ["--count", "5"], setup_logging=False) == 105
    finally:
        sys.modules.pop("fixture_cmd", None)
