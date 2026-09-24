"""Regression test: `_help_formatter_` must style the WHOLE
subcommand tree, not just the root's direct children.

Before this fix, `_parser_` only assigned the root's formatter to each
direct child, AFTER that child had already built its own (grandchild)
subtree using its own (unset) `_help_formatter_` -- so a depth-2+ nested
command kept argparse's plain formatter regardless of what the app root
declared.
"""

from duho import Cli, Cmd, DefaultsFormatter


class _Leaf(Cmd):
    """A leaf command."""

    n: int = 3
    "A number"
    ("--n",)

    def __call__(self):
        return 0


class _Mid(Cmd):
    """A middle command with its own subcommand."""

    _subcommands_ = [_Leaf]

    m: int = 7
    "A number"
    ("--m",)

    def __call__(self):
        return 0


class _Root(Cli):
    _help_formatter_ = DefaultsFormatter
    _subcommands_ = [_Mid]

    def __call__(self):
        return 0


def _formatter_of(parser) -> type:
    return parser.formatter_class


def test_grandchild_inherits_the_roots_help_formatter():
    parser = _Root._parser_()
    assert _formatter_of(parser) is DefaultsFormatter

    mid_parser = None
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if choices and "_Mid" in choices:
            mid_parser = choices["_Mid"]
    assert mid_parser is not None
    assert _formatter_of(mid_parser) is DefaultsFormatter

    leaf_parser = None
    for action in mid_parser._actions:
        choices = getattr(action, "choices", None)
        if choices and "_Leaf" in choices:
            leaf_parser = choices["_Leaf"]
    assert leaf_parser is not None
    assert _formatter_of(leaf_parser) is DefaultsFormatter, (
        "grandchild must inherit the root's _help_formatter_, not argparse's "
        "plain default"
    )


def test_grandchild_help_shows_defaults():
    parser = _Root._parser_()
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if choices and "_Mid" in choices:
            mid_parser = choices["_Mid"]
    for action in mid_parser._actions:
        choices = getattr(action, "choices", None)
        if choices and "_Leaf" in choices:
            leaf_parser = choices["_Leaf"]
    text = leaf_parser.format_help()
    assert "(default: 3)" in text


def test_a_middle_command_with_its_own_formatter_overrides_for_its_subtree():
    import duho.formatters as formatters

    class MidWithOwn(Cmd):
        _help_formatter_ = formatters.ColorHelpFormatter
        _subcommands_ = [_Leaf]

        def __call__(self):
            return 0

    class RootPlain(Cli):
        _help_formatter_ = DefaultsFormatter
        _subcommands_ = [MidWithOwn]

        def __call__(self):
            return 0

    parser = RootPlain._parser_()
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if choices and "MidWithOwn" in choices:
            mid_parser = choices["MidWithOwn"]
    assert _formatter_of(mid_parser) is formatters.ColorHelpFormatter

    for action in mid_parser._actions:
        choices = getattr(action, "choices", None)
        if choices and "_Leaf" in choices:
            leaf_parser = choices["_Leaf"]
    assert _formatter_of(leaf_parser) is formatters.ColorHelpFormatter, (
        "a middle command's OWN _help_formatter_ must win for its subtree, "
        "not the root's"
    )
