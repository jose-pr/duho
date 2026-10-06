from __future__ import annotations

import pathlib as _pathlib
import sys as _sys
import typing as _ty

from ._argsclass import Args
from ._meta import _AutoVersion, _C


class Cmd(Args):
    """An executable command: a data ``Args`` plus the command contract.

    ``Args`` is pure data -- a Namespace of parsed values, not
    required to run. ``Cmd`` adds the *executable* contract on top:
    ``__call__(self)`` is the command entrypoint (``instance()`` runs the
    command).

    The entrypoint is ``__call__`` -- a dunder -- deliberately. A ``Cmd``
    subclass's namespace is user-owned: annotated non-underscore attributes
    become CLI fields, so a plain method name like ``main`` would collide
    with a user field ``main: str`` (``--main``). ``__call__`` lives in the
    dunder namespace duho's field introspection already skips, so it can
    never clash with a declared flag.

    A ``Cmd`` subclass that does not override ``__call__`` raises
    ``NotImplementedError`` naming the class when dispatched -- the same
    loud-failure spirit as an earlier "missing ``__call__``". Data-only
    ``Args`` subclasses stay non-runnable by design: ``duho.main`` rejects them
    with a clear error rather than silently no-op'ing (the whole point of the
    split is that "runnable" is explicit).

    Base-order for the ``LoggingArgs`` mixin: ``class App(LoggingArgs, Cmd)``
    (data mixin first, executable base last). Both orders resolve the MRO
    correctly since ``LoggingArgs`` defines no ``__call__``; the recommended
    order reads "add logging to a command".
    """

    #: Own empty class-body-constants cache: ``Cmd``'s body declares no
    #: real CLI fields (only ``_passthrough_`` and ``__call__``), so seeding
    #: this skips AST-parsing this module for it. See ``Args._duho_constants_``.
    _duho_constants_: dict = {}

    #: argv captured after the first literal ``--`` separator (parse-time);
    #: an empty list when no ``--`` was present. Populated on the parsed
    #: instance by ``_initparser_``'s patched ``parse_known_args``.
    _passthrough_: list[str]

    #: ``False`` makes a non-empty ``--`` tail a usage error (exit 2) that
    #: names this command; ``True`` (default) captures it as ``_passthrough_``.
    #: Read from the command the parse selects, so a subcommand sets its own.
    _allow_passthrough_: bool = True

    #: On a group: the subcommand (name or alias) used when the first token
    #: that is not one of the group's own options names no subcommand.
    #: ``None`` (default) keeps the subcommand required. Read at parse time.
    _default_subcommand_: _ty.Optional[str] = None

    def __call__(
        self,
    ) -> _ty.Any:  # noqa: D401 - contract stub, overridden by subclasses
        """Run the command. Override ``__call__`` in a ``Cmd`` subclass.

        The base raises ``NotImplementedError`` naming the concrete class,
        so a ``Cmd`` that forgets to implement ``__call__`` fails loud when
        dispatched rather than silently doing nothing.
        """
        raise NotImplementedError(
            f"{type(self).__name__} is a Cmd but does not implement '__call__'"
        )

    @classmethod
    def _register_subcmd_(cls, child: _C) -> _C:
        """Attach ``child`` to THIS class's own ``_subcommands_`` tree.

        Appends ``child`` to a per-class list, materialized copy-on-write on
        first use: if ``_subcommands_`` is not set directly in ``vars(cls)``
        (i.e. it is unset or inherited from a parent ``Cli``), a fresh list
        is created -- never mutating a parent class's inherited list, so two
        ``Cli`` subclasses never cross-contaminate. An inherited
        ``_subcommands_`` seeds the fresh list (its children are kept, then
        ``child`` is added). Idempotent: if ``child`` is already present it
        is a no-op, so a child registered both statically (in a declared
        ``_subcommands_``) and via this API appears exactly once. Returns
        ``child`` so it can be used as a decorator.
        """
        if "_subcommands_" in vars(cls):
            current = cls._subcommands_
            existing = list(current) if current else []
        else:
            # Copy-on-write: seed from an inherited/unset value WITHOUT
            # mutating the parent's list.
            inherited = getattr(cls, "_subcommands_", None)
            existing = list(inherited) if inherited else []
        if child not in existing:
            existing.append(child)
        cls._subcommands_ = existing
        return child


