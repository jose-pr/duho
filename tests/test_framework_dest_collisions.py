"""Regression tests: a user field must never be silently dropped because its
name happens to match a dest the framework itself uses internally.

Before this fix, ``-h``/``--help``, ``--version``, and ``--print-completion``
all install argparse actions whose ``dest`` equals a plain english word
(``help``/``version``/``print_completion``) BEFORE the declarative field
loop runs; that loop skipped ANY field whose name already had an action
(a check meant only for a ``parents=[...]`` merge), so a user field named
``help``/``version``/``print_completion`` vanished from the CLI with no
error at all -- not even a warning. Separately, ``duho.app()``'s dynamic
subcommand dispatch and a static ``_subcommands_`` tree both used the same
plain ``command`` dest, which a nested subcommand tree OR a root field named
``command`` could silently clobber.
"""

import pytest

import duho
from duho import Args, Cli, Cmd


def test_field_named_help_raises_a_clear_build_time_error():
    class WithHelpField(Args):
        help: str = "h"
        "Extended help text"
        ("--help-text",)

    with pytest.raises(ValueError, match="help"):
        WithHelpField._parser_()


def test_field_named_version_raises_when_version_flag_is_active():
    class WithVersionField(Args):
        _version_ = "1.0"

        version: str = "latest"
        "Release to install"
        ("--deploy-version",)

    with pytest.raises(ValueError, match="version"):
        WithVersionField._parser_()


def test_field_named_version_is_fine_when_no_version_flag_is_added():
    """Without `_version_` set, no --version action exists at all, so a field
    named `version` is perfectly ordinary -- no collision, no error."""

    class NoAutoVersion(Args):
        version: str = "1"
        "Release version"
        ("--version",)

    parser = NoAutoVersion._parser_()
    args = parser.parse_args(["--version", "3"])
    assert args.version == "3"


def test_field_named_print_completion_raises_when_completion_flag_is_active():
    class Comp(Cli):
        _completion_ = True

        print_completion: str = "n"
        ("--pc",)

        def __call__(self):
            return 0

    with pytest.raises(ValueError, match="print_completion"):
        Comp._parser_()


def test_field_sharing_a_parents_dest_is_still_silently_reused():
    """The skip is still correct -- and silent -- for its ORIGINAL purpose: a
    subcommand parser built with `parents=[...]` sharing a genuine global
    option."""

    class Root(Args):
        verbose: bool = False
        ("-v", "--verbose")

    base_parser = Root._parser_(add_help=False)

    class Sub(Args):
        verbose: bool = False
        ("-v", "--verbose")

    # Must not raise: "verbose" is a legitimate parents=-contributed dest.
    Sub._parser_(parents=[base_parser])


def test_root_field_named_command_is_not_clobbered_by_app_dispatch():
    """`app()`'s own subcommand-selection dest must never be the
    same plain name a root field could declare."""

    class Root(Cmd):
        command: str = "echo"
        ("--command",)

        def __call__(self):
            return 0

    class Backup(Cmd):
        def __call__(self):
            return 0

    seen = {}

    def dispatch(command, instance):
        seen["command_value"] = instance.command
        return 0

    duho.app(
        Root,
        commands=[Backup],
        argv=["--command", "custom", "Backup"],
        setup_logging=False,
        dispatch=dispatch,
    )
    assert seen["command_value"] == "custom"


def test_nested_subcommand_does_not_hijack_a_same_named_module_command(tmp_path):
    """A class subcommand named the same as a top-level MODULE command
    must not be dispatched as that module command."""
    cmds = tmp_path / "cmds"
    cmds.mkdir()
    (cmds / "list.py").write_text(
        "def main(args):\n" "    return 'TOP-LEVEL'\n",
        encoding="utf-8",
    )

    class RemoteList(Cmd):
        _parsername_ = "list"

        def __call__(self):
            return "NESTED"

    class Remote(Cmd):
        _parsername_ = "remote"
        _subcommands_ = [RemoteList]

        def __call__(self):
            return 0

    seen = {}

    def dispatch(command, instance):
        seen["type"] = type(instance).__name__
        return 0

    duho.app(
        commands=[Remote],
        source=cmds,
        argv=["remote", "list"],
        setup_logging=False,
        dispatch=dispatch,
    )
    assert seen["type"] == "RemoteList"
