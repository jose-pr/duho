"""``LoggingArgs._base_loglevel_``: the level ``-v``/``-q`` step from."""

import logging
import typing

import pytest

import duho
from duho import Cli, Cmd, LoggingArgs


def _levels():
    from duho import logging as duho_logging

    return list(duho_logging.VERBOSE_LEVELS)


def _step(base, verbose=0, quiet=0):
    levels = _levels()
    index = max(0, min(levels.index(base) + verbose - quiet, len(levels) - 1))
    return levels[index]


class _Leaf(Cmd):
    """A plain leaf: no LoggingArgs of its own."""

    def __call__(self):
        return 0


class _WarnRoot(LoggingArgs, Cli):
    _parsername_ = "base-warn-root"
    _base_loglevel_ = logging.WARNING
    _subcommands_ = [_Leaf]

    def __call__(self):
        return 0


class _NameRoot(LoggingArgs, Cli):
    _parsername_ = "base-name-root"
    _base_loglevel_ = "ERROR"
    _subcommands_ = [_Leaf]

    def __call__(self):
        return 0


class _BareRoot(LoggingArgs, Cmd):
    _parsername_ = "base-bare-root"
    _base_loglevel_ = logging.WARNING

    def __call__(self):
        return 0


@pytest.fixture
def clean_logging():
    root = logging.getLogger()
    saved, saved_level = list(root.handlers), root.level
    root.handlers[:] = []
    yield
    root.handlers[:] = saved
    root.setLevel(saved_level)
    for name in (
        "base-warn-root",
        "base-name-root",
        "base-default-root",
        "base-bare-root",
    ):
        logging.getLogger(name).setLevel(logging.NOTSET)


def test_default_base_is_info():
    assert LoggingArgs._base_loglevel_ == logging.INFO


@pytest.mark.parametrize(
    "verbose, quiet", [(0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (0, 3)]
)
def test_method_steps_from_the_class_base(verbose, quiet):
    instance = _WarnRoot()
    instance.verbose, instance.quiet = verbose, quiet
    assert instance._verbose_loglevel_() == _step(logging.WARNING, verbose, quiet)


def test_base_may_be_a_level_name():
    instance = _NameRoot()
    instance.verbose, instance.quiet = 1, 0
    assert instance._verbose_loglevel_() == _step(logging.ERROR, 1, 0)


def test_unregistered_base_is_a_value_error():
    class Odd(LoggingArgs, Cmd):
        _base_loglevel_ = 7

        def __call__(self):
            return 0

    instance = Odd()
    instance.verbose = instance.quiet = 0
    with pytest.raises(ValueError, match="7"):
        instance._verbose_loglevel_()


def test_main_applies_the_base_on_the_root(clean_logging):
    duho.main(_BareRoot, ["-v"])
    expected = _step(logging.WARNING, 1, 0)
    assert logging.getLogger("base-bare-root").getEffectiveLevel() == expected


def test_plain_leaf_under_the_root_uses_the_root_base(clean_logging):
    duho.main(_WarnRoot, ["-v", "leaf"])
    expected = _step(logging.WARNING, 1, 0)
    assert logging.getLogger("base-warn-root").getEffectiveLevel() == expected


def test_plain_leaf_without_flags_sits_at_the_base(clean_logging):
    duho.main(_NameRoot, ["leaf"])
    assert logging.getLogger("base-name-root").getEffectiveLevel() == logging.ERROR


def test_app_plain_leaf_uses_the_root_base(clean_logging):
    duho.app(_WarnRoot, commands=[_Leaf], argv=["-q", "leaf"])
    expected = _step(logging.WARNING, 0, 1)
    assert logging.getLogger("base-warn-root").getEffectiveLevel() == expected


def test_default_root_is_unchanged(clean_logging):
    class Plain(LoggingArgs, Cli):
        _parsername_ = "base-default-root"
        _subcommands_ = [_Leaf]

        def __call__(self):
            return 0

    duho.main(Plain, ["-v", "leaf"])
    assert logging.getLogger("base-default-root").getEffectiveLevel() == _step(
        logging.INFO, 1, 0
    )


def test_base_is_not_a_command_line_option():
    parser = _WarnRoot._parser_()
    flags = {flag for action in parser._actions for flag in action.option_strings}
    assert not [f for f in flags if "base" in f]


def test_annotation_resolves():
    hints = typing.get_type_hints(LoggingArgs)
    assert hints["_base_loglevel_"] == typing.Union[int, str]
