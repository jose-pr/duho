from __future__ import annotations

import argparse as _argparse
import sys as _sys
import typing as _ty

from .. import _compat as _compat

from ._helptext import _write_machine_text

#: The shells `print_completion` and `--print-completion` accept; the flag's
#: `choices=` and the function's validation both read this tuple.
_COMPLETION_SHELLS = ("bash", "zsh", "fish", "powershell")


class _Utf8SafeVersionAction(_argparse._VersionAction):
    """argparse's ``version`` action, written through ``_compat.write_human``.

    ``parser._print_message`` raises ``UnicodeEncodeError`` for a non-ASCII
    version on a non-UTF-8 stdout (a piped Windows ``cp1252``); ``write_human``
    falls back to ``backslashreplace`` and is otherwise byte-identical.
    Subclasses ``_VersionAction`` so ``isinstance`` checks still match.
    """

    def __call__(self, parser, namespace, values, option_string=None):
        version = self.version
        if version is None:
            version = parser.version
        formatter = parser._get_formatter()
        formatter.add_text(version)
        message = formatter.format_help()
        if message:
            _compat.write_human(message, _sys.stdout)
        parser.exit()


class _PrintCompletionAction(_argparse.Action):
    """``--print-completion``: emit the root parser's completion script, exit 0.

    ``root_parser`` is captured at injection time because a subcommand's
    parser sees only its own subtree. The script binds the root ``prog``.
    """

    def __init__(self, option_strings, dest, root_parser=None, **kwargs):
        kwargs.setdefault("nargs", None)
        kwargs.setdefault("default", _argparse.SUPPRESS)
        super().__init__(option_strings, dest, **kwargs)
        self.root_parser = root_parser

    def __call__(self, parser, namespace, values, option_string=None):
        from .. import completion as _completion

        emitter = getattr(_completion, values)
        root = self.root_parser if self.root_parser is not None else parser
        _write_machine_text(emitter(root, prog=root.prog), _sys.stdout)
        parser.exit()


class _AgentHelpAction(_argparse._HelpAction):
    """``-h``/``--help`` action that emits agent help when the env trigger is set.

    Installed by a per-instance ``__class__`` swap of argparse's ``_HelpAction``,
    so argparse's classes are never mutated. With the trigger env var
    (``_duho_agent_env_``, default ``AGENT_HELP``) truthy it prints the agent
    document for THIS parser; otherwise it prints the normal human help.
    """

    #: The app's ROOT class (version and exit codes come from it, while
    #: ``_duho_cls_`` stays the current node) and the trigger variable name
    #: (``None`` means ``AGENT_HELP``); both are set after the ``__class__`` swap.
    _duho_agent_cls_ = None
    _duho_agent_env_ = None

    def __call__(self, parser, namespace, values, option_string=None):
        from .. import agenthelp as _agenthelp

        if _agenthelp.agent_help_requested(self._duho_agent_env_):
            spec = _agenthelp.describe_parser(
                parser, root=True, root_cls=self._duho_agent_cls_
            )
            _compat.write_machine(_agenthelp.render(spec), _sys.stdout)
            parser.exit()
        # Human help shows each field's class default, never a live env/config
        # value installed as `action.default`: argparse's own `%(default)s`
        # expansion reads that attribute whatever the formatter, so it is
        # swapped out for the duration of the render.
        with _agenthelp._redact_action_defaults(parser):
            # `_print_message` raises UnicodeEncodeError on a non-UTF-8 pipe.
            _compat.write_human(parser.format_help(), _sys.stdout)
        parser.exit()


class _AgentHelpFlagAction(_argparse.Action):
    """The opt-in ``--help-agents`` flag: always emit agent help, then exit 0.

    ``root_parser`` is captured before subparsers are attached; it carries the
    full tree when the flag fires, so the document covers every subcommand.
    """

    def __init__(self, option_strings, dest, root_parser=None, root_cls=None, **kwargs):
        kwargs.setdefault("nargs", 0)
        kwargs.setdefault("default", _argparse.SUPPRESS)
        super().__init__(option_strings, dest, **kwargs)
        self.root_parser = root_parser
        self.root_cls = root_cls

    def __call__(self, parser, namespace, values, option_string=None):
        from .. import agenthelp as _agenthelp

        root = self.root_parser if self.root_parser is not None else parser
        spec = _agenthelp.describe_parser(root, root=True, root_cls=self.root_cls)
        _compat.write_machine(_agenthelp.render(spec), _sys.stdout)
        parser.exit()


