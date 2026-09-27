"""Regression tests: a class's subcommand name must never be persisted onto
the class itself, only ever resolved fresh from that class's OWN declaration
(or its class name).

Before this fix, ``Args._parser_`` wrote its derived name back onto the class
with ``setattr(cls, "_parsername_", name)``. Every reader used plain
``getattr``, which follows the MRO -- so once a base class's parser had been
built once, EVERY SUBCLASS built afterwards (a sibling subcommand, or a
subclass registered alongside its own base) inherited the base's derived
name and collapsed onto it (a real user-facing bug, not just a test
artifact -- a natural way to share global options between sibling
subcommands is exactly "subclass a shared base").
"""

import argparse

import duho
from duho import Cli, Cmd, LoggingArgs


def _subcommand_choices(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    return set()


class _Base(Cmd):
    """Shared base with a global option."""

    host: str = "localhost"
    "Target host"
    ("--host",)

    def __call__(self):
        return 0


class _Push(_Base):
    """Push to the remote."""

    def __call__(self):
        return 0


class _Pull(_Base):
    """Pull from the remote."""

    def __call__(self):
        return 0


def test_building_base_first_does_not_rename_subclasses():
    """Building `_Base`'s own parser must not leak its name onto `_Push`/`_Pull`."""
    duho.parser(_Base)  # builds _Base's own parser first, as a sibling would

    push_parser = duho.parser(_Push)
    pull_parser = duho.parser(_Pull)
    assert push_parser.prog == "_Push"
    assert pull_parser.prog == "_Pull"


def test_siblings_in_static_subcommands_tree_keep_their_own_names():
    """A base command and a subclass of it registered as sibling subcommands
    must each keep their own name (previously: 3.11+ raised "conflicting
    subparser"; 3.9/3.10 silently misrouted dispatch)."""

    class App(Cli):
        _subcommands_ = [_Base, _Push]

        def __call__(self):
            return 0

    parser = App._parser_()
    assert _subcommand_choices(parser) == {"_Base", "_Push"}

    pushed = duho.parse(App, ["_Push"])
    assert type(pushed).__name__ == "_Push"

    based = duho.parse(App, ["_Base"])
    assert type(based).__name__ == "_Base"


def test_app_with_class_commands_sharing_a_base_keeps_both_names():
    """`duho.app(commands=[Deploy, Status])` where both subclass a shared
    `Root` must register both names, not collapse onto the base's."""

    class Root(Cmd):
        region: str = "us"
        ("--region",)

        def __call__(self):
            return 0

    class Deploy(Root):
        def __call__(self):
            return 1

    class Status(Root):
        def __call__(self):
            return 2

    assert duho.app(Root, commands=[Deploy, Status], argv=["Deploy"]) == 1
    assert duho.app(Root, commands=[Deploy, Status], argv=["Status"]) == 2


def test_undeclared_subclass_gets_its_own_name_not_the_bases():
    """A subclass that does not declare its OWN `_parsername_` always gets
    its own class name -- an inherited (not self-declared) `_parsername_`
    would be exactly the framework-derived-name-leak shape this fix closes,
    so `_command_name` deliberately checks the class's own ``__dict__``, not
    ``getattr``'s MRO-following lookup."""

    class WithName(Cmd):
        _parsername_ = "shared-name"

        def __call__(self):
            return 0

    class UndeclaredSubclass(WithName):
        pass

    assert duho.parser(WithName).prog == "shared-name"
    assert duho.parser(UndeclaredSubclass).prog == "UndeclaredSubclass"


def test_subclass_declaring_its_own_parsername_wins():
    """A subclass MAY still explicitly opt into sharing a name -- that is a
    deliberate declaration on the subclass itself, not an accidental leak."""

    class WithName(Cmd):
        _parsername_ = "shared-name"

        def __call__(self):
            return 0

    class DeclaredSubclass(WithName):
        _parsername_ = "shared-name"

    assert duho.parser(DeclaredSubclass).prog == "shared-name"


class _NamedBase(Cmd):
    """A base declaring an EXPLICIT ``_parsername_`` (not the derived,
    class-name-based one)."""

    _parsername_ = "base-cmd"

    def __call__(self):
        return 0


class _UndeclaredChild(_NamedBase):
    """A subclass that does not re-declare ``_parsername_`` of its own."""


def test_explicit_parsername_on_a_base_is_not_inherited_by_a_plain_subclass():
    """A base's EXPLICITLY declared ``_parsername_`` (not just a derived
    one) is resolved the same own-class-only way as any other: a subclass
    that doesn't declare its own gets its OWN class name, never the base's.

    [minor] This is a deliberate choice, not an oversight: inheriting an
    explicit name here would reopen exactly the sibling-subcommand
    name-collision this whole own-``vars()``-only rule exists to close (two
    subclasses of the same explicitly-named base, registered as separate
    subcommands, would collapse onto one name the moment the base's own
    parser had been built once). A class that wants to share a base's
    explicit name still can -- by declaring ``_parsername_`` on itself too,
    exactly like any other subclass (see the sibling test above).
    """
    assert duho.parser(_NamedBase).prog == "base-cmd"
    assert duho.parser(_UndeclaredChild).prog == "_UndeclaredChild"


class _LoggedBase(LoggingArgs, Cmd):
    def __call__(self):
        return 0


class _LoggedChild(_LoggedBase):
    def __call__(self):
        return 0


def test_child_logger_name_is_its_own_not_the_built_parents(caplog):
    """LoggingArgs._logger_ must reflect the class ACTUALLY selected, not a
    name inherited from the base's own (no-longer-persisted) build."""
    duho.parser(_LoggedBase)  # build the base first

    child = duho.parse(_LoggedChild, [])
    assert child._logger_.name == "_LoggedChild"

    base = duho.parse(_LoggedBase, [])
    assert base._logger_.name == "_LoggedBase"


def test_directly_constructed_logging_args_command_has_a_working_logger():
    """A LoggingArgs command constructed directly (never parsed) must not
    raise in `_logger_` -- it used to require `self._parsername_`, which only
    existed once SOME parser for the class had been built."""

    class Named(LoggingArgs, Cmd):
        _logger_name_ = "myapp"

        def __call__(self):
            return 0

    instance = Named()
    assert instance._logger_.name == "myapp"


def test_directly_constructed_command_without_logger_name_falls_back_to_class_name():
    instance = _LoggedChild()
    assert instance._logger_.name == "_LoggedChild"


def test_command_name_helper_is_shared_not_duplicated():
    """runtime/discovery/mcp/presets all resolve a command's name
    through duho.args's own `_command_name`, not several independent copies
    of the same rule that could silently drift apart."""
    import duho.args as args_mod
    import duho.discovery as discovery_mod
    import duho.mcp as mcp_mod
    import duho.presets as presets_mod
    import duho.runtime as runtime_mod

    assert discovery_mod._command_name is args_mod._command_name
    assert runtime_mod._command_name is args_mod._command_name
    assert mcp_mod._command_name is args_mod._command_name
    assert presets_mod._command_name is args_mod._command_name