class Cli(Cmd):
    """Application-root layer: an opt-in mixin over ``Cmd``.

    A leaf ``Cmd`` is lean -- it declares its own CLI fields and a
    ``__call__``. A ``Cli`` root is the *top* of an app and additionally
    exposes the app-wide, sandwich-named configuration attributes a plain
    ``Cmd`` does not declare (``--version``, shell completion, a config
    file, a subcommand tree). Opt in by subclassing ``Cli``::

        class MyApp(LoggingArgs, Cli):
            _version_ = "1.2.3"
            _completion_ = True

    ``Cli`` adds **no new runtime behavior for *running*** -- it inherits
    ``Cmd.__call__`` unchanged (a data-only ``Cli`` that never overrides
    ``__call__`` still fails loud when dispatched, exactly like a ``Cmd``).
    What it adds is two things:

    1. **Typed, documented app-root class attrs.** Every one of these is
       already read elsewhere via ``getattr(cls, "_x_", default)``
       (``duho.args``/``duho.runtime``), so declaring them here changes no
       reader -- it only gives them a typed home and a class-level default
       where one exists. A plain ``Cmd`` leaves them undeclared; a ``Cli``
       root is where they belong.
    2. **Self-registration** (``_register_subcmd_`` / ``@subcommand``): a
       leaf command file can attach itself to the root's subcommand tree
       instead of the root listing every child centrally in
       ``_subcommands_``. The two mechanisms compose (union + dedup).

    ``LoggingArgs`` stays orthogonal (a separate data mixin) -- the
    batteries-included recipe is ``class MyApp(LoggingArgs, Cli)`` (data
    mixin first, executable/root base last), NOT a forced bundle. Every
    OTHER member ``Cli`` adds is sandwich-named or dunder, so a ``Cli``
    subclass's field namespace stays user-owned (annotated non-underscore
    attrs still become CLI fields) -- with exactly ONE reserved, plain name:
    ``subcommand`` (the ``@Root.subcommand`` decorator). A CLI field named
    ``subcommand`` silently replaces the decorator instead of erroring, so
    avoid that one field name on a ``Cli`` subclass.
    """

    #: Own empty class-body-constants cache: every field ``Cli`` declares
    #: is sandwich-named (``_version_``, ``_completion_``, ...) and gets filtered
    #: out by ``get_clsargs`` anyway, so seeding this skips AST-parsing
    #: this module for ``Cli``. See ``Args._duho_constants_``.
    _duho_constants_: dict = {}

    #: ``--version`` string, the ``AUTO`` sentinel (resolve via
    #: ``importlib.metadata``), or ``None`` for no ``--version`` flag. Read by
    #: ``_resolve_version``.
    #:
    #: NOTE: every annotation on this class is written with ``typing.Union`` /
    #: ``typing.Optional`` and quoted, NEVER PEP-604 ``X | Y`` -- even sandwich-
    #: named fields are evaluated by ``typing.get_type_hints`` in
    #: ``_introspect.get_clsargs`` (before the ``_``-prefix filter drops them),
    #: so a ``|`` union would raise ``TypeError`` at parser-build time on 3.9.
    _version_: _ty.Optional[_ty.Union[str, _AutoVersion]] = None

    #: Distribution name override for ``_version_ = duho.AUTO`` when the import
    #: package differs from the PyPI distribution name. Read by
    #: ``_resolve_version``.
    _distribution_: _ty.Optional[str] = None

    #: When ``True``, inject ``--print-completion {bash,zsh,fish,powershell}``
    #: on the top-level parser. Read by ``_initparser_``;
    #: defaults off.
    _completion_: bool = False

    #: Path to a config file whose values become layered defaults (precedence
    #: CLI > env > config > class default). ``None`` disables it. A ``.json`` file
    #: is parsed as JSON, any other suffix (``.toml``/unspecified) as TOML. A
    #: path that does not exist YET is treated as no config at all (skipped,
    #: logged at debug) rather than raising -- an explicit ``config=`` kwarg to
    #: ``duho.main``/``duho.parse``/``duho.app`` stays strict. Read by
    #: ``_resolve_config_dict`` via ``_apply_layers``.
    _config_: _ty.Optional[_ty.Union[str, _pathlib.Path]] = None

    #: Name of an environment variable holding the config file's path. A
    #: non-empty value outranks ``_config_`` (an explicit ``config=`` and
    #: ``_config_field_`` outrank it); a path named this way must exist.
    _config_env_: _ty.Optional[str] = None

    #: Name of a declared field whose value is the config file's path, when the
    #: user gave it on the command line or through its own env var. It
    #: outranks ``_config_env_`` and ``_config_`` (only an explicit
    #: ``config=`` outranks it); a path named this way must exist. A name that
    #: is not a declared field is a ``ValueError`` naming the class.
    _config_field_: _ty.Optional[str] = None

    #: Optional custom config loader ``Callable[[Path], dict]``. When set it
    #: is used INSTEAD of duho's built-in JSON/TOML dispatch, so a user can plug a
    #: format duho does not ship (e.g. YAML via their own ``yaml.safe_load``)
    #: WITHOUT duho depending on it -- keeping the zero-runtime-deps contract.
    #: Read by ``_load_config`` via ``_resolve_config_dict`` /
    #: ``duho.app``.
    _config_loader_: _ty.Optional[_ty.Callable[[_pathlib.Path], dict]] = None

    #: Opt-in argparse help ``formatter_class``. ``None`` (default) uses
    #: argparse's plain formatter; set it to ``duho.DefaultsFormatter``,
    #: ``duho.ColorHelpFormatter``, ``duho.ColorDefaultsFormatter``, or any
    #: ``HelpFormatter`` subclass. Plumbed into ``formatter_class`` by
    #: ``Args._parser_`` (and inherited onto every subcommand parser).
    _help_formatter_: _ty.Optional[type] = None

    #: The static subcommand tree. ``None`` (the default) means "no declared
    #: subcommands"; self-registration lazily materializes a per-class list.
    #: Read via ``getattr(cls, "_subcommands_", None)`` (``duho.args`` +
    #: ``duho.runtime``) -- declaring it here does NOT change that contract.
    _subcommands_: _ty.Optional[_ty.Sequence[_ty.Type[Cmd]]] = None

    #: When ``True``, add the opt-in ``--help-agents`` flag (a detailed,
    #: machine-readable description of the whole CLI for AI agents). Read by
    #: ``_install_agent_help``; defaults off. Independent of the
    #: always-on ``AGENT_HELP``/``AGENTS_HELP`` env-var trigger, which needs
    #: no opt-in.
    _agent_help_: bool = False

    #: Environment variable whose truthy value flips ``--help`` into agent mode.
    #: ``None`` (default) checks every name in
    #: :data:`duho.agenthelp.DEFAULT_ENVS` (``AGENT_HELP`` and ``AGENTS_HELP``;
    #: either truthy triggers). Set explicitly to check exactly that one
    #: variable instead -- replaces both defaults, no aliasing. Read by
    #: ``_AgentHelpAction`` via ``agent_help_requested``.
    _agent_help_env_: _ty.Optional[str] = None

    #: Optional examples surfaced in the agent-help document. A sequence of
    #: command strings, or of ``(command, description)`` pairs. ``None`` (default)
    #: lets duho synthesize a minimal invocation line. Read by
    #: ``duho.agenthelp`` when building the document.
    _examples_: _ty.Optional[_ty.Sequence[_ty.Any]] = None

    #: Optional exit-code overrides/additions for the agent-help document, as a
    #: ``{code: meaning}`` mapping merged over duho's defaults (0/1/2). ``None``
    #: (default) uses the defaults alone. Read by ``duho.agenthelp``.
    _exit_codes_: _ty.Optional[_ty.Mapping[_ty.Any, str]] = None

    #: When ``True`` (the default), ``duho.main``/``duho.app`` call
    #: :func:`duho.utf8_stdio` FIRST thing -- before the MCP launch trigger,
    #: before ``argv`` is parsed, before anything else -- so piped/redirected
    #: stdout/stderr on a non-UTF-8 host locale (``cp1252`` on Windows) become
    #: UTF-8 and stop being able to crash on a non-ASCII character at all.
    #: ``False`` opts the app out entirely: duho leaves stdio completely
    #: alone, and the app may call :func:`duho.utf8_stdio` itself (with its
    #: own choice of streams/policy) or do nothing. Read via ``getattr``, so
    #: any class works, not just a ``Cli``. ``duho.main(..., utf8_stdio=...)``/
    #: ``duho.app(..., utf8_stdio=...)`` accept the same tri-state as
    #: ``mcp=``: an explicit ``True``/``False`` wins over this class
    #: attribute; ``None`` (the default kwarg value) defers to it.
    _utf8_stdio_: bool = True

    #: On the ROOT class of the tree being served, ``False`` disables the
    #: ``<PREFIX>MCP``/``<NAME>_MCP`` environment trigger (see
    #: ``duho.main``/``duho.app``'s own docs) for this app entirely -- the
    #: variable, if set, is left in ``os.environ`` untouched and a normal
    #: CLI run proceeds. Default ``True`` (the trigger is on by default).
    #: ``duho.app(..., mcp=False)`` does the same for one ``app()`` call,
    #: and wins over this class attribute when given. Independent of
    #: ``_mcp_command_`` below.
    #:
    #: On any OTHER (non-root) node in the tree -- a nested ``Cmd``/``Cli``
    #: reached as a subcommand -- ``_mcp_ = False`` means something
    #: different: it (and its whole subtree, if it has one) is left out of
    #: ``tools/list`` entirely, and ``tools/call`` on it (or on anything
    #: below it) raises the same "unknown tool" error as a nonexistent
    #: name, disclosing nothing. Read via plain ``getattr``, so a subclass
    #: of an excluded command is excluded too without redeclaring it. A
    #: module command supports the same opt-out via a module-level
    #: ``_mcp_ = False`` (see ``discovery.ModuleCommand``). The command
    #: registered under ``_mcp_command_``/``mcp_command=`` (an ``McpCmd``
    #: subclass) is excluded unconditionally regardless of this attribute.
    _mcp_: bool = True

    #: Opt-in built-in subcommand that serves this CLI as an MCP server,
    #: read by both ``duho.main`` and ``duho.app``. ``False`` (default): no
    #: subcommand. ``True``:
    #: registers ``duho.mcp.McpCmd`` under the name ``"mcp"``. A non-empty
    #: ``str``: registers it under that exact name instead (validated at
    #: build time: non-empty, no whitespace, not starting with ``"-"``; a
    #: name colliding with an existing command/alias, or no other
    #: subcommand existing at all, is a build-time ``ValueError``).
    #: ``duho.app(..., mcp_command=...)`` wins over this class attribute
    #: when given (including passing ``False`` to override a ``True``/
    #: ``str`` class default) -- ``duho.main`` has no such kwarg, so it
    #: always reads this attribute directly. Quoted ``Union`` (not PEP 604
    #: ``|``) per the module's 3.9-quoting rule for declared class attrs
    #: (see ``_version_`` above).
    _mcp_command_: _ty.Union[str, bool] = False

    #: Opt-in built-in subcommand that prints a shell completion script,
    #: read by both ``duho.main`` and ``duho.app`` through the same
    #: registration rules as ``_mcp_command_`` (same name validation, collision
    #: and "needs another subcommand" errors). ``False`` (default): none.
    #: ``True``: a subcommand named ``"completion"``; a non-empty ``str``
    #: names it. It takes one positional, the shell (``bash``, ``zsh``,
    #: ``fish`` or ``powershell``), and is never an MCP tool.
    _completion_command_: _ty.Union[str, bool] = False

    @classmethod
    def subcommand(cls, child: _C) -> _C:
        """Decorator form of :meth:`_register_subcmd_`.

        Lets a command file self-attach to the root::

            @MyApp.subcommand
            class Deploy(Cmd):
                ...

        Returns ``child`` unchanged, so the decorated class keeps its
        identity. Equivalent to calling ``MyApp._register_subcmd_(Deploy)``.
        """
        return cls._register_subcmd_(child)