def _install_agent_help(parser, cls, is_subcommand, agent_root_cls=None):
    """Wire both agent-help triggers onto a freshly built parser.

    Stashes ``cls`` as ``parser._duho_cls_`` (the node whose field metadata is
    described), swaps each ``_HelpAction`` for :class:`_AgentHelpAction`, and on
    the top-level parser adds ``--help-agents`` when ``_agent_help_`` is set.
    ``agent_root_cls`` is the app's root class (``None`` at the top, where
    ``cls`` is the root); it supplies the version and exit codes.
    """
    parser._duho_cls_ = cls  # type: ignore[attr-defined]
    root_cls = agent_root_cls if agent_root_cls is not None else cls

    env_name = getattr(cls, "_agent_help_env_", None)
    for action in parser._actions:
        if isinstance(action, _argparse._HelpAction) and not isinstance(
            action, _AgentHelpAction
        ):
            action.__class__ = _AgentHelpAction
            action._duho_agent_cls_ = root_cls  # type: ignore[attr-defined]
            action._duho_agent_env_ = env_name  # type: ignore[attr-defined]

    if not is_subcommand and getattr(cls, "_agent_help_", False):
        existing_dests = {action.dest for action in parser._actions}
        if "help_agents" not in existing_dests:
            parser.add_argument(
                "--help-agents",
                dest="help_agents",
                action=_AgentHelpFlagAction,
                root_parser=parser,
                root_cls=root_cls,
                help="Show a detailed machine-readable description of this CLI "
                "(for AI agents) and exit.",
            )


def print_completion(
    cls,
    shell: str,
    file: _ty.Optional[_ty.TextIO] = None,
    *,
    prog: _ty.Optional[str] = None,
) -> None:
    """Print a shell completion script for `cls` to `file` (default sys.stdout).

    ``shell`` is one of ``"bash"``, ``"zsh"``, ``"fish"``, or ``"powershell"``
    -- an unrecognized name raises a clear ``ValueError`` listing the
    valid ones, instead of an ``AttributeError`` about ``duho.completion``'s
    internals -- or, worse, silently calling one of that module's PRIVATE
    helpers as if it were an emitter. Standalone counterpart to the
    `--print-completion` flag injected when `_completion_ = True` -- builds
    cls's parser tree fresh (independent of whether `_completion_` is set)
    and delegates to `duho.completion.<shell>`.

    ``prog`` overrides the command name the emitted script binds to; by
    default it is the root parser's ``prog``, the application's name (see
    :func:`_app_name`), however the program was launched.
    """
    from .. import completion as _completion

    if shell not in _COMPLETION_SHELLS:
        raise ValueError(
            f"unknown shell {shell!r}; choose from " f"{', '.join(_COMPLETION_SHELLS)}"
        )
    parser = cls._parser_()
    emitter = getattr(_completion, shell)
    if prog is None:
        prog = parser.prog
    _write_machine_text(emitter(parser, prog=prog), file)


def print_agent_help(cls, file: _ty.Optional[_ty.TextIO] = None) -> None:
    """Print a detailed, machine-readable (JSON) agent-help document for `cls`.

    Standalone counterpart to the ``--help-agents`` flag / the ``AGENT_HELP``
    env-var trigger: builds ``cls``'s parser tree fresh and describes it
    (independent of whether either trigger is wired up), then writes the JSON to
    ``file`` (default ``sys.stdout``). Delegates to
    :func:`duho.agenthelp.print_agent_help`.
    """
    from .. import agenthelp as _agenthelp

    _agenthelp.print_agent_help(cls, file=file)
