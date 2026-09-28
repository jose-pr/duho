"""Tests for duho.logging module."""

import argparse
import logging

import pytest

import duho
from duho import (
    add_logging_level,
    Cli,
    Cmd,
    DefaultFormatter,
    init_stderr_logging,
    LoggingArgs,
    parse_loglevels,
)


def test_add_logging_level():
    """Test adding custom log level."""
    add_logging_level("CUSTOM", 25)
    assert hasattr(logging, "CUSTOM")
    assert logging.CUSTOM == 25


def test_add_logging_level_to_logger():
    """Test custom level is callable on logger."""
    add_logging_level("TEST_LEVEL", 15)
    logger = logging.getLogger("test")
    # Should not raise
    logger.test_level("This is a test message")


def test_default_formatter_colors():
    """Test color mapping for log levels."""
    formatter = DefaultFormatter()
    assert logging.DEBUG in formatter.COLORS
    assert logging.INFO in formatter.COLORS
    assert logging.WARNING in formatter.COLORS
    assert logging.ERROR in formatter.COLORS
    assert logging.CRITICAL in formatter.COLORS


def test_init_stderr_logging():
    """Test stderr logging initialization."""
    logger = init_stderr_logging("test_logger", level=logging.DEBUG)
    assert logger.name == "test_logger"
    assert len(logger.handlers) > 0
    assert isinstance(logger.handlers[0], logging.StreamHandler)


def test_parse_loglevels_single():
    """Test parsing single log level."""
    levels = parse_loglevels("DEBUG")
    assert levels.get("") == logging.DEBUG


def test_parse_loglevels_module_specific():
    """Test parsing module-specific log levels."""
    levels = parse_loglevels("mymodule:INFO")
    assert levels.get("mymodule") == logging.INFO


def test_parse_loglevels_multiple():
    """Test parsing multiple log level specs."""
    levels = parse_loglevels("DEBUG,mymodule:WARNING")
    assert levels.get("") == logging.DEBUG
    assert levels.get("mymodule") == logging.WARNING


class MyCommand(LoggingArgs):
    """Command with logging."""

    name: str
    "Name to process"
    ("--name",)


def test_logging_args_integration():
    """Test LoggingArgs mixin."""
    parser = MyCommand._parser_()
    args = parser.parse_args(["--name", "test", "-v"])

    assert args.name == "test"
    assert args.verbose == 1

    # Should have logger property
    assert hasattr(args, "_logger_")
    assert isinstance(args._logger_, logging.Logger)


class VerboseCommand(LoggingArgs):
    """Test command for verbose logging."""

    pass


def test_verbose_to_loglevel():
    """Test converting verbose count to log level."""
    parser = VerboseCommand._parser_()
    levels = list(duho.logging.VERBOSE_LEVELS.keys())
    base = levels.index(logging.INFO)

    # No -v/-q: the base level is INFO.
    args = parser.parse_args([])
    level = args._verbose_loglevel_()
    assert level == logging.INFO

    # One -v steps exactly one entry MORE verbose than the base level.
    args = parser.parse_args(["-v"])
    level_verbose = args._verbose_loglevel_()
    assert level_verbose == levels[base + 1]
    assert level_verbose != level


class SimpleLoggingCommand(LoggingArgs):
    """Simple logging command."""

    pass


def test_logging_args_set_loglevels():
    """Test setting log levels from parsed args."""
    parser = SimpleLoggingCommand._parser_()
    args = parser.parse_args(["--loglevel", "DEBUG"])
    loglevels = args._set_loglevels_()

    # The bare "--loglevel DEBUG" applies to both the default ("") key and
    # this command's own logger, and is actually installed on that logger.
    assert loglevels == {"": logging.DEBUG, args._logger_.name: logging.DEBUG}
    assert logging.getLogger(args._logger_.name).level == logging.DEBUG


class VerbosityContractCommand(LoggingArgs):
    """Command used to pin down the verbose/quiet -> loglevel contract."""

    pass