def subcommand(parent: type[Cmd]) -> _ty.Callable[[_C], _C]:
    """Decorator factory: register the decorated class as a subcommand of ``parent``.

    ``parent`` is any ``Cmd`` (or ``Cli``) class acting as a group::

        @duho.subcommand(Tools)
        class Build(duho.Cmd):
            ...

    The decorated class is returned unchanged. Equivalent to
    ``parent._register_subcmd_(Build)``; registering twice is a no-op.
    """

    def register(child: _C) -> _C:
        return parent._register_subcmd_(child)

    return register


def command(
    args_cls: type[Args],
    func: _ty.Callable[[_ty.Any], object],
    *,
    name: _ty.Optional[str] = None,
    module: _ty.Optional[str] = None,
) -> type[Cmd]:
    """Build a ``Cmd`` subclass from a data ``Args`` class and a callable.

    Lets a user attach behavior to an existing data ``Args`` without
    rewriting it as a ``Cmd`` subclass ("build one from Args and a
    method"). The returned class subclasses BOTH ``args_cls`` (to inherit
    its declared fields / parsing machinery) and ``Cmd`` (for the
    executable contract). Its ``__call__`` calls ``func(self)`` -- the parsed
    instance IS the parsed args -- so ``command(MyArgs, f)`` makes ``f``
    receive the parsed ``MyArgs``-shaped instance and its return value becomes
    the command's result.

    ``name`` (optional) sets the built class's ``_parsername_`` (the
    subcommand name). When omitted, the usual
    ``_parsername_``/class-name rule applies to the generated class.

    ``module`` (optional): overrides the built class's ``__module__``.
    ``type(cls_name, bases, namespace)`` -- with no ``__module__`` in
    ``namespace`` -- otherwise makes Python fill it in from THIS function's
    OWN globals (``duho.args``), the same gotcha ``collections.namedtuple``/
    ``dataclasses.make_dataclass`` solve by resolving the CALLER's module via
    ``sys._getframe``. Left as ``duho.args``, this broke two things: (1)
    discovery's module-boundary filter keeps only classes whose ``__module__``
    equals the scanned module, so a ``command()``-built class in a discovered
    command file was silently never registered (no warning -- it looked like
    "not a command" the same way a helpers-only module does); (2)
    ``_version_ = duho.AUTO`` derives the lookup distribution from
    ``cls.__module__``, so it resolved to duho's OWN installed version instead
    of the app's. ``__qualname__`` is also set to a value that can never
    collide with a real ``ClassDef`` in the caller's source (AST-based
    introspection looks classes up by ``__module__`` + ``__qualname__``, and
    could otherwise misattribute a same-named sibling class's body to this
    synthesized one). ``_duho_constants_`` is seeded empty for the same
    reason ``Cmd``/``Cli`` seed it: without it, a bare
    `command()`-built class (whose ``__module__`` now points at the CALLER's
    real file) would still fall through to AST-parsing that file looking for
    a body this synthesized class never had.
    """
    if Cmd in getattr(args_cls, "__mro__", ()):
        bases: tuple = (args_cls,)
    else:
        bases = (args_cls, Cmd)

    def __call__(self, _func=func):
        return _func(self)

    cls_name = name or getattr(args_cls, "__name__", "Command")
    namespace: dict[str, object] = {
        "__call__": __call__,
        "_duho_constants_": {},
    }
    if name is not None:
        namespace["_parsername_"] = name

    resolved_module = module
    if resolved_module is None:
        try:
            frame = _sys._getframe(1)
        except (AttributeError, ValueError):  # pragma: no cover - no _getframe
            frame = None
        resolved_module = (
            frame.f_globals.get("__name__", __package__)
            if frame is not None
            else __package__
        )
    namespace["__module__"] = resolved_module
    # Never matches a real ClassDef -- `duho.command(...)` synthesizes this
    # class, it has no body of its own for AST introspection to find.
    namespace["__qualname__"] = f"command.<locals>.{cls_name}"
    namespace.setdefault("__doc__", args_cls.__doc__)

    return _ty.cast("type[Cmd]", type(cls_name, bases, namespace))
