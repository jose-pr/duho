"""An option's attached ``--`` value keeps its value on every Python version.

argparse before 3.13 strips a bare ``--`` from an option's values, so
``--k=--`` and ``-k--`` would otherwise parse to an empty value. Fixtures are
module-level classes (introspection reads this file's source).
"""

import typing as _t

import pytest

import duho
from duho import NS, Arg, Cli, Cmd, LoggingArgs

from duho.args import _keep_attached_double_dash


def _tag(text: str) -> str:
    return "T(" + text + ")"


class Opts(Cmd):
    """Options that can receive an attached double dash."""

    prefix: str = "none"
    "A scalar option"
    ("--prefix",)

    tags: "_t.List[str]" = []
    "A list option"
    ("--tags",)

    short: str = "none"
    "A short-only option"
    ("-s",)

    tagged: "Arg[str, NS(type=_tag)]" = "none"
    "A converted option"
    ("--tagged",)

    mode: "_t.Literal['--', 'x']" = "x"
    "A choice that includes the double dash"
    ("--mode",)

    strict: "_t.Literal['x', 'y']" = "x"
    "A choice that excludes the double dash"
    ("--strict",)

    rest: "_t.List[str]" = []
    "Positional values"
    ("rest",)

    def __call__(self):  # pragma: no cover - parsed only
        return 0


class Child(Cmd):
    """A subcommand with its own option."""

    prefix: str = "none"
    "A scalar option"
    ("--prefix",)

    def __call__(self):  # pragma: no cover - parsed only
        return 0


class Tree(LoggingArgs, Cli):
    """A root with a subcommand."""

    _subcommands_ = [Child]


def test_scalar_attached_value():
    assert duho.parse(Opts, ["--prefix=--"]).prefix == "--"


def test_list_attached_value():
    assert duho.parse(Opts, ["--tags=--", "--tags=y"]).tags == ["--", "y"]


def test_short_attached_value():
    assert duho.parse(Opts, ["-s--"]).short == "--"


def test_type_converter_receives_the_value():
    assert duho.parse(Opts, ["--tagged=--"]).tagged == "T(--)"


def test_choice_including_double_dash_is_accepted():
    assert duho.parse(Opts, ["--mode=--"]).mode == "--"


def test_choice_excluding_double_dash_is_rejected():
    with pytest.raises(SystemExit) as exc:
        duho.parse(Opts, ["--strict=--"])
    assert exc.value.code == 2


def test_subcommand_option():
    parsed = duho.parse(Tree, ["child", "--prefix=--"])
    assert isinstance(parsed, Child)
    assert parsed.prefix == "--"


_MODULE_WITH_ARGS = '''\
"""A module command that declares an option."""
from duho import Arg, NS


class Args:
    prefix: str = "none"
    "A scalar option"
    ("--prefix",)


def main(args):
    print("prefix=" + args.prefix)
    return 0
'''

_MODULE_WITH_REGISTER = '''\
"""A module command whose register hook adds an option."""


def register(parser, args):
    parser.add_argument("--k", default="none")


def main(args):
    print("k=" + args.k)
    return 0
'''


def _run(tmp_path, name, source, argv, capsys):
    (tmp_path / (name + ".py")).write_text(source)
    rc = duho.app(LoggingArgs, source=tmp_path, argv=[name] + argv, setup_logging=False)
    assert rc == 0
    return capsys.readouterr().out


def test_module_command_declared_option(tmp_path, capsys):
    out = _run(tmp_path, "modargs", _MODULE_WITH_ARGS, ["--prefix=--"], capsys)
    assert "prefix=--" in out


def test_module_command_register_hook_option(tmp_path, capsys):
    out = _run(tmp_path, "modreg", _MODULE_WITH_REGISTER, ["--k=--"], capsys)
    assert "k=--" in out


def test_attached_value_beside_a_real_separator():
    parsed = duho.parse(Opts, ["--prefix=--", "--", "x"])
    assert parsed.prefix == "--"
    assert parsed._passthrough_ == ["x"]


def test_detached_double_dash_is_still_an_error():
    with pytest.raises(SystemExit) as exc:
        duho.parse(Opts, ["--prefix", "--"])
    assert exc.value.code == 2


def test_helper_is_idempotent():
    parser = Opts._parser_()
    first = parser._get_values
    _keep_attached_double_dash(parser)
    assert parser._get_values is first