def test_verbosity_contract():
    """0 -v -> INFO, 1 -> DEBUG, 2 -> TRACE, >=3 clamps at TRACE.

    Uses duho's own VERBOSE_LEVELS (rather than hardcoded stdlib level
    numbers) because other tests in this module register extra custom
    levels (e.g. CUSTOM=25) via the shared, process-global `logging`
    module, which shifts numeric level values without changing the
    verbose/quiet *index* contract under test here.
    """
    from duho import logging as duho_logging

    duho_logging.initverbose()
    levels = list(duho_logging.VERBOSE_LEVELS.keys())
    base = levels.index(logging.INFO)
    parser = VerbosityContractCommand._parser_()

    args = parser.parse_args([])
    assert args._verbose_loglevel_() == levels[base]

    args = parser.parse_args(["-v"])
    assert args._verbose_loglevel_() == levels[base + 1]

    # A verbose count large enough to run off either end of the table must
    # clamp to the least-severe (last) entry, regardless of table length.
    overshoot = len(levels) + 5
    args = parser.parse_args(["-v"] * overshoot)
    assert args._verbose_loglevel_() == levels[-1]

    args = parser.parse_args(["-v"] * (overshoot + 1))
    assert args._verbose_loglevel_() == levels[-1]


def test_quiet_contract():
    """-q -> WARNING, -qq -> ERROR, -qqq -> CRITICAL, more clamps at CRITICAL."""
    from duho import logging as duho_logging

    duho_logging.initverbose()
    levels = list(duho_logging.VERBOSE_LEVELS.keys())
    base = levels.index(logging.INFO)
    parser = VerbosityContractCommand._parser_()

    args = parser.parse_args(["-q"])
    assert args._verbose_loglevel_() == levels[base - 1]

    args = parser.parse_args(["-q", "-q"])
    assert args._verbose_loglevel_() == levels[base - 2]

    # A quiet count large enough to run off either end of the table must
    # clamp to the most-severe (first) entry, regardless of table length.
    overshoot = len(levels) + 5
    args = parser.parse_args(["-q"] * overshoot)
    assert args._verbose_loglevel_() == levels[0]

    args = parser.parse_args(["-q"] * (overshoot + 1))
    assert args._verbose_loglevel_() == levels[0]


def test_verbose_and_quiet_offset():
    """verbose and quiet counts offset each other around INFO."""
    from duho import logging as duho_logging

    duho_logging.initverbose()
    levels = list(duho_logging.VERBOSE_LEVELS.keys())
    base = levels.index(logging.INFO)
    parser = VerbosityContractCommand._parser_()

    args = parser.parse_args(["-v", "-v", "-q"])
    assert args._verbose_loglevel_() == levels[base + 1]

    args = parser.parse_args(["-v", "-q", "-q"])
    assert args._verbose_loglevel_() == levels[base - 1]


def test_formatter_does_not_mutate_record():
    """DefaultFormatter.format must leave the original record untouched."""
    formatter = DefaultFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    original_levelname = record.levelname
    formatter.format(record)
    assert record.levelname == original_levelname


def test_formatter_does_not_leak_across_handlers():
    """A record formatted twice (e.g. by two handlers) must not accumulate
    padding/color from the first format() call."""
    formatter = DefaultFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    first = formatter.format(record)
    second = formatter.format(record)
    assert first == second


def test_default_formatter_has_no_dead_levelsize_override_hook():
    """`_levelsize` was always None (no setter); the dead indirection
    was removed, so a formatter instance no longer carries the attribute."""
    formatter = DefaultFormatter()
    assert not hasattr(formatter, "_levelsize")


# --------------------------------------------------------------------------
# duho.main/duho.app must not skip logging setup just because the
# DEEPEST selected class (a plain Cmd leaf) has no _set_loglevels_ of its own
# -- the root class they were called/built with does.
# --------------------------------------------------------------------------


@pytest.fixture
def _clean_root_logger():
    root = logging.getLogger()
    saved = list(root.handlers)
    saved_level = root.level
    yield root
    root.handlers[:] = saved
    root.setLevel(saved_level)


def _expected_verbose_level(verbose=0, quiet=0):
    """The numeric level -v/-q of the given counts resolves to right now.

    Reads ``duho.logging.VERBOSE_LEVELS`` live (never a hardcoded stdlib
    level number): other tests in this same process register extra custom
    levels, which insert into the table and shift index positions between
    INFO and DEBUG/TRACE -- the same reason ``test_verbosity_contract``
    above does this.
    """
    from duho import logging as duho_logging

    levels = list(duho_logging.VERBOSE_LEVELS.keys())
    base = levels.index(logging.INFO)
    index = max(0, min(base + verbose - quiet, len(levels) - 1))
    return levels[index]


class _A012Deploy(Cmd):
    """A plain Cmd leaf -- no LoggingArgs of its own (the README shape)."""

    def __call__(self):
        return 0


