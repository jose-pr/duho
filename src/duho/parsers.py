"""Subparser utilities and helper functions."""

from __future__ import annotations

import argparse as _argparse
import typing as _ty


def pop_action(parser: _argparse.ArgumentParser, name: str) -> _argparse.Action:
    """Remove an action from a parser by destination name.

    Removes the action from the parser's action list, its option-string map,
    the owning argument group's ``_group_actions`` -- ``format_help`` renders
    from the group lists, so missing the last one left a "removed" flag still
    visible in help output -- AND every ``_mutually_exclusive_groups``
    entry it belonged to, so a required mutex group's error no longer names a
    flag that no longer exists.
    """
    index = None
    for idx, action in enumerate(parser._actions):
        if action.dest == name:
            index = idx
            break
    if index is not None:
        action = parser._actions.pop(index)
        for k in action.option_strings:
            parser._option_string_actions.pop(k)
        container = getattr(action, "container", None)
        group_actions = getattr(container, "_group_actions", None)
        if group_actions is not None and action in group_actions:
            group_actions.remove(action)
        for mutex_group in getattr(parser, "_mutually_exclusive_groups", []):
            if action in mutex_group._group_actions:
                mutex_group._group_actions.remove(action)
        return action
    raise KeyError(name)


def insert_action(
    parser: _argparse.ArgumentParser,
    action: _argparse.Action,
    index: int | None = None,
) -> None:
    """Insert an action into a parser (optionally at a given index).

    ``index=None`` (the default) APPENDS. The previous default, ``-1``, used
    ``list.insert(-1, x)`` semantics -- which puts ``x`` BEFORE the last
    action, not at the end, almost certainly not what a caller reordering a
    flag wants. Also re-adds the action to an argument group's
    ``_group_actions`` -- without this, ``format_help`` (which renders from
    the GROUP lists, never ``parser._actions`` directly) never shows the
    reinserted flag -- the defect ``pop_action`` fixes, now fixed in the
    opposite direction too.

    The target group is the action's OWN pre-removal one (``action.
    container``, which ``pop_action`` removes the action FROM but never
    clears) when it still has one -- so an action that belonged to a titled
    ``add_argument_group(...)`` goes back to that SAME group, not the
    parser's default "options"/"positional arguments" section. Only an
    action with no ``container`` of its own yet (never added through a
    group at all) falls back to the parser's own ``_optionals``/
    ``_positionals`` (chosen by whether the action has option strings -- the
    same split ``add_argument`` itself uses).
    """
    if index is None:
        parser._actions.append(action)
    else:
        parser._actions.insert(index, action)
    for k in action.option_strings:
        parser._option_string_actions[k] = action
    group = getattr(action, "container", None)
    if group is None:
        group = parser._optionals if action.option_strings else parser._positionals
    action.container = group
    if action not in group._group_actions:
        group._group_actions.append(action)


def add_help_argument(parser: _argparse.ArgumentParser) -> _argparse.Action:
    """Add a help argument to a parser."""
    return parser.add_argument(
        "-h",
        "--help",
        action="help",
        default=_argparse.SUPPRESS,
        help=("show this help message and exit"),
    )


class _NoOpAction(_argparse.Action):
    """An ``Action`` whose ``__call__`` does nothing.

    Installed transiently, via a per-instance ``__class__`` swap, on any
    action :func:`prerun_parse` treats as "terminal" for the duration of its
    advisory parse -- argparse's own ``-h``/``--help`` and ``--version``
    (``_HelpAction``/``_VersionAction``, including duho's ``_AgentHelpAction``,
    which IS a ``_HelpAction``), plus duho's ``--print-completion``/
    ``--help-agents`` actions (see :func:`_is_terminal_action`). Swapping the
    *instance's* class, never argparse's or duho's own class, keeps the
    surgery local and thread-safe; the caller restores it in a
    ``finally``.
    """

    def __call__(self, parser, namespace, values, option_string=None):  # noqa: D401
        return None


