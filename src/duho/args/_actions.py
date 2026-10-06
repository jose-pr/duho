import argparse as _argparse
import sys as _sys

from .. import _compat as _compat

from ._helptext import _write_machine_text

#: The shells `duho.print_completion`/`--print-completion` accept, in one
#: place -- both the argparse `choices=` for the CLI flag and the
#: standalone function's own validation read this, instead of duplicating the
#: tuple (and silently drifting) between the two call sites.
_COMPLETION_SHELLS = ("bash", "zsh", "fish", "powershell")


class _Utf8SafeVersionAction(_argparse._VersionAction):
    """Same behavior, text, and exit code as argparse's own ``action="version"``
    (stdlib ``_VersionAction``, which this subclasses -- unchanged ``__init__``,
    so a real ``isinstance(action, argparse._VersionAction)`` check, e.g.
    ``parsers._is_terminal_action``'s, still recognizes it), except the
    version string is written via :func:`duho._compat.write_human` instead
    of ``parser._print_message``.

    ``_print_message`` does ``file.write(message)`` directly and only
    swallows ``(AttributeError, OSError)`` -- a ``UnicodeEncodeError`` from a
    non-ASCII version/prog string propagates uncaught (empty output, exit 1)
    on a non-UTF-8 stdout, e.g. Windows' default piped/redirected ``cp1252``.
    ``write_human`` tries the same plain ``stream.write`` first, so the
    common (ASCII, or already-UTF-8) case is byte-identical to stock
    argparse; only a genuinely unrepresentable character falls back to the
    stream's own encoding with ``errors="backslashreplace"`` instead of
    raising. This is duho's crash-proofing for ``--version`` when
    :func:`duho.utf8_stdio` did NOT already make stdout UTF-8 -- opted out
    via ``_utf8_stdio_ = False``/``utf8_stdio=False``, or a parser built and
    used outside ``duho.main``/``duho.app`` entirely.
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
    """argparse Action for --print-completion: emits a shell completion
    script for the *root* parser tree and exits 0, mirroring how the
    stdlib's own action="version" short-circuits before dispatch.

    ``root_parser`` is captured at injection time (the top-level parser
    built by this call to _parser_/_initparser_) rather than re-derived
    from ``parser`` at call time, since a subcommand's own parser only
    sees its own subtree, not the whole app.

    The emitted script binds the root parser's ``prog`` -- the application's
    name (see :func:`_app_name`), however the program was launched.
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

    Installed by :meth:`Args._initparser_` via a per-instance ``__class__`` swap
    of argparse's own ``_HelpAction`` -- the same blessed idiom ``parsers.py``
    uses (``_NoOpHelpAction``/``_RelaxedSubParsersAction``): argparse's classes
    are never mutated, so the surgery stays thread-safe and reentrant. When the
    trigger env var (``_duho_agent_env_`` or the ``AGENT_HELP`` default) is set
    truthy, it prints the machine-readable agent document for THIS parser and
    exits 0; otherwise it defers to the normal human ``_HelpAction``.
    """

    #: The app's ROOT duho class (for version/exit-code lookup -- kept
    #: distinct from THIS parser's own ``_duho_cls_``, which stays the current
    #: node so a subcommand-scoped document still reports the APP's version
    #: and exit codes, not its own usually-unset ones); the trigger env-var
    #: name (``None`` -> the ``AGENT_HELP`` default). Both are set as instance
    #: attrs right after the ``__class__`` swap.
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
        # Human help: show only each field's CLASS default, never a
        # live env/config value `_stage_layers`/`_apply_default_layers_one`
        # may have already installed as `action.default` for THIS invocation.
        # `_stash_default_provenance` alone only stashes the class default
        # onto each action for `DefaultsFormatter` (which only ever sees
        # `action`, never `parser`) to read -- it does NOT touch
        # `action.default` itself, so argparse's OWN `%(default)s` expansion
        # (`HelpFormatter._expand_help`, which reads `action.default`
        # directly and runs regardless of formatter) still saw the live
        # value for any help text that spells the placeholder literally.
        # `_redact_action_defaults` additionally swaps that attribute for the
        # duration of this render, then restores it.
        with _agenthelp._redact_action_defaults(parser):
            # Write via the stream's own encoding with a lossy
            # fallback (`errors="backslashreplace"`) instead of argparse's own
            # `_print_message`, which writes strict-encoded text and raises
            # `UnicodeEncodeError` (empty output, exit 1) for a docstring/help
            # character outside a piped Windows console's code page.
            _compat.write_human(parser.format_help(), _sys.stdout)
        parser.exit()


class _AgentHelpFlagAction(_argparse.Action):
    """The opt-in ``--help-agents`` flag: always emit agent help, then exit 0.

    Mirrors :class:`_PrintCompletionAction`: ``root_parser`` is captured at
    injection time (in ``_initparser_``, before subparsers are attached) but the
    same parser object carries the full tree by the time the flag fires at parse
    time, so the emitted document covers every subcommand.
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
    """Wire up both agent-help triggers on a freshly built parser.

    1. Stash ``cls`` on the parser as ``_duho_cls_`` so the emitter can enrich
       each command with duho's field metadata (env bindings, conflicts, declared
       types) -- see :mod:`duho.agenthelp`.
    2. Swap every ``_HelpAction`` on this parser to :class:`_AgentHelpAction` so
       ``--help`` becomes agent-aware (env-triggered). Always on: it only changes
       ``--help`` behavior when the trigger env var is deliberately set, so
       normal human help is unchanged.
    3. On the top-level parser only, when ``_agent_help_ = True``, add the opt-in
       ``--help-agents`` flag (guarded against a duplicate dest).

    ``agent_root_cls`` is the APP's true root class, threaded down from
    :meth:`Args._parser_`'s own recursive ``_subcommands_`` build (mirrors how
    ``_inherited_formatter_class_`` propagates the effective help formatter) --
    ``None`` at the true top level, where ``cls`` itself IS the root. It is
    stashed on the (possibly swapped) help action as ``_duho_agent_cls_`` so a
    subcommand-scoped document (``AGENT_HELP=1 app sub --help``) still reports
    the APP's own ``_version_``/``_exit_codes_``, not the subcommand's usually
    unset ones -- ``_duho_cls_`` itself stays ``cls`` (the current node), since
    field metadata must still come from THIS node, not the root.
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


def print_completion(cls, shell: str, file=None, *, prog: "str | None" = None) -> None:
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


def print_agent_help(cls, file=None) -> None:
    """Print a detailed, machine-readable (JSON) agent-help document for `cls`.

    Standalone counterpart to the ``--help-agents`` flag / the ``AGENT_HELP``
    env-var trigger: builds ``cls``'s parser tree fresh and describes it
    (independent of whether either trigger is wired up), then writes the JSON to
    ``file`` (default ``sys.stdout``). Delegates to
    :func:`duho.agenthelp.print_agent_help`.
    """
    from .. import agenthelp as _agenthelp

    _agenthelp.print_agent_help(cls, file=file)