class _A012App(LoggingArgs, Cli):
    """Root carries LoggingArgs; the dispatched leaf (_A012Deploy) does not."""

    _subcommands_ = [_A012Deploy]

    def __call__(self):
        return 0


def test_main_sets_up_logging_for_a_plain_cmd_leaf_under_a_loggingargs_root(
    _clean_root_logger,
):
    root = _clean_root_logger
    root.handlers[:] = []
    logging.getLogger("_A012App").setLevel(logging.WARNING)

    rc = duho.main(_A012App, ["-vv", "_A012Deploy"])

    assert rc == 0
    assert len(root.handlers) == 1
    assert logging.getLogger("_A012App").getEffectiveLevel() == _expected_verbose_level(
        verbose=2
    )


def test_app_sets_up_logging_for_a_plain_cmd_leaf_under_a_loggingargs_root(
    _clean_root_logger,
):
    """Same shape through duho.app -- runtime.py's identical logging tail."""
    root = _clean_root_logger
    root.handlers[:] = []
    logging.getLogger("_A012App").setLevel(logging.WARNING)

    rc = duho.app(_A012App, commands=[_A012Deploy], argv=["-vv", "_A012Deploy"])

    assert rc == 0
    assert len(root.handlers) == 1
    assert logging.getLogger("_A012App").getEffectiveLevel() == _expected_verbose_level(
        verbose=2
    )


def test_main_still_uses_the_leafs_own_logger_when_the_leaf_is_loggingargs():
    """The existing (already-working) shape is unaffected: a leaf that IS
    itself a LoggingArgs still gets its OWN logger name, not the root's."""

    class _A012LoggingLeaf(LoggingArgs, Cmd):
        def __call__(self):
            return 0

    class _A012Root(Cli):
        _subcommands_ = [_A012LoggingLeaf]

        def __call__(self):
            return 0

    root = logging.getLogger()
    saved, saved_level = list(root.handlers), root.level
    root.handlers[:] = []
    try:
        # -v/-q are declared on the LEAF here (it IS the LoggingArgs), not
        # the root, so they must follow the subcommand name.
        duho.main(_A012Root, ["_A012LoggingLeaf", "-vv"])
        assert logging.getLogger(
            "_A012LoggingLeaf"
        ).getEffectiveLevel() == _expected_verbose_level(verbose=2)
    finally:
        root.handlers[:] = saved
        root.setLevel(saved_level)


# --------------------------------------------------------------------------
# A bare `--loglevel LEVEL` must also raise the app's own logger, not
# just root -- unless -v/-q was given, in which case -v/-q decides the app's
# own level and the bare entry only still affects root.
# --------------------------------------------------------------------------


class _C006App(LoggingArgs, Cmd):
    def __call__(self):
        return 0


def test_bare_loglevel_also_raises_the_apps_own_logger():
    parser = _C006App._parser_()
    ns = parser.parse_args(["--loglevel", "DEBUG"])
    ns._set_loglevels_()
    try:
        assert logging.getLogger("_C006App").isEnabledFor(logging.DEBUG)
        assert logging.getLogger().isEnabledFor(logging.DEBUG)
    finally:
        logging.getLogger("_C006App").setLevel(logging.NOTSET)
        logging.getLogger().setLevel(logging.WARNING)


def test_explicit_verbose_flag_wins_over_a_bare_loglevel_default():
    parser = _C006App._parser_()
    ns = parser.parse_args(["--loglevel", "ERROR", "-v"])
    expected = _expected_verbose_level(verbose=1)
    loglevels = ns._set_loglevels_()
    try:
        # -v was given, so it decides the app's OWN level, not the bare ""
        # entry (ERROR) -- the bare entry still applies to root.
        assert loglevels[""] == logging.ERROR
        assert logging.getLogger("_C006App").getEffectiveLevel() == expected
    finally:
        logging.getLogger("_C006App").setLevel(logging.NOTSET)
        logging.getLogger().setLevel(logging.WARNING)


def test_named_loglevel_entry_still_wins_over_the_bare_default():
    parser = _C006App._parser_()
    ns = parser.parse_args(["--loglevel", "ERROR,_C006App:DEBUG"])
    ns._set_loglevels_()
    try:
        assert logging.getLogger("_C006App").isEnabledFor(logging.DEBUG)
    finally:
        logging.getLogger("_C006App").setLevel(logging.NOTSET)
        logging.getLogger().setLevel(logging.WARNING)


