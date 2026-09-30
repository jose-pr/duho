"""Regression tests for three independent subcommand/parser-build edge cases:

* A subcommand that subclasses its own root (to share global options)
  must not recurse forever building itself as its own child.
* The documented "manual subparsers" recipe (attaching a duho
  subparser to a plain, non-duho argparse root) must produce a way to get the
  real command instance back, not a raw Namespace carrying the internal
  "#cls" marker.
* `duho.parser(cls, prog=...)` must not crash with a `prog`/positional
  collision.
"""

import argparse

import pytest

import duho
from duho import Cli, Cmd


def test_subcommand_subclassing_its_root_does_not_recurse_forever():
    class App(Cli):
        verbose: bool = False
        ("-v", "--verbose")

        def __call__(self):
            return 0

    @App.subcommand
    class Build(App):
        def __call__(self):
            return 1

    # Previously: RecursionError (Build inherits App._subcommands_, which
    # contains Build itself, so building it recurses without end).
    rc = duho.main(App, ["build"], setup_logging=False)
    assert rc == 1


def test_direct_self_reference_raises_a_clear_error_instead_of_recursing():
    class Cyclic(Cmd):
        def __call__(self):
            return 0

    Cyclic._subcommands_ = [Cyclic]  # pathological: a class listing itself

    with pytest.raises(TypeError, match="Cyclic"):
        Cyclic._parser_()


def test_manual_subparser_recipe_can_finish_the_parse():
    """The README's own documented recipe: attach a duho command's
    `_parser_(subparsers, ...)` to a plain, hand-built argparse root."""

    class Serve(Cmd):
        port: int = 8000
        ("--port",)

        def __call__(self):
            return self.port

    root = argparse.ArgumentParser()
    subparsers = root.add_subparsers()
    Serve._parser_(subparsers, name="serve")

    namespace = root.parse_args(["serve", "--port", "9"])
    assert "#cls" in vars(namespace)  # the raw shape before finishing

    instance = duho.finish_parse(namespace)
    assert isinstance(instance, Serve)
    assert instance.port == 9
    assert instance() == 9
    assert "#cls" not in vars(instance)


def test_finish_parse_raises_when_no_command_was_selected():
    plain = argparse.Namespace(x=1)
    with pytest.raises(ValueError, match="no duho command was selected"):
        duho.finish_parse(plain)


def test_finish_parse_strips_collection_action_sidecars():
    """The internal `parse_known_args` patch strips the `_CollectionAction`/
    `UpdateAction` sidecars (`_duho_items_<dest>`/`_duho_dict_seen_<dest>`)
    before constructing an instance -- the manual `finish_parse` recipe
    builds the instance itself, so it must do the same cleanup, or this
    bookkeeping leaks into the constructed instance's own `vars()`."""

    class Serve(Cmd):
        tags: "list[str]" = []
        opts: "dict[str, str]" = {}

        def __call__(self):
            return 0

    root = argparse.ArgumentParser()
    subparsers = root.add_subparsers()
    Serve._parser_(subparsers, name="serve")

    namespace = root.parse_args(["serve", "--tags", "a", "--opts", "k=v"])
    instance = duho.finish_parse(namespace)
    assert isinstance(instance, Serve)
    assert instance.tags == ["a"]
    assert instance.opts == {"k": "v"}
    leaked = [k for k in vars(instance) if k.startswith("_duho_")]
    assert leaked == []


def test_duho_parser_accepts_an_explicit_prog_override():
    class App(Cli):
        def __call__(self):
            return 0

    parser = duho.parser(App, prog="myapp")
    assert parser.prog == "myapp"


def test_duho_parse_accepts_prog_via_parser_kwargs():
    class App2(Cli):
        def __call__(self):
            return 0

    result = duho.parse(App2, [], parser_kwargs={"prog": "myapp2"})
    assert type(result)._duho_last_parser_.prog == "myapp2"


# --------------------------------------------------------------------------
# The subcommand selector's PRIVATE dest (`_duho_command_`, chosen so a root
# field literally named `command` is never clobbered) must never leak into
# user-facing "required"/"invalid choice" error text -- only the usage
# synopsis defaulted to a clean `{...}` from `choices`; the ERROR messages
# fall back to the action's DEST when no `metavar` is set.
# --------------------------------------------------------------------------


def test_missing_subcommand_error_does_not_leak_the_private_dest(capsys):
    class Deploy(Cmd):
        def __call__(self):
            return 0

    class Root(Cli):
        _subcommands_ = [Deploy]

        def __call__(self):
            return 0

    with pytest.raises(SystemExit):
        duho.main(Root, [], setup_logging=False)
    err = capsys.readouterr().err
    assert "_duho_command_" not in err
    assert "required: {deploy}" in err


def test_invalid_subcommand_choice_error_does_not_leak_the_private_dest(capsys):
    class Deploy(Cmd):
        def __call__(self):
            return 0

    class Root(Cli):
        _subcommands_ = [Deploy]

        def __call__(self):
            return 0

    with pytest.raises(SystemExit):
        duho.main(Root, ["zz"], setup_logging=False)
    err = capsys.readouterr().err
    assert "_duho_command_" not in err
    assert "argument {deploy}: invalid choice: 'zz'" in err