class _RelaxedSubParsersAction(_argparse._SubParsersAction):
    """A ``_SubParsersAction`` whose ``__call__`` tolerates any subcommand name.

    Records the first subcommand name, then re-parses the remaining args with the
    parent parser so trailing globals still land; unknown/incomplete subcommands
    do not error. Installed transiently via a per-instance ``__class__`` swap
    (never a class-global patch of argparse) so it is safe for distinct parser
    instances used concurrently. The "seen a subcommand yet" flag lives on the
    instance, not a shared closure.
    """

    def __call__(self, parser, namespace, values, option_string=None):
        parser_name = values[0]
        arg_strings = values[1:]

        if not getattr(self, "_duho_action_called_", False):
            # argparse's own `_SubParsersAction.__call__` guards this identical
            # assignment with `if self.dest is not SUPPRESS` -- a subparsers
            # action built via a plain `add_subparsers()` (no explicit `dest=`)
            # otherwise leaves a literal `"==SUPPRESS=="` key on the returned
            # namespace.
            if self.dest is not _argparse.SUPPRESS:
                setattr(namespace, self.dest, parser_name)
            self._duho_action_called_ = True  # type: ignore[attr-defined]

        subnamespace, arg_strings = parser.parse_known_args(arg_strings, namespace)
        for key, value in vars(subnamespace).items():
            setattr(namespace, key, value)

        if arg_strings:
            vars(namespace).setdefault(_argparse._UNRECOGNIZED_ARGS_ATTR, [])
            getattr(namespace, _argparse._UNRECOGNIZED_ARGS_ATTR).extend(arg_strings)


def disable_subparser_check(action: _argparse._SubParsersAction) -> None:
    """Relax name validation on THIS ``_SubParsersAction`` instance.

    Per-instance surgery: swaps only this action's class to
    :class:`_RelaxedSubParsersAction` and nulls its ``choices`` (so argparse's
    name check is skipped). Reentrant via a depth counter: a NESTED
    disable (this action is already relaxed) only bumps the depth and leaves
    the saved original state alone, so the matching, non-outermost
    :func:`enable_subparser_check` calls only decrement it -- only the
    OUTERMOST pair actually saves/restores. Before this, a nested disable
    saved the already-relaxed state, so the inner ``enable`` restored THAT
    (still relaxed) and the outer ``enable`` found nothing left to restore,
    leaving the action permanently relaxed. No ``argparse`` class attribute is
    ever mutated; safe for distinct parser instances used concurrently,
    but this action itself is ordinary, unsynchronized instance state -- two
    threads racing the SAME action need their own external lock.
    """
    depth = getattr(action, "_duho_disable_depth_", 0)
    if depth == 0:
        action._duho_saved_ = (action.__class__, action.choices)  # type: ignore[attr-defined]
        action.choices = None  # type: ignore
        action._duho_action_called_ = False  # type: ignore[attr-defined]
        action.__class__ = _RelaxedSubParsersAction
    action._duho_disable_depth_ = depth + 1  # type: ignore[attr-defined]


def enable_subparser_check(action: _argparse._SubParsersAction) -> None:
    """Restore the class + choices saved by :func:`disable_subparser_check`.

    Reentrant: only the call that brings the depth counter back to
    ``0`` (the OUTERMOST ``enable`` matching the OUTERMOST ``disable``)
    actually restores anything; an inner call just decrements.
    """
    depth = getattr(action, "_duho_disable_depth_", 0)
    if depth > 1:
        action._duho_disable_depth_ = depth - 1  # type: ignore[attr-defined]
        return
    saved = getattr(action, "_duho_saved_", None)
    if saved is not None:
        action.__class__, action.choices = saved
        del action._duho_saved_
    if hasattr(action, "_duho_action_called_"):
        del action._duho_action_called_
    if hasattr(action, "_duho_disable_depth_"):
        del action._duho_disable_depth_


def find_subparsers(
    parser: _argparse.ArgumentParser,
) -> _argparse._SubParsersAction | None:
    """``parser``'s subparsers action, if it has one.

    The one lookup every parser-tree walker needs (a parser can carry at most
    one, since argparse itself raises on a second ``add_subparsers()`` call);
    shared here instead of a hand-written ``for action in parser._actions:
    isinstance(...)`` loop repeated at each call site.
    """
    for action in parser._actions:
        if isinstance(action, _argparse._SubParsersAction):
            return action
    return None