# --------------------------------------------------------------------------
# --loglevel's help/metavar must show the real grammar, and
# parse_loglevels must validate instead of silently dropping bad input.
# --------------------------------------------------------------------------


def test_loglevel_metavar_and_help_show_the_real_grammar():
    help_text = _C006App._parser_().format_help()
    assert "[NAME:]LEVEL[,...]" in help_text
    assert "Increase verbosity (repeatable)" in help_text
    assert "Decrease verbosity (repeatable)" in help_text


def test_parse_loglevels_is_case_insensitive_and_strips_whitespace():
    assert parse_loglevels("debug") == {"": logging.DEBUG}
    assert parse_loglevels(" a : DEBUG , b:info ") == {
        "a": logging.DEBUG,
        "b": logging.INFO,
    }


def test_parse_loglevels_accepts_a_numeric_level():
    assert parse_loglevels("17") == {"": 17}
    assert parse_loglevels("mod:5") == {"mod": 5}


def test_parse_loglevels_raises_on_an_unresolved_entry():
    with pytest.raises(argparse.ArgumentTypeError):
        parse_loglevels("DEGUB")
    with pytest.raises(argparse.ArgumentTypeError):
        # The old (wrong) metavar advertised KEY=VALUE; "=" is not the real
        # separator, so this must now be reported, not silently dropped.
        parse_loglevels("mod=DEBUG")


def test_parse_loglevels_matches_a_lowercase_registered_level_name_exactly():
    """A level registered under a lowercase (or mixed-case) name via stdlib's
    own ``logging.addLevelName`` must resolve by its EXACT registered
    spelling, not only by upper-casing the input -- upper-casing alone never
    finds a name that was never registered in upper case to begin with."""
    logging.addLevelName(25, "notice")
    try:
        assert parse_loglevels("notice") == {"": 25}
    finally:
        logging.addLevelName(25, "Level 25")


def test_verbose_and_quiet_accept_their_long_flag_spellings():
    """The header/docstring promised --verbose/--quiet; adding the
    long spellings (rather than correcting the docs) is the chosen fix."""
    parser = _C006App._parser_()
    ns = parser.parse_args(["--verbose", "--verbose", "--quiet"])
    assert ns.verbose == 2
    assert ns.quiet == 1


# --------------------------------------------------------------------------
# custom-level wrappers and log_exception must attribute records to
# the CALLER, not to duho/logging.py's own wrapper functions.
# --------------------------------------------------------------------------


def test_custom_level_and_log_exception_attribute_records_to_the_caller(caplog):
    from duho.logging import log_exception

    add_logging_level("STACKCHK", 22, force=True)
    logger = logging.getLogger("test_logging_stackcheck")

    # Root-scoped (no `logger=`): `logging.stackchk(...)` below logs through
    # the true root logger by design, so root's own effective level -- not
    # just this named logger's -- must be lowered for it to be enabled at
    # all.
    with caplog.at_level(1):
        logger.stackchk("via the Logger method")
        logging.stackchk("via the module-level function")
        try:
            raise ValueError("boom")
        except ValueError:
            log_exception(logger, "caught")

    this_func = (
        test_custom_level_and_log_exception_attribute_records_to_the_caller.__name__
    )
    assert len(caplog.records) == 3
    assert {r.funcName for r in caplog.records} == {this_func}


# --------------------------------------------------------------------------
# add_logging_level must refresh the -v/-q table itself, so a custom
# level participates in it regardless of whether logging was already
# configured (previously that refresh only happened inside
# init_stderr_logging, which main/app skipped once any handler existed).
# --------------------------------------------------------------------------


def test_add_logging_level_refreshes_the_verbose_table_immediately():
    from duho import logging as duho_logging

    add_logging_level("PRECONFIGURED", 27, force=True)
    assert 27 in duho_logging.VERBOSE_LEVELS
    assert "PRECONFIGURED" in duho_logging.VERBOSE_HELP


# --------------------------------------------------------------------------
# add_logging_level's collision guard must also check the LOWER-CASE
# method name it installs, not just the upper-case level name.
# --------------------------------------------------------------------------


def test_add_logging_level_refuses_to_clobber_an_unrelated_stdlib_name():
    with pytest.raises(ValueError):
        add_logging_level("LOG", 33)
    # The guard must raise BEFORE mutating anything: stdlib's own
    # `logging.log` is untouched.
    assert logging.log.__module__ == "logging"


