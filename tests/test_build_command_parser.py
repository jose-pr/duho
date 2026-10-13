"""``duho.runtime.build_command_parser`` builds what ``app`` would for one command."""

import argparse

import pytest

import duho
from duho.discovery import discover_commands
from duho.runtime import build_command_parser


class Deploy(duho.Cmd):
    """Deploy."""

    target: str = "prod"
    ("--target",)

    def __call__(self):
        return 0


class Root(duho.Cli):
    _parsername_ = "build-root"
    shout: bool = False
    ("--shout",)


def _module(tmp_path, body, name="mod"):
    (tmp_path / (name + ".py")).write_text(body)
    (command,) = discover_commands(tmp_path)
    return command


def test_a_class_command_parses_its_own_option():
    parser = build_command_parser(Deploy)
    assert isinstance(parser, argparse.ArgumentParser)
    assert parser.parse_known_args(["--target", "dev"])[0].target == "dev"


def test_a_class_command_inherits_the_roots_options():
    parser = build_command_parser(Deploy, Root)
    namespace, _ = parser.parse_known_args(["--shout", "--target", "dev"])
    assert namespace.shout is True and namespace.target == "dev"
    assert "--shout" not in build_command_parser(Deploy)._option_string_actions


MODULE = """\
\"\"\"A module command.\"\"\"

import duho


class Args(duho.Args):
    level: int = 1
    ("--level",)


seen = []


def register(parser, args):
    seen.append(args)
    parser.add_argument("--extra", default="x")


def main(args):
    return 0
"""


def test_a_module_command_parses_its_own_and_its_hook_options(tmp_path):
    command = _module(tmp_path, MODULE)
    parser = build_command_parser(command)
    namespace, _ = parser.parse_known_args(["--level", "4", "--extra", "y"])
    assert (namespace.level, namespace.extra) == (4, "y")
    assert command.module.seen == [None]


def test_a_module_command_inherits_the_roots_options(tmp_path):
    parser = build_command_parser(_module(tmp_path, MODULE), Root)
    assert parser.parse_known_args(["--shout"])[0].shout is True


def test_a_three_argument_hook_gets_a_logger(tmp_path):
    command = _module(
        tmp_path,
        "def register(parser, args, logger):\n"
        "    parser.set_defaults(logged=logger is not None)\n"
        "\n\ndef main(args):\n    return 0\n",
    )
    assert build_command_parser(command).parse_known_args([])[0].logged is True


def test_a_hook_that_raises_propagates(tmp_path):
    command = _module(
        tmp_path,
        "def register(parser, args):\n    raise RuntimeError('hook broke')\n"
        "\n\ndef main(args):\n    return 0\n",
    )
    with pytest.raises(RuntimeError, match="hook broke"):
        build_command_parser(command)


def test_a_hook_that_collides_with_a_global_propagates(tmp_path):
    command = _module(
        tmp_path,
        "def register(parser, args):\n    parser.add_argument('--shout')\n"
        "\n\ndef main(args):\n    return 0\n",
    )
    with pytest.raises(argparse.ArgumentError, match="collides"):
        build_command_parser(command, Root)


def test_a_command_whose_field_cannot_build_raises():
    class Broken(duho.Cmd):
        """Broken."""

        bad: "set[dict]" = None  # type: ignore[assignment]
        ("--bad",)

        def __call__(self):
            return 0

    with pytest.raises(Exception):
        build_command_parser(Broken)


def test_anything_else_is_a_type_error():
    with pytest.raises(TypeError, match="expected a Cmd subclass"):
        build_command_parser(object())


def test_it_is_public():
    import duho.runtime

    assert "build_command_parser" in duho.runtime.__all__