def strip_subparsers(parser: _argparse.ArgumentParser):
    """Detach ``parser``'s subparsers action (if any) from BOTH ``_actions``
    and whichever argument group it belongs to, returning what
    :func:`_restore_subparsers` needs to put it back in the exact same place.

    Removing only from ``_actions`` (a past, buggy shortcut this replaces)
    leaves the action listed in its owning group's ``_group_actions``,
    which ``format_help``/usage formatting renders from -- so a caller that
    stripped it for good (e.g. a subcommand's ``parents=`` donor parser, which
    never wants the whole app's command tree inherited downward) still showed
    it in help. Safe for a PERMANENT removal too: just discard the returned
    handle instead of passing it to :func:`_restore_subparsers`.

    Returns ``None`` when ``parser`` has no subparsers action.
    """
    action = find_subparsers(parser)
    if action is None:
        return None
    actions_index = parser._actions.index(action)
    parser._actions.pop(actions_index)
    group = None
    group_index = None
    for candidate in parser._action_groups:
        group_actions = candidate._group_actions
        if action in group_actions:
            group = candidate
            group_index = group_actions.index(action)
            group_actions.pop(group_index)
            break
    return (action, actions_index, group, group_index)


def _restore_subparsers(parser: _argparse.ArgumentParser, saved) -> None:
    """Undo :func:`strip_subparsers`; a no-op for its ``None`` result."""
    if saved is None:
        return
    action, actions_index, group, group_index = saved
    parser._actions.insert(actions_index, action)
    if group is not None:
        group._group_actions.insert(group_index, action)


def command_name(command) -> str:
    """Re-export of ``duho.args._command_name``, the one canonical
    subcommand-naming rule (a class's OWN ``_parsername_`` if it declares one
    -- checked through the class's own ``__dict__``, never inherited -- else
    its class name).

    Exposed here so a parser-tree consumer that only needs naming/subparser
    utilities (this module) does not also have to import ``duho.args``
    directly. Forwards lazily, function-local (rather than a top-level
    import), because ``args.py`` itself imports this module for
    :func:`prerun_parse` -- a top-level import the other way would cycle.
    """
    from .args._naming import _command_name

    return _command_name(command)


def unique_subcommands(
    parser: _argparse.ArgumentParser, seen: set | None = None
) -> _ty.Iterator[tuple]:
    """Yield ``(canonical_name, aliases, subparser)`` once per DISTINCT
    subcommand of ``parser``.

    argparse registers every alias as an extra ``choices`` key pointing at
    the SAME subparser object; entries are grouped by ``id(subparser)`` and
    each is yielded once, under its canonical name -- the subcommand class's
    own :func:`command_name` if it is one of the registered names, else the
    first-seen key. ``aliases`` is every OTHER registered name for that same
    subparser (empty when there are none).

    ``seen`` is the caller's running set of already-yielded subparser ids,
    threaded through a whole-tree walk (pass the same set into a recursive
    call) so a subparser reached under multiple paths is never yielded twice;
    omit it (the default) for a single-level call. Previously copied by hand,
    with the same grouping/tie-break logic, by ``duho.agenthelp`` and
    ``duho.mcp`` -- a fix to canonical-name selection used to have
    to land in both copies to not silently diverge.
    """
    if seen is None:
        seen = set()
    subparsers_action = find_subparsers(parser)
    if subparsers_action is None:
        return

    grouped: dict = {}
    order: list = []
    for choice_name, subparser in (subparsers_action.choices or {}).items():
        key = id(subparser)
        if key not in grouped:
            grouped[key] = {"parser": subparser, "names": []}
            order.append(key)
        grouped[key]["names"].append(choice_name)

    for key in order:
        if key in seen:
            continue
        seen.add(key)
        entry = grouped[key]
        subparser = entry["parser"]
        names = entry["names"]
        sub_cls = getattr(subparser, "_duho_cls_", None)
        canonical = command_name(sub_cls) if sub_cls else None
        if canonical not in names:
            canonical = names[0]
        aliases = tuple(n for n in names if n != canonical)
        yield canonical, aliases, subparser