def test_add_logging_level_refuses_to_clobber_an_unrelated_upper_case_attribute():
    """A name colliding with an unrelated UPPER-CASE stdlib attribute (never
    installed by ``add_logging_level`` itself) must raise, not silently
    no-op -- previously any ``hasattr(logging, NAME)`` hit short-circuited
    to a bare ``return``, so e.g. ``add_logging_level("BASIC_FORMAT", 44)``
    silently registered nothing at all."""
    original = logging.BASIC_FORMAT
    with pytest.raises(ValueError):
        add_logging_level("BASIC_FORMAT", 44)
    assert logging.BASIC_FORMAT == original
    assert logging.getLevelName(44) != "BASIC_FORMAT"


def test_add_logging_level_repeat_call_for_its_own_name_is_a_noop():
    """Calling ``add_logging_level`` again for a name it ALREADY installed
    (without ``force``) must stay a harmless no-op, unlike a genuinely
    unrelated collision."""
    add_logging_level("REPEATABLE", 24)
    add_logging_level("REPEATABLE", 24)  # must not raise
    assert logging.REPEATABLE == 24


def test_add_logging_level_force_replaces_an_earlier_duho_level():
    add_logging_level("DUPTEST", 26, force=True)
    first = logging.duptest
    add_logging_level("DUPTEST", 27, force=True)
    second = logging.duptest
    assert first is not second
    assert logging.DUPTEST == 27


# --------------------------------------------------------------------------
# init_stderr_logging must be idempotent on its own, independent of
# any caller-side "does the logger already have handlers" guard.
# --------------------------------------------------------------------------


def test_init_stderr_logging_is_idempotent_across_direct_calls(_clean_root_logger):
    root = _clean_root_logger
    root.handlers[:] = []
    init_stderr_logging()
    init_stderr_logging()
    assert len(root.handlers) == 1


class _C040App(LoggingArgs, Cmd):
    def __call__(self):
        return 0


def test_main_setup_logging_does_not_stack_handlers_across_repeated_calls(
    _clean_root_logger,
):
    root = _clean_root_logger
    root.handlers[:] = []
    duho.main(_C040App, [])
    count = len(root.handlers)
    duho.main(_C040App, [])
    assert len(root.handlers) == count == 1


def test_main_does_not_add_a_stderr_handler_when_the_root_already_owns_logging(
    _clean_root_logger,
):
    """0.5.4's guard, restored: an app/harness that already configured
    logging itself (`basicConfig`, pytest's own capture handler) must not
    get a SECOND, duho-installed stderr handler stacked on top of its own --
    only its handler stays. Verbosity (`setter()`) must still run regardless
    of whether the handler was installed."""
    root = _clean_root_logger
    foreign = logging.Handler()
    root.handlers[:] = [foreign]
    logging.getLogger("_C040App").setLevel(logging.WARNING)

    rc = duho.main(_C040App, ["-v"])

    assert rc == 0
    assert root.handlers == [foreign]
    assert not any(getattr(h, "_duho_stderr_handler_", False) for h in root.handlers)
    assert logging.getLogger("_C040App").getEffectiveLevel() == _expected_verbose_level(
        verbose=1
    )


# --------------------------------------------------------------------------
# _set_loglevels_ must call the (possibly overridden) bound
# `_verbose_loglevel_`, not hard-code LoggingArgs's own implementation.
# --------------------------------------------------------------------------


class _C042Override(LoggingArgs, Cmd):
    def __call__(self):
        return 0

    def _verbose_loglevel_(self):
        return logging.ERROR


def test_set_loglevels_honors_a_verbose_loglevel_override():
    parser = _C042Override._parser_()
    ns = parser.parse_args([])
    loglevels = ns._set_loglevels_()
    assert loglevels[ns._logger_.name] == logging.ERROR


# --------------------------------------------------------------------------
# duho.logging's forwarding __getattr__ must not masquerade as stdlib
# logging for dunders (__path__ made it look like a real package).
# --------------------------------------------------------------------------


def test_duho_logging_getattr_rejects_dunders_and_private_names():
    import duho.logging as duho_logging_mod

    with pytest.raises(AttributeError):
        duho_logging_mod.__path__
    with pytest.raises(AttributeError):
        getattr(duho_logging_mod, "_srcfile")
    # Public stdlib names still forward correctly.
    assert duho_logging_mod.getLogger is logging.getLogger


# --------------------------------------------------------------------------
# `--loglevel app:LEVEL` is documented as applying to the app.* logger
# SUBTREE -- a descendant logger that already has its own explicit level
# (set by an earlier import, or a previous run) must still pick it up, not
# just one that would inherit it for free via Python's normal hierarchy.
# --------------------------------------------------------------------------


class _SubtreeLoggingApp(LoggingArgs, Cmd):
    def __call__(self):
        return 0


def test_named_loglevel_reaches_a_child_logger_with_its_own_explicit_level():
    child = logging.getLogger("duho_test_subtree.child")
    child.setLevel(logging.WARNING)
    try:
        parser = _SubtreeLoggingApp._parser_()
        ns = parser.parse_args(["--loglevel", "duho_test_subtree:DEBUG"])
        ns._set_loglevels_()
        assert child.getEffectiveLevel() == logging.DEBUG
    finally:
        child.setLevel(logging.NOTSET)
        logging.getLogger("duho_test_subtree").setLevel(logging.NOTSET)
        logging.getLogger("_SubtreeLoggingApp").setLevel(logging.NOTSET)
        logging.getLogger().setLevel(logging.WARNING)


# --------------------------------------------------------------------------
# The subtree walk above must fire ONLY for a name the user explicitly
# named with `--loglevel`, never for the -v/-q-derived (or bare
# `--loglevel LEVEL`) entry for the app's own default logger -- that entry
# is applied on EVERY ordinary dispatch, and 0.5.4 never touched descendants
# at all. A `NOTSET` child already inherits for free; pinning it breaks
# hierarchical control (`logging.getLogger("app").setLevel(...)` no longer
# governing `app.child`). The walk must also never promote a `PlaceHolder`
# registry entry into a real `Logger` as a side effect.
# --------------------------------------------------------------------------


class _DefaultVerbosityApp(LoggingArgs, Cmd):
    def __call__(self):
        return 0


def test_default_verbosity_dispatch_does_not_pin_a_notset_descendant():
    child = logging.getLogger("duho_test_defverbosity.child")
    assert child.level == logging.NOTSET
    try:
        parent = logging.getLogger("duho_test_defverbosity")
        parent.setLevel(logging.WARNING)
        parser = _DefaultVerbosityApp._parser_()
        ns = parser.parse_args([])  # default verbosity: no -v/-q/--loglevel
        ns._set_loglevels_()
        # Still NOTSET -- the default-logger entry never forced a level onto
        # a pre-existing, unrelated descendant.
        assert child.level == logging.NOTSET
        assert child.getEffectiveLevel() == logging.WARNING
        parent.setLevel(logging.ERROR)
        assert child.getEffectiveLevel() == logging.ERROR
    finally:
        child.setLevel(logging.NOTSET)
        logging.getLogger("duho_test_defverbosity").setLevel(logging.NOTSET)
        logging.getLogger("_DefaultVerbosityApp").setLevel(logging.NOTSET)
        logging.getLogger().setLevel(logging.WARNING)


def test_named_loglevel_subtree_walk_never_promotes_a_placeholder():
    # Only the leaf is ever passed to `getLogger` -- "duho_test_placeholder"
    # and "duho_test_placeholder.mid" register as `PlaceHolder` ancestor
    # entries in `logging.Logger.manager.loggerDict`, not real `Logger`s.
    logging.getLogger("duho_test_placeholder.mid.leaf")
    placeholder_name = "duho_test_placeholder.mid"
    assert isinstance(
        logging.Logger.manager.loggerDict[placeholder_name], logging.PlaceHolder
    )
    try:
        parser = _SubtreeLoggingApp._parser_()
        ns = parser.parse_args(["--loglevel", "duho_test_placeholder:DEBUG"])
        ns._set_loglevels_()
        # The explicitly-named logger itself is legitimately promoted to a
        # real Logger (pre-existing behavior); its untouched descendant
        # PlaceHolder must not be.
        assert isinstance(
            logging.Logger.manager.loggerDict["duho_test_placeholder"],
            logging.Logger,
        )
        assert isinstance(
            logging.Logger.manager.loggerDict[placeholder_name],
            logging.PlaceHolder,
        )
    finally:
        logging.getLogger("duho_test_placeholder.mid.leaf").setLevel(logging.NOTSET)
        logging.getLogger("duho_test_placeholder").setLevel(logging.NOTSET)
        logging.getLogger("_SubtreeLoggingApp").setLevel(logging.NOTSET)
        logging.getLogger().setLevel(logging.WARNING)