def _is_terminal_action(action: _argparse.Action) -> bool:
    """True for an action :func:`prerun_parse` must silence during its
    advisory parse: argparse's own ``_HelpAction``/``_VersionAction`` (a duho
    ``_AgentHelpAction`` IS a ``_HelpAction``, so it's covered too) plus
    duho's own ``--print-completion``/``--help-agents`` actions.

    The latter two are recognized via a LAZY, in-function import of
    ``duho.args`` rather than a module-level one: ``args.py`` imports
    ``parsers.py`` (for :func:`prerun_parse` itself), so a top-level import
    the other way would be circular. By the time this function is actually
    CALLED, both modules are fully loaded.
    """
    if isinstance(action, (_argparse._HelpAction, _argparse._VersionAction)):
        return True
    from .args._actions import _AgentHelpFlagAction, _PrintCompletionAction

    return isinstance(action, (_PrintCompletionAction, _AgentHelpFlagAction))


def prerun_parse(
    parser: _argparse.ArgumentParser,
    argv: _ty.Sequence[str] | None = None,
    *,
    quiet: bool = False,
) -> _argparse.Namespace:
    """Parse arguments without a subcommand descent or any print-and-exit
    side effect.

    An advisory, throwaway pre-parse of ``parser``'s ROOT-level options only:

    * Any subparsers action is DETACHED for the duration of the call (see
      :func:`strip_subparsers`) and restored exactly afterward. A subcommand
      name -- or anything after it -- becomes an ordinary unrecognized
      trailing token instead of re-entering the parser's own (possibly
      duho-patched) ``parse_known_args``, whatever it is. This is what makes
      the exported ``duho.parsers.prerun_parse`` safe to call directly on any
      duho root that has ``_subcommands_``, and what lets
      ``duho.app``'s advisory ``register`` prepass run on a root that already
      has built-in subcommands without a ``KeyError('#cls')`` from a
      double-popped selection marker.
    * Every TERMINAL action (:func:`_is_terminal_action`: ``-h``/``--help``,
      ``--version``, ``--print-completion``, ``--help-agents``) is swapped to
      a no-op for the duration of the call, also restored afterward -- widened
      from the original, help-only version: before, only
      ``--help`` was silenced, so ``--version`` printed twice (once here, once
      on the real parse afterward) and ``--print-completion`` wrote two
      concatenated scripts.

    Since there is never a reachable subparser action during this call, only
    ``parser``'s OWN root-level actions can ever be terminal here -- no
    subparser subtree to walk.

    When ``quiet`` is true, ``parser.error()`` (a missing required option, a
    bad ``type=`` conversion) is ALSO silenced: it raises ``SystemExit(2)``
    without writing usage/error text, for a caller (``duho.app``'s advisory
    prepass) that re-parses for real right after and reports any error
    exactly once, itself. ``quiet=False`` (the default --
    ``duho.parse_globals``'s case, which never re-parses) leaves
    ``parser.error()`` writing normally, so a genuinely missing global is
    still reported, once, here.

    All surgery is per-instance, restored in ``finally``: no ``argparse``/duho
    CLASS is ever mutated, and ``parser`` itself is back to its original shape
    (subparsers action included) before this returns or raises -- safe for
    repeated and nested calls, since nothing here is shared state.
    """
    saved_subparsers = strip_subparsers(parser)

    terminal_actions = [a for a in parser._actions if _is_terminal_action(a)]
    saved_classes = [(a, a.__class__) for a in terminal_actions]
    for a in terminal_actions:
        a.__class__ = _NoOpAction

    if quiet:

        def _silent_error(message=None):  # noqa: ARG001 - argparse's own signature
            raise SystemExit(2)

        parser.error = _silent_error  # type: ignore[method-assign]

    try:
        args, _unused = parser.parse_known_args(argv)
    finally:
        if quiet:
            del parser.error
        for a, cls in saved_classes:
            a.__class__ = cls
        _restore_subparsers(parser, saved_subparsers)
    return args


__all__ = [
    "pop_action",
    "insert_action",
    "add_help_argument",
    "disable_subparser_check",
    "enable_subparser_check",
    "prerun_parse",
    "find_subparsers",
    "strip_subparsers",
    "unique_subcommands",
    "command_name",
]
