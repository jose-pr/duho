import argparse as _argparse
import collections as _collections
import copy as _copy
import dataclasses as _dataclasses
import datetime as _datetime
import enum as _enum
import logging as _logging
import os as _os
import pathlib as _pathlib
import re as _re
import sys as _sys
import threading as _threading
import typing as _ty
import weakref as _weakref
from typing import Annotated as Arg

from . import _compat as _compat
from . import _introspect as _introspect
from . import logging as _duho_logging
from . import parsers as _parsers
from ._fieldspec import Factory as Factory
from ._fieldspec import UpdateAction as UpdateAction
from ._fieldspec import _bool_from_text as _bool_from_text
from ._fieldspec import _choice_checked as _choice_checked
from ._fieldspec import _factory_for as _factory_for
from ._fieldspec import _ISOFORMAT_FACTORIES as _ISOFORMAT_FACTORIES
from ._layers import _apply_default_layers_one as _apply_default_layers_one
from ._layers import _apply_layers as _apply_layers
from ._layers import _finalize_layers as _finalize_layers
from ._layers import _merge_layers_upward as _merge_layers_upward
from ._layers import _raw_config_values as _raw_config_values
from ._layers import _raw_env_values as _raw_env_values
from ._layers import _resolve_config_dict as _resolve_config_dict
from ._layers import _stage_layers as _stage_layers
from ._layers import _stash_layer_state as _stash_layer_state
from ._layers import value_sources as value_sources

_LOGGER = _logging.getLogger(__name__)

NOT_DEFINED = _introspect.NOT_DEFINED
_NONETYPE = type(None)

#: {id(instance): frozenset(explicitly-passed field names)} for every `Args`
#: instance built via `__init__`. `Args.__init__` seeds a class-body
#: default for every declared field the caller did NOT pass (so a directly
#: built instance has the same attribute surface as a parsed one, e.g. a bare
#: `bool` field materializes to `False`) -- those seeded placeholders must
#: never be mistaken for a value the caller actually supplied when
#: `duho.parse(instance)` decides what outranks env/config.
#:
#: Keyed by `id()`, not the instance itself: `argparse.Namespace` defines
#: `__eq__` (value equality over `vars()`) without a matching `__hash__`, so
#: Python makes every `Namespace`/`Args` instance UNHASHABLE by default --
#: a plain `WeakKeyDictionary` (which hashes its keys) cannot hold one.
#: `id()` is always hashable; `weakref.finalize` below drops the entry once
#: the instance is actually collected, so this never outlives it. Storing the
#: set here (rather than as a real attribute) also keeps it OUT of
#: `vars(instance)`, which would otherwise leak into the documented
#: `type(self)(**self._get_kwargs())` clone pattern and instance equality/repr.
_duho_explicit_instance_fields: "dict[int, frozenset]" = {}

if _ty.TYPE_CHECKING:
    from typing_extensions import Self as _Self  # type: ignore

    class _Parser(_argparse.ArgumentParser, _ty.Generic[_T]):
        """Type-checking-only view of the parser ``_parser_`` returns: an
        ``ArgumentParser`` whose ``parse_args``/``parse_known_args`` are typed
        as returning ``_T`` (the constructed ``Args``/``Cmd`` instance) instead
        of a bare ``Namespace``. Never instantiated -- every real parser is a
        plain ``argparse.ArgumentParser`` with ``parse_known_args`` patched in
        place (see ``Args._initparser_``); this class exists only so
        ``-> "_Parser[_Self]"`` return annotations and the one
        ``typing.cast("_Parser[_Self]", ...)`` describe that shape to a type
        checker, referenced exclusively through quoted annotations that are
        never evaluated at runtime."""

        def parse_args(self, args=None, namespace: "_T | None" = None) -> _T:  # type: ignore
            raise NotImplementedError()

        def parse_known_args(  # type: ignore
            self, args=None, namespace: "_T | None" = None
        ) -> tuple[_T, list[str]]:
            raise NotImplementedError()


_type = type

_T = _ty.TypeVar("_T")

#: Bound to :class:`Args`: lets ``duho.parse``/``duho.parse_globals`` return the
#: caller's own subclass instead of erasing it to ``Args``/``Any``.
_A = _ty.TypeVar("_A", bound="Args")

#: Bound to ``type[Cmd]``: lets ``Cli.subcommand``/``@Cli.subcommand`` and
#: ``Cli._register_subcmd_`` return the decorated CLASS's own type instead of
#: widening it to ``type[Cmd]``.
_C = _ty.TypeVar("_C", bound="type[Cmd]")

NS = _argparse.Namespace


class _AutoVersion:
    """Sentinel for ``_version_ = duho.AUTO``: resolve via importlib.metadata."""

    def __repr__(self) -> str:
        return "duho.AUTO"


AUTO = _AutoVersion()


class _MetaUnset:
    """Sentinel for a :class:`Meta` field left unset (never merged)."""

    def __repr__(self) -> str:
        return "duho.Meta.UNSET"


_META_UNSET = _MetaUnset()


@_dataclasses.dataclass
class Meta:
    """Typed, typo-safe alternative to ``NS(...)`` for field metadata (F5).

    ``NS(...)`` is an untyped ``argparse.Namespace``: a misspelled key
    (``NS(hlep="oops")``) is silently dropped. ``Meta`` declares the known
    metadata fields as a dataclass, so an unknown keyword is a ``TypeError`` at
    class-definition time -- the whole point. Only the fields you set are merged
    (each defaults to a private sentinel); every key ``NS`` accepts EXCEPT
    ``dest`` (see below) is also a ``Meta`` field, and ``NS`` keeps working
    forever.

    Use it exactly where ``NS`` goes::

        level: Arg[int, Meta(help="verbosity", env="LEVEL")] = 0
        ("--level",)

    Recommended over ``NS`` precisely because a typo fails loud instead of
    vanishing. The ``kwargs`` field is the same raw ``add_argument`` escape hatch
    ``NS(kwargs=...)`` provides.

    There is deliberately no ``dest`` field: an argument's ``dest`` is always
    its declared field name (the parsed instance attribute), so a ``dest=``
    override -- accepted, and silently ignored, by ``NS(dest=...)`` -- is a
    ``TypeError`` here instead of a value that looks honored but never is.

    ``flags`` is the typed, lint-clean way to give an explicit flag tuple
    (equivalent to the bare ``("-n", "--times")`` statement in the class body,
    which some checkers flag as an unused expression)::

        times: Arg[int, Meta(flags=("-n", "--times"))] = 1
    """

    help: "_ty.Any" = _META_UNSET
    env: "_ty.Any" = _META_UNSET
    conflicts: "_ty.Any" = _META_UNSET
    conflicts_required: "_ty.Any" = _META_UNSET
    group: "_ty.Any" = _META_UNSET
    action: "_ty.Any" = _META_UNSET
    nargs: "_ty.Any" = _META_UNSET
    const: "_ty.Any" = _META_UNSET
    default: "_ty.Any" = _META_UNSET
    choices: "_ty.Any" = _META_UNSET
    metavar: "_ty.Any" = _META_UNSET
    required: "_ty.Any" = _META_UNSET
    type: "_ty.Any" = _META_UNSET
    version: "_ty.Any" = _META_UNSET
    flags: "_ty.Any" = _META_UNSET
    kwargs: "_ty.Any" = _META_UNSET

    def _duho_options_(self) -> "dict[str, object]":
        """The explicitly-set metadata as a plain dict (unset fields omitted).

        Consumed by ``Args._getargs_`` in place of ``vars(self)`` so a
        sentinel-valued (never-set) field never overrides a type-derived kwarg.
        """
        return {k: v for k, v in vars(self).items() if v is not _META_UNSET}


def _top_level_dist_name(cls) -> str:
    """The distribution-lookup name for `cls` when no `_distribution_`
    override is given: normally the top-level import package
    (``cls.__module__.split('.')[0]``).

    When `cls` lives in a module run as ``python -m pkg``, ``cls.__module__``
    reads as the literal string ``"__main__"`` -- useless for
    ``importlib.metadata.version``, which then raises ``PackageNotFoundError``
    and silently drops ``--version`` even though the SAME app run through its
    installed console-script entry point resolves fine. ``runpy`` (which
    implements ``-m``) sets ``sys.modules["__main__"].__spec__.name`` to the
    real dotted module path (e.g. ``"pkg.__main__"``) even though ``__name__``/
    ``__module__`` themselves still read ``"__main__"`` -- recover the real
    top-level package from there instead.
    """
    module_name = cls.__module__
    if module_name == "__main__":
        main_module = _sys.modules.get("__main__")
        spec = getattr(main_module, "__spec__", None)
        spec_name = getattr(spec, "name", None)
        if spec_name:
            return spec_name.split(".")[0]
    return module_name.split(".")[0]


#: Process-lifetime cache for `duho.AUTO` version resolution, keyed by
#: distribution name: `_version_ = duho.AUTO` is re-resolved at every parser
#: build (root, each subcommand, every `duho.app` rebuild), but an installed
#: distribution's version cannot change mid-process, so repeating the
#: `importlib.metadata` filesystem scan buys nothing. Caches a `None` (not
#: found) result too, so a class using AUTO in a dev checkout is not
#: re-scanned on every build either.
_AUTO_VERSION_CACHE: "dict[str, str | None]" = {}


def _resolve_auto_version(dist: str) -> "str | None":
    """Resolve (and cache) ``importlib.metadata.version(dist)`` for
    ``_version_ = duho.AUTO``. Never raises.

    3.10+'s ``importlib.metadata`` normalizes a distribution
    name per PEP 503 (``.``/``-``/``_`` are equivalent); Python 3.9's does not
    treat ``.`` as a separator. A modern build backend (hatchling, recent
    setuptools) writes its ``*.dist-info`` directory with underscores, so a
    dotted ``_distribution_`` (the exact ``name`` from ``pyproject.toml``, e.g.
    a namespace-style ``"acme.tools"``) resolves on 3.10+ but not on 3.9. Retry
    once with ``-``/``_``/``.`` runs collapsed to a single ``_`` -- never
    REPLACE the verbatim lookup outright, since a legacy dotted
    ``acme.tools-X.dist-info`` directory only matches the verbatim form.
    """
    if dist in _AUTO_VERSION_CACHE:
        return _AUTO_VERSION_CACHE[dist]

    # Imported lazily (not at module top) so a plain `import duho` never pays
    # importlib.metadata's ~30 ms cost -- only a class that actually opts into
    # `_version_ = duho.AUTO` triggers the load, and only at parser-build time
    # (P1), once per distinct distribution name thanks to the cache above.
    import importlib.metadata as _importlib_metadata

    def _lookup(name: str) -> "str | None":
        try:
            return _importlib_metadata.version(name)
        except _importlib_metadata.PackageNotFoundError:
            return None

    version: "str | None" = None
    try:
        version = _lookup(dist)
        if version is None:
            import re as _re

            normalized = _re.sub(r"[-_.]+", "_", dist)
            if normalized != dist:
                version = _lookup(normalized)
    except Exception:
        _LOGGER.debug(
            "duho.AUTO: failed to resolve version for distribution %r",
            dist,
            exc_info=True,
        )
        version = None

    if version is None:
        _LOGGER.debug("duho.AUTO: distribution %r not found; skipping --version", dist)
    _AUTO_VERSION_CACHE[dist] = version
    return version


def _resolve_version(cls) -> "str | None":
    """Resolve a class's effective ``--version`` string, or None to skip it.

    ``_version_`` may be unset/None (no --version), an explicit str (used
    as-is), or the ``AUTO`` sentinel (resolved via importlib.metadata using
    ``_distribution_`` or the class's top-level import package -- see
    :func:`_top_level_dist_name`/:func:`_resolve_auto_version`). Never
    raises -- any resolution failure is logged at debug level and treated
    as "no version available".

    When ``_version_`` is unset/None, a class-level ``__version__`` string is
    used as a fallback (so an app that already carries the conventional
    ``__version__`` gets ``--version`` for free). ``_version_`` always wins when
    both are set; the ``__version__`` fallback accepts only a plain ``str``
    (not the ``AUTO`` sentinel).
    """
    raw = getattr(cls, "_version_", None)
    if raw is None:
        fallback = getattr(cls, "__version__", None)
        return fallback if isinstance(fallback, str) else None
    if isinstance(raw, str):
        return raw
    if raw is AUTO:
        dist = getattr(cls, "_distribution_", None) or _top_level_dist_name(cls)
        return _resolve_auto_version(dist)
    return None


#: A well-formed ``%(key)conversion`` mapping placeholder, or an already-
#: doubled ``%%`` literal -- the only two forms argparse's own ``%``-format
#: expansion (`text % dict(...)`) accepts once it is asked to format against
#: a MAPPING (which every call site here does): a bare `%s`/`%d` with no
#: `(key)` is invalid against a dict and raises just as badly as a stray `%`.
_PERCENT_PLACEHOLDER = _re.compile(r"%\([^)]*\)[a-zA-Z]|%%")


def _escape_stray_percent(text: str) -> str:
    """Escape a literal ``%`` to ``%%`` EXCEPT inside an already-valid
    ``%(key)s``-style placeholder (or an already-doubled ``%%``).

    Existing code (see :class:`duho.formatters.DefaultsFormatter`, which
    explicitly checks ``"%(default)" in help_text`` and leaves it alone) lets
    an author write a REAL ``%(default)s``/``%(prog)s`` placeholder directly
    in help/description text and have argparse expand it normally -- a
    blanket ``text.replace("%", "%%")`` would silently turn that intentional
    placeholder into inert literal text too, alongside the stray, crash-
    prone ``%`` (e.g. "50% of CPUs") this escaping exists to neutralize.
    Preserving already-valid placeholders and escaping everything else keeps
    both working.
    """
    if "%" not in text:
        return text
    pieces: "list[str]" = []
    pos = 0
    for match in _PERCENT_PLACEHOLDER.finditer(text):
        pieces.append(text[pos : match.start()].replace("%", "%%"))
        pieces.append(match.group(0))
        pos = match.end()
    pieces.append(text[pos:].replace("%", "%%"))
    return "".join(pieces)


def _escape_help(text: str) -> str:
    """Escape a literal ``%`` to ``%%`` for text handed to argparse as a
    ``help=`` string.

    argparse's ``HelpFormatter._expand_help`` (or, on 3.14+, ``add_argument``'s
    own ``_check_help``) always ``%``-formats an action/subcommand ``help=``
    string, so a literal ``%`` in docstring-derived help text (a field
    docstring, a class docstring's one-line subcommand summary, a module
    command's docstring) would otherwise crash parser BUILD on Python 3.14 or
    ``--help`` on 3.9. An explicit ``NS(help=...)``/``Meta(help=...)`` is
    applied AFTER this (via the builder-options setattr loop), so an author
    who already wrote a real ``%(default)s`` placeholder there is untouched
    either way; :func:`_escape_stray_percent` also leaves one written
    directly in a docstring alone.
    """
    return _escape_stray_percent(text)


def _escape_description(text: str) -> str:
    """Escape a literal ``%`` to ``%%`` for a ``description=`` ONLY when it
    contains a real ``%(prog)`` placeholder.

    Unlike ``help=``, argparse's ``HelpFormatter._format_text`` only
    ``%``-formats a ``description``/epilog when it literally contains the
    substring ``%(prog)`` -- an ordinary description is shown byte-for-byte.
    Escaping every description unconditionally (an earlier fix did) therefore
    made a literal ``%`` show up DOUBLED (``%%``) in ``--help`` for the common
    case; escaping only when the placeholder is actually present keeps both
    correct (and, via :func:`_escape_stray_percent`, keeps the ``%(prog)``
    placeholder ITSELF from also being escaped into inert text).
    """
    return _escape_stray_percent(text) if "%(prog)" in text else text


#: The shells `duho.print_completion`/`--print-completion` accept, in one
#: place -- both the argparse `choices=` for the CLI flag and the
#: standalone function's own validation read this, instead of duplicating the
#: tuple (and silently drifting) between the two call sites.
_COMPLETION_SHELLS = ("bash", "zsh", "fish", "powershell")


def _write_machine_text(text: str, file=None) -> None:
    """Write machine-consumed (non-prose) text -- a completion script, an
    agent-help JSON document -- as literal UTF-8 bytes, bypassing the text
    layer's newline translation and console code page.

    Thin alias kept under its original name (used at several call sites in
    this module); the actual implementation is the shared
    ``duho._compat.write_machine`` writer also used by ``duho.agenthelp`` and
    ``duho.mcp``, so every machine-readable output path agrees.
    """
    _compat.write_machine(text, file)


def _command_name(command) -> str:
    """A command's subcommand name: the one canonical rule shared by every
    reader that needs it (`Args._parser_`, `duho.runtime`, `duho.discovery`,
    `duho.mcp`, `LoggingArgs._logger_`).

    For a CLASS command, resolves its OWN ``_parsername_`` -- checked through
    ``vars(command)``, deliberately NOT ``getattr`` -- or, failing that, its
    class name. ``getattr`` follows the MRO, so it cannot distinguish "this
    class declares its own ``_parsername_``" from "this class merely
    inherited one from a base it subclasses". A framework-DERIVED name must
    never leak to a subclass this
    way (a subcommand and a subclass of it, registered as siblings, used to
    collapse onto one name the moment the base's own parser had been built
    once) -- and since duho no longer persists a derived name anywhere
    (`_parser_` computes it fresh every build, never writing it back), a
    subclass that wants to deliberately SHARE its base's name has to declare
    ``_parsername_`` on itself too; a bare, undecorated subclass always gets
    its own class name.

    For a MODULE command (a :class:`duho.discovery.ModuleCommand` instance,
    not a class), ``vars(command).get("_parsername_")`` finds it directly --
    `ModuleCommand.__init__` always sets it as a plain instance attribute
    (never inherited from anywhere), so the same own-``vars()`` read already
    does the right thing with no class/instance branch needed.
    """
    return vars(command).get("_parsername_") or getattr(command, "__name__", "")


#: Thread-local guard against a class appearing in its own (possibly
#: inherited) ``_subcommands_`` tree -- directly, or via a subcommand that
#: subclasses its own root. Keyed by `id(cls)` so it works for any
#: class regardless of hashability; cleared as each class finishes building
#: (`finally`), so it only ever reflects classes CURRENTLY being built in
#: this thread's current `_parser_()` call tree, never a class built by an
#: earlier, already-finished call.
_building_stack = _threading.local()


def _guard_recursive_build(cls):
    """Raise a clear ``TypeError`` if `cls` is already being built higher up
    in the current `_parser_()` call, else record it and return the
    set to remove it from when this build finishes.

    Without this, a subcommand that subclasses its own root (directly, or by
    inheriting a shared `_subcommands_` list that was never meant to include
    it) recurses into building itself as its own child forever, failing with
    a bare, unhelpful ``RecursionError``.
    """
    ids = getattr(_building_stack, "ids", None)
    if ids is None:
        ids = set()
        _building_stack.ids = ids
    if id(cls) in ids:
        raise TypeError(
            f"{cls.__name__!r} appears in its own _subcommands_ tree "
            f"(directly, or via a shared/inherited _subcommands_ list that "
            f"was not meant to include it); declare _subcommands_ only on "
            f"the class that should own it, or exclude {cls.__name__!r} "
            f"explicitly"
        )
    ids.add(id(cls))
    return ids


class _PrintCompletionAction(_argparse.Action):
    """argparse Action for --print-completion: emits a shell completion
    script for the *root* parser tree and exits 0, mirroring how the
    stdlib's own action="version" short-circuits before dispatch.

    ``root_parser`` is captured at injection time (the top-level parser
    built by this call to _parser_/_initparser_) rather than re-derived
    from ``parser`` at call time, since a subcommand's own parser only
    sees its own subtree, not the whole app.

    ``prog`` names the command the emitted script actually binds
    to. Completion scripts key on the root parser's ``prog`` (``_parsername_``
    or, failing that, the CLASS NAME), which almost never matches the
    installed command someone types (``MyApp`` vs. ``myapp``). When the root
    name is only that class-name fallback (``explicit_prog`` is ``False``),
    default ``prog`` to the stem of ``sys.argv[0]`` -- the command actually
    invoked -- instead of the misleading class name; an explicitly declared
    ``_parsername_``/``duho.app(name=...)`` always wins.
    """

    def __init__(
        self, option_strings, dest, root_parser=None, explicit_prog=True, **kwargs
    ):
        kwargs.setdefault("nargs", None)
        kwargs.setdefault("default", _argparse.SUPPRESS)
        super().__init__(option_strings, dest, **kwargs)
        self.root_parser = root_parser
        self.explicit_prog = explicit_prog

    def __call__(self, parser, namespace, values, option_string=None):
        from . import completion as _completion

        emitter = getattr(_completion, values)
        root = self.root_parser if self.root_parser is not None else parser
        prog = root.prog
        if not self.explicit_prog:
            argv0_stem = _pathlib.Path(_sys.argv[0]).stem
            if argv0_stem.endswith("-script"):
                argv0_stem = argv0_stem[: -len("-script")]
            if argv0_stem and argv0_stem != "__main__":
                try:
                    _completion._validate_prog(argv0_stem)
                except ValueError:
                    pass
                else:
                    prog = argv0_stem
        _write_machine_text(emitter(root, prog=prog), _sys.stdout)
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
        from . import agenthelp as _agenthelp

        if _agenthelp.agent_help_requested(self._duho_agent_env_):
            spec = _agenthelp.describe_parser(
                parser, root=True, root_cls=self._duho_agent_cls_
            )
            _compat.write_machine(_agenthelp.render(spec), _sys.stdout)
            parser.exit()
        # Human help: show only each field's CLASS default, never a
        # live env/config value `_stage_layers`/`_apply_default_layers_one`
        # may have already installed as `action.default` for THIS invocation
        # -- `DefaultsFormatter` only ever sees `action`, never `parser`, so
        # the class default + provenance is stashed onto each action here,
        # the one place in the print path that still has both.
        _agenthelp.stash_default_provenance(parser)
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
        from . import agenthelp as _agenthelp

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


class ArgumentMeta(_ty._ProtocolMeta):
    """Metaclass backing :class:`Argument`'s duck-typed ``isinstance`` check.

    An object satisfies :class:`Argument` simply by having a callable
    ``_argbuilder_`` -- no explicit subclassing/registration needed, so any
    custom type can opt in by defining that one classmethod.
    """

    def __instancecheck__(self, instance) -> bool:
        builder_factory = getattr(instance, "_argbuilder_", None)
        return callable(builder_factory)


@_ty.runtime_checkable
class Argument(_ty.Protocol, metaclass=ArgumentMeta):
    """The customization protocol for a field's declared type.

    Any object with a classmethod ``_argbuilder_(name, decl, factory=None) ->
    ArgumentBuilder`` satisfies this protocol (see :class:`ArgumentMeta`) and
    can be used as a field's type -- wrapped in ``Arg[CustomType, ...]``, or
    returned by :meth:`from_type` for a plain type -- to fully control how
    that field's :class:`ArgumentBuilder` is built. This is how duho's own
    type ladder (``int``, ``list[T]``, ``Literal[...]``, ...) is implemented,
    and the extension point a user-defined type (e.g. a ``Port`` value type)
    hooks into for the exact same treatment.
    """

    @classmethod
    def _argbuilder_(
        cls,
        name: str,
        decl: _introspect.ClsArgDeclaration,
        factory: "Factory | None" = None,
    ):
        """Build this field's :class:`ArgumentBuilder` from its declaration.

        ``name`` is the field name; ``decl`` is its
        ``_introspect.ClsArgDeclaration`` (annotation, default, docstring,
        flags expression); ``factory`` is an already-resolved text-to-value
        callable, or ``None`` to derive one from ``decl.type``/``cls``.
        Returns a freshly built ``ArgumentBuilder`` -- ``NS(...)``/
        ``Meta(...)`` metadata is applied by the CALLER afterward (see
        :func:`_apply_argument_options`), not here.
        """
        # Escape a literal `%` in the field's docstring -- argparse
        # `%`-expands every action's `help=` unconditionally (crashing parser
        # BUILD on 3.14, `--help` on 3.9). An explicit `NS(help=...)`/
        # `Meta(help=...)` overrides this via the builder-options setattr
        # loop (see `Argument.from_type`/`_apply_argument_options`), applied
        # AFTER this, so it is never double-escaped.
        help = _escape_help(decl.docstring or "")
        flags_expr = next(
            filter(lambda x: isinstance(x, (list, tuple, set)), decl.exprs),
            None,
        )
        if isinstance(flags_expr, set):
            # A set has no defined iteration order, so `flags[0]` (positional
            # detection) is nondeterministic and previously crashed. Reject it
            # with a clear build-time error naming the field.
            raise ValueError(
                f"argument {name!r}: flags must be given as a list or tuple, "
                f"not a set {flags_expr!r} (a set has no guaranteed order)"
            )
        flags = (
            flags_expr if flags_expr is not None else ("--" + name.replace("_", "-"),)
        )
        required = None
        choices = None
        metavar = None
        action = None
        nargs = None
        collection = None
        default = decl.default
        ty = decl.type
        if ty is NOT_DEFINED:
            ty = factory if isinstance(factory, type) else cls

        if factory is None:
            _factory = decl.type if decl.type is not NOT_DEFINED else cls
        else:
            _factory = _ty.cast(Factory, factory)

        cls = ty
        if cls is not None and cls is not Argument:
            origin = _ty.get_origin(cls)
            args = _ty.get_args(cls)
            # An Optional[...] (Union carrying None) is not required.
            if origin in _compat.UNION_ORIGINS and _NONETYPE in args:
                required = False

            # One dispatch ladder, shared by the top level and every Union member.
            # A plain/custom type yields factory=None -- keep the seeded
            # `_factory` (e.g. a `from_type` custom factory) for that case.
            spec = _factory_for(cls, name)
            if spec.factory is not None:
                _factory = spec.factory
            if spec.choices is not None:
                choices = spec.choices
            if spec.metavar is not None:
                metavar = spec.metavar
            if spec.action is not None:
                action = spec.action
            implicit_nargs = False
            if spec.nargs is not None:
                # `_factory_for`'s `list`/`set`/`tuple` branch hardcodes
                # `nargs="*"`, correct for a POSITIONAL (a trailing variadic
                # positional is exactly the point of that shape) but not for
                # an OPTION, which defaults to ONE value per occurrence
                # (`-f a -f b`) rather than space-separated multi-value in one
                # occurrence (`-f a b`). Deciding that here -- before an
                # explicit `NS(nargs=...)`/`NS(flags=...)` override has even
                # been applied (`Argument.from_type`'s `setattr` loop runs
                # AFTER this method returns) -- baked in the type-derived
                # shape too early: the documented `NS(nargs="*")` opt-back and
                # a `NS(flags=...)` that makes the field positional were both
                # unable to change it. The downgrade itself now lives
                # in `ArgumentBuilder._kwargs`, computed from the FINAL flags
                # and nargs once every override is known; here we only record
                # that this `nargs` came from the type ladder (not a user
                # override) via `implicit_nargs`, so `_kwargs` can tell the
                # two cases apart.
                nargs = spec.nargs
                implicit_nargs = True
            if spec.collection is not None:
                collection = spec.collection
            if spec.default is not NOT_DEFINED and default is NOT_DEFINED:
                default = spec.default

        return ArgumentBuilder(
            name=name,
            flags=flags,
            type=_factory,
            default=default,
            help=help,
            required=required,
            choices=choices,
            metavar=metavar,
            action=action,
            nargs=nargs,
            collection=collection,
            _implicit_nargs_=implicit_nargs,
        )

    @classmethod
    def from_type(cls, factory: _ty.Callable[[str], _T], **kwargs):
        """Wrap a plain type/text-factory as an :class:`Argument`.

        Returns an ``Argument`` subclass whose ``_argbuilder_`` builds via
        ``cls``'s own (``Argument``'s base) logic using ``factory``, then
        applies ``**kwargs`` (the ``NS(...)``/``Meta(...)`` metadata dict) onto
        the result via :func:`_apply_argument_options`. Every plain-typed
        field (i.e. one not ALREADY a custom :class:`Argument`) is built
        through this single path -- see ``Args._getargs_``.
        """
        _factory = factory

        class Arg(cls):

            @classmethod
            def _argbuilder_(
                cls,
                name: str,
                decl: _introspect.ClsArgDeclaration,
                factory: "Factory | None" = _factory,
            ):
                builder = super()._argbuilder_(name, decl, factory or _factory)
                _apply_argument_options(builder, kwargs)
                return builder

        return Arg


def _apply_argument_options(builder: "ArgumentBuilder", options: dict) -> None:
    """Apply ``NS(...)``/``Meta(...)`` metadata onto an already-built
    ``ArgumentBuilder``.

    The post-processing :meth:`Argument.from_type`'s wrapper applies to a
    plain-type field's builder, factored out so it can ALSO be applied to a
    CUSTOM :class:`Argument` type's own builder (built by that type's own
    ``_argbuilder_``, not through ``from_type`` at all) when it is used
    inside ``Arg[CustomType, NS(...)]`` -- see ``Args._getargs_``. Without
    this, wrapping a custom type in ``Arg[...]``/``Meta(...)`` (the
    documented way to attach ``help=``/``env=`` to ANY field, custom types
    included) silently discarded the type's own ``_argbuilder_`` override and
    replaced it with the type used as a bare, uncustomized `type=` factory.
    """
    for k, v in options.items():
        setattr(builder, k, v)
    if "nargs" in options:
        # An explicit NS(nargs=...)/Meta(nargs=...) override wins outright --
        # clear the "came from the type ladder" marker so `_kwargs` never
        # downgrades it back.
        builder._implicit_nargs_ = False
    if builder.split is not None:
        # duho.Extend(): compose the split function with the field's OWN
        # element factory (already resolved onto `builder.type` by the
        # type's own `_argbuilder_` above) rather than replacing it outright,
        # so a typed collection (e.g. `list[int]`) still converts each split
        # part, and the natural collection action (list/set/tuple) still runs
        # unmodified.
        splitter = builder.split
        base = builder.type

        def _extend_factory(text, _splitter=splitter, _base=base):
            return [_base(part) for part in _splitter(text)]

        _extend_factory._duho_extend_base_ = base
        builder.type = _extend_factory


#: Actions argparse forbids from receiving `type=`.
_TYPE_INCOMPATIBLE_ACTIONS = frozenset(
    {
        "store_true",
        "store_false",
        "store_const",
        "append_const",
        "count",
        "help",
        "version",
        _argparse.BooleanOptionalAction,
    }
)

#: Actions that require `const=` to be supplied.
_CONST_REQUIRED_ACTIONS = frozenset({"store_const", "append_const"})

#: Zero-argument (flag-only) actions with no declared default get an
#: implicit one instead of `required=True` -- argparse's own natural
#: resting value for each, so `_effective_default_` agrees.
_ZERO_ARG_ACTION_DEFAULTS = {
    "count": 0,
    "store_const": None,
    "append_const": None,
    "store_false": True,
}


def _is_positional(flags: "_ty.Sequence[str]") -> bool:
    """A flag tuple whose sole entry has no leading ``-`` is a positional.

    The one place this decision is made -- ``ArgumentBuilder._kwargs``
    and the ``is_positional`` property below both call this, instead of each
    re-deriving ``len(flags) == 1 and not flags[0].startswith("-")``
    independently (and, before this fix, disagreeing with a THIRD copy in
    ``duho.mcp``).
    """
    return len(flags) == 1 and not flags[0].startswith("-")


class ArgumentBuilder(_argparse.Namespace):
    """One declared field's build state: raw ``add_argument`` kwargs plus
    duho's own metadata (``collection``/``split``/``env``/``conflicts``/...).

    Built by :meth:`Argument._argbuilder_` (via :meth:`Argument.from_type` for
    a plain type) from a field's ``_introspect.ClsArgDeclaration``, then
    post-processed with any ``NS(...)``/``Meta(...)`` metadata
    (:func:`_apply_argument_options`). One instance per declared field,
    cached on the owning class (``Args._getargs_``). :meth:`_kwargs` resolves
    it to the exact keyword arguments :meth:`add_to_parser` passes to
    ``parser.add_argument``; the env/config layering
    (``_raw_env_values``/``_raw_config_values``/``convert_layered``) reads it
    to convert a non-CLI value the same way a CLI occurrence would.
    """

    name: str
    flags: list[str]
    type: Factory
    default: "None | object | _introspect.NotDefined"
    help: str
    required: "bool | None" = None
    action: "str | type[_argparse.Action] | None" = None
    nargs: "str|int|None" = None
    choices: "_ty.Sequence | None" = None
    metavar: "str | None" = None
    const: "object | _introspect.NotDefined" = NOT_DEFINED
    version: "str | None" = None
    env: "str | None" = None
    #: ``NS(conflicts=...)``/``Meta(conflicts=...)``'s mutually-exclusive-group
    #: key; ``None`` for a field in no group. Declared here (rather than read
    #: via ``getattr(..., "conflicts", None)``) so every consumed metadata key
    #: has ONE declaration, matching ``Meta``'s own field list.
    conflicts: "str | None" = None
    #: Whether THIS member's group must be satisfied (``NS(conflicts_required=True)``);
    #: a group is required if ANY of its members sets this.
    conflicts_required: bool = False
    #: ``NS(group=...)``/``Meta(group=...)``'s titled-argument-group heading;
    #: ``None`` puts the field directly on the parser/container instead.
    group: "str | None" = None
    #: The raw ``add_argument`` escape hatch (``NS(kwargs={...})``/
    #: ``Meta(kwargs={...})``), applied LAST in :meth:`_kwargs` so it wins over
    #: every field-derived kwarg, including duho's own ``dest``.
    kwargs: "_ty.Mapping[str, object] | None" = None
    #: For a collection field (``list``/``set``/``tuple``) the target collection
    #: type; ``None`` for a scalar field. Recorded at build time so a layered
    #: (env/config) value converts to the SAME collection a CLI occurrence would
    #: produce (see :meth:`convert_layered`). ``self.type`` is then the *element*
    #: factory, not the collection factory.
    collection: "_type | None" = None
    #: ``duho.Extend()``'s split callable, or ``None``. Consumed by
    #: `Argument.from_type`'s wrapper to compose a text-splitting factory with
    #: the field's own element type; never read afterwards.
    split: "_ty.Callable | None" = None
    #: True when `nargs` came from the type ladder (a `list`/`set`/`tuple`
    #: field) rather than an explicit `NS(nargs=...)` override. Lets
    #: `_kwargs` downgrade a repeatable OPTION to one value per occurrence
    #: without also clobbering a deliberate opt-back into space-separated
    #: multi-value.
    _implicit_nargs_: bool = False

    @property
    def is_positional(self) -> bool:
        """True when this field's final, post-override flags are positional."""
        return _is_positional(self.flags)

    @property
    def is_bare_bool_flag(self) -> bool:
        """True when this field compiles to a bare ``store_true``/
        ``BooleanOptionalAction`` flag with no value of its own -- as opposed
        to a `bool` that carries `choices` (a `Literal[True, False]` field,
        which must go through `type=`+`choices=` like any other `Literal`) or
        one with an explicit `action=` override.
        """
        return self.type is bool and not self.action and self.choices is None

    #: Truthy/falsy strings a layered bool value maps to True/False
    #: (case-insensitive, whitespace-stripped). The one shared table
    #: (``_compat.BOOL_TRUE``/``BOOL_FALSE``) aliased here so
    #: existing readers of ``ArgumentBuilder._BOOL_TRUE``/``_BOOL_FALSE`` keep
    #: working. Unlike ``Env.bool`` (which treats an unrecognized string as
    #: False) the layered converter is STRICT -- an explicit config/env value
    #: that parses to neither is a user error, not a silent False.
    _BOOL_TRUE = _compat.BOOL_TRUE
    _BOOL_FALSE = _compat.BOOL_FALSE

    def _convert_single(self, raw):
        """Convert one raw scalar (env string / TOML-typed value) to the field type.

        A string always runs through ``self.type`` (the CLI text factory), so a
        bad value raises exactly the error argparse would. A non-string raw
        (TOML int/float/bool/date/list-element) goes through
        :meth:`_convert_non_str`.
        """
        factory = self.type
        if isinstance(raw, str):
            return factory(raw)
        return self._convert_non_str(raw, factory)

    def _convert_non_str(self, raw, factory):
        """Shared lossless-widening rule for a non-string raw value.

        Used by both :meth:`_convert_single` (``self.type``) and
        :meth:`convert_layered`'s dict-table branch (the per-value factory,
        which is NOT ``self.type`` there -- ``self.type`` is the
        ``_KVFactory`` wrapper) -- so a ``dict[str, V]`` table value widens
        exactly like a scalar ``V`` field would, instead of skipping this
        rule entirely.

        A raw value already an instance of the factory's type is kept as-is
        (``timeout: float`` receiving TOML int ``30`` widens to ``30.0``
        below, not here, since ``30`` is not already a ``float``). Otherwise:

        * ``bool`` is rejected for any factory except an actual bool
          factory -- ``bool`` subclasses ``int``, so ``isinstance(True, int)``
          is ``True``; without this check a TOML ``port = true`` silently
          stayed ``True`` in an ``int`` field.
        * ``list``/``tuple``/``dict``/``set``/``frozenset`` is rejected for a
          scalar field -- previously ``str(["a", "b"])`` silently stringified
          a list instead of rejecting it (the factory call itself never
          raises for ``str``).
        * ``float`` -> ``int`` is rejected when it has a fractional part
          (``int(1.5)`` truncates instead of erroring); the reverse (``int``
          widening to ``float``) is always lossless and stays allowed via the
          final factory-call fallback.
        * anything else: the factory itself decides, and a ``TypeError`` (a
          factory that flatly cannot accept a non-string, e.g.
          ``date.fromisoformat``) keeps the raw value unchanged -- documented,
          deliberate behavior, not a bug (a native TOML/JSON date in a date
          field, for example).
        """
        if isinstance(raw, bool):
            if factory is bool or factory is _bool_from_text:
                return raw
            raise ValueError(
                f"{raw!r} is a boolean but the field expects "
                f"{getattr(factory, '__name__', factory)!r}"
            )
        if isinstance(raw, (list, tuple, dict, set, frozenset)):
            raise ValueError(
                f"{raw!r} is a {type(raw).__name__}, which cannot widen to "
                f"{getattr(factory, '__name__', factory)!r}"
            )
        if isinstance(factory, type) and isinstance(raw, factory):
            return raw
        if isinstance(raw, float) and factory is int:
            if not raw.is_integer():
                raise ValueError(
                    f"{raw!r} has a fractional part; converting to int "
                    f"would lose precision"
                )
            return int(raw)
        try:
            return factory(raw)
        except TypeError:
            return raw

    def _check_layered_choices(self, value):
        """Validate a converted layered/instance value against ``self.choices``:
        ``set_defaults`` bypasses argparse's own choices check
        entirely, so a Literal/``Choice(...)`` field silently accepted any
        env or config text without this. Checked per element for a
        collection, per value for a dict; an Enum field never carries
        ``self.choices`` (its factory already validates by member name), so
        it is unaffected.
        """
        if self.choices is None:
            return
        if self.collection is dict:
            candidates = value.values()
        elif self.collection is not None:
            candidates = value
        else:
            candidates = (value,)
        for v in candidates:
            if v not in self.choices:
                raise ValueError(
                    f"invalid choice: {v!r} (choose from "
                    f"{', '.join(map(repr, self.choices))})"
                )

    def convert_layered(self, raw, *, source: str):
        """Convert a raw env/config *layer* value to this field's Python value.

        The env/config layers feed ``parser.set_defaults`` directly, bypassing
        argparse's own ``type=``/``action=`` handling -- so a layered value must
        be converted here to match what CLI parsing of the same field yields.
        Three field shapes are handled:

        * **bool** (``self.type is bool`` or a store_true/BooleanOptionalAction
          effective action): real bools pass through; strings map via the
          strict :data:`_BOOL_TRUE`/:data:`_BOOL_FALSE` sets (unknown -> error).
        * **collection** (``self.collection`` set): a *string* raw becomes a
          single element wrapped in the collection (``FILES=a.txt`` ->
          ``["a.txt"]``, matching one CLI occurrence); a *list/tuple/set* raw
          (a TOML array) converts element-wise then coerces to the collection.
        * **scalar**: via :meth:`_convert_single`.

        The result is checked against ``self.choices`` before it is returned,
        the same membership check argparse itself would apply to a
        CLI-supplied value.

        ``source`` names the layer ("env"/"config") for error messages; the
        calling resolver wraps any ``ValueError``/``TypeError`` with the field
        and variable name.
        """
        is_bool = (
            self.type is bool
            or self.action in ("store_true", "store_false")
            or self.action is _argparse.BooleanOptionalAction
        )
        if is_bool:
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, str):
                low = raw.strip().lower()
                if low in self._BOOL_TRUE:
                    return True
                if low in self._BOOL_FALSE:
                    return False
                raise ValueError(
                    f"{raw!r} is not a valid boolean "
                    f"(expected one of {sorted(self._BOOL_TRUE | self._BOOL_FALSE)})"
                )
            raise ValueError(f"cannot interpret {raw!r} ({source}) as a boolean")

        if self.collection is dict:
            # A dict field: a *string* raw ("k=v") runs the KV factory (one-pair
            # dict); a TOML *table* (Mapping) converts each value through the
            # value factory (strings only; already-typed TOML values pass
            # through, matching :meth:`_convert_single`).
            if isinstance(raw, _ty.Mapping):
                value_factory = getattr(self.type, "value_factory", str)
                result: dict = {}
                for k, v in raw.items():
                    if isinstance(v, str):
                        result[str(k)] = value_factory(v)
                    else:
                        result[str(k)] = self._convert_non_str(v, value_factory)
                self._check_layered_choices(result)
                return result
            value = self._convert_single(raw)
            self._check_layered_choices(value)
            return value

        if self.collection is not None:
            extend_base = getattr(self.type, "_duho_extend_base_", None)
            if extend_base is not None:
                # duho.Extend(): `self.type` SPLITS one string into several
                # elements rather than converting a single one, so it must
                # NOT be run once per array element like a plain per-element
                # factory would -- a *string* raw is the whole thing
                # to split; a *list/tuple/set* raw (a TOML array) splits each
                # STRING element and flattens the parts together, widening a
                # non-string element (already fully typed) via the base
                # (per-element) factory instead.
                if isinstance(raw, str):
                    value = self.collection(self.type(raw))
                elif isinstance(raw, (list, tuple, set)):
                    parts: list = []
                    for e in raw:
                        if isinstance(e, str):
                            parts.extend(self.type(e))
                        else:
                            parts.append(self._convert_non_str(e, extend_base))
                    value = self.collection(parts)
                else:
                    value = self.collection([self._convert_non_str(raw, extend_base)])
            elif isinstance(raw, (list, tuple, set)):
                value = self.collection(self._convert_single(e) for e in raw)
            else:
                value = self.collection([self._convert_single(raw)])
            self._check_layered_choices(value)
            return value

        value = self._convert_single(raw)
        self._check_layered_choices(value)
        return value

    def _kwargs(self, *, layered: bool = False):
        # NS(kwargs={...}) is the raw escape-hatch override: it must win over
        # every field-derived kwarg (explicit NS(field=...) loses to it), so
        # field derivation writes into `kwargs` first and the raw overrides
        # are applied last, on top.
        overrides = dict(self.kwargs or {})
        kwargs: dict = {}

        positional = self.is_positional
        nargs = self.nargs
        # A repeatable OPTION's collection nargs="*" (from the type ladder,
        # not a user override -- `_implicit_nargs_`) defaults to ONE value
        # per occurrence (`-f a -f b`), not space-separated multi-value in
        # one occurrence (`-f a b`). Computed here, from the FINAL flags and
        # nargs once every NS(nargs=...)/NS(flags=...) override is already
        # applied (both land on `self` before `_kwargs` ever runs), so the
        # documented `NS(nargs="*")` opt-back and a `NS(flags=...)` override
        # that makes the field positional both work. `_CollectionAction`
        # (bound to list/set/tuple/frozenset alike) already has its
        # own single-value-per-occurrence branch, so no action swap is needed.
        if self._implicit_nargs_ and nargs == "*" and not positional:
            nargs = None
        if nargs is not None:
            kwargs["nargs"] = nargs

        if self.choices is not None:
            kwargs["choices"] = self.choices

        if self.metavar is not None:
            kwargs["metavar"] = self.metavar

        if self.default is not NOT_DEFINED:
            kwargs["default"] = self.default

        if self.is_bare_bool_flag:
            # A bare bool becomes a store_true/BooleanOptionalAction flag. A
            # `Literal[True, False]` (carries choices) or an explicit
            # action= override is excluded by `is_bare_bool_flag` -- those go
            # through type=+choices= like any other Literal, since argparse
            # forbids choices= on a store_true action.
            no_flag = any(
                f.startswith("--no-") for f in self.flags if f.startswith("--")
            )
            if self.default is True:
                if no_flag:
                    # BooleanOptionalAction tries to synthesize a --no-<flag>
                    # pair for a flag that ALREADY starts with --no- -- 3.14+
                    # rejects that outright, and 3.9-3.13 built the confusing
                    # --no-verify/--no-no-verify pair. A plain
                    # store_false under the SAME flag means what a
                    # True-default --no-* flag always meant: presence sets
                    # False, absence keeps the True default.
                    kwargs["action"] = "store_false"
                else:
                    kwargs["action"] = _argparse.BooleanOptionalAction
            elif layered:
                # A field that can receive True from a layer OTHER than the
                # CLI (env=, or the owning class has a config source) needs a
                # way to turn it back off from the command line -- store_true
                # can only ever SET True, never re-assert False.
                kwargs["action"] = _argparse.BooleanOptionalAction
            else:
                kwargs["action"] = "store_true"
        if self.action:
            kwargs["action"] = self.action

        # Resolve the *effective* action (raw override wins) so the
        # type-incompatibility / const / version guards below key off what
        # will actually be sent to add_argument, not the pre-override value.
        action = overrides.get("action", kwargs.get("action"))

        if action is _argparse.BooleanOptionalAction:
            # Python 3.14 removed the (already-deprecated) type/choices/
            # metavar parameters outright -- drop them here, not only
            # when duho itself picked the action, so an explicit
            # NS(action=argparse.BooleanOptionalAction) override is covered
            # too.
            kwargs.pop("metavar", None)
            kwargs.pop("choices", None)

        if action not in _TYPE_INCOMPATIBLE_ACTIONS:
            kwargs["type"] = self.type
        else:
            kwargs.pop("type", None)

        if action == "store_true" and "default" not in kwargs:
            kwargs["default"] = False
        if action is _argparse.BooleanOptionalAction and "default" not in kwargs:
            kwargs["default"] = False

        if action == "append" and self.collection not in (None, list):
            # duho.Append() forces argparse's stdlib "append" action, which
            # always produces a *list* -- it doesn't compose with a set/tuple
            # field's own collection action. Fail loud at build time
            # instead of silently returning the wrong collection type.
            raise ValueError(
                f"argument {self.name!r}: duho.Append() does not support a "
                f"{self.collection.__name__} field (it always produces a "
                f"list); a repeatable {self.collection.__name__} field "
                f"already accumulates one value per occurrence without it"
            )

        if action in _CONST_REQUIRED_ACTIONS:
            const = (
                self.const
                if self.const is not NOT_DEFINED
                else overrides.get("const", NOT_DEFINED)
            )
            if const is NOT_DEFINED:
                raise ValueError(
                    f"argument {self.name!r}: action={action!r} requires const="
                )
            kwargs["const"] = const
        elif self.const is not NOT_DEFINED:
            kwargs["const"] = self.const

        if action == "version":
            version = (
                self.version if self.version is not None else overrides.get("version")
            )
            if version is not None:
                kwargs["version"] = version

        dest = self.name
        if positional:
            dest = None

        if dest:
            kwargs["dest"] = dest

        if positional:
            if self.choices is not None and kwargs.get("nargs") == "*":
                # A variadic (nargs="*") positional with `choices=` -- e.g. a
                # `list[T]` positional through `Choice()`/`NS(choices=...)`,
                # or a `list[Literal[...]]` positional (whose element choices
                # bubble up to the field spec too). argparse itself (through
                # 3.13, bpo-9625) validates the DEFAULT against `choices` too
                # whenever the positional is omitted -- and does so with the
                # raw default object, so even `default=SUPPRESS` gets checked
                # against `choices` and fails (worse than the plain empty
                # list). Move the membership check into the element factory
                # instead (mirrors the enforcement a Union/Literal member
                # already gets, see `_choice_checked`) and drop `choices=`
                # from `add_argument` for this shape entirely -- so argparse
                # never validates anything itself here, on any version.
                # `metavar` still shows the allowed values.
                if "type" in kwargs:
                    kwargs["type"] = _choice_checked(kwargs["type"], self.choices)
                kwargs.pop("choices", None)
                if kwargs.get("metavar") is None:
                    kwargs["metavar"] = (
                        "{" + ",".join(str(c) for c in self.choices) + "}"
                    )
            elif "nargs" not in kwargs and (
                "default" in kwargs or self.required is False
            ):
                # An optional positional (a real default, or an Optional[T]
                # with none at all) needs nargs="?", otherwise
                # argparse makes it required and ignores the default.
                kwargs["nargs"] = "?"
            kwargs.pop("required", None)
        elif action in ("version", "help"):
            # argparse's _VersionAction / _HelpAction don't accept required=.
            pass
        elif self.required is not None:
            kwargs["required"] = self.required
        elif dest is not None:
            if self.conflicts:
                # A mutually-exclusive member with no explicit required= and
                # no default: argparse forbids a required member inside a
                # mutex group ("mutually exclusive arguments must be
                # optional"), so this can never become `required=True` here
                # -- group-level requiredness is exactly what
                # `conflicts_required=` expresses instead.
                kwargs["required"] = False
            elif action in _ZERO_ARG_ACTION_DEFAULTS and "default" not in kwargs:
                # A flag-style zero-argument action (count/store_const/
                # append_const/store_false) with no declared default gets
                # argparse's own natural resting value instead of becoming a
                # mandatory flag.
                kwargs["required"] = False
                kwargs["default"] = _ZERO_ARG_ACTION_DEFAULTS[action]
            else:
                kwargs["required"] = "default" not in kwargs

        kwargs.update(overrides)

        # Copy a mutable default so each parser build gets its OWN list/set/
        # dict (the builder is cached on the class, so without this every
        # build would share the same object). A second, per-PARSE copy (for
        # a parser reused across multiple parse_args() calls) happens
        # in `_initparser_`'s wrapped `parse_known_args`. Covers both the
        # collection-branch default ([]/set()) and an override default (e.g.
        # an explicit Extend(sep, default=[...])). Tuples are immutable.
        default_value = kwargs.get("default")
        if isinstance(default_value, (list, set, dict)):
            kwargs["default"] = _copy.copy(default_value)

        return kwargs

    def add_to_parser(self, parser: _argparse.ArgumentParser, *, layered: bool = False):
        """Call ``parser.add_argument(*self.flags, ...)`` for this field.

        ``layered=True`` when an env/config layer can ALSO supply a value for
        this field (see :meth:`_kwargs`'s ``layered`` parameter -- it changes
        a bare bool field's action so the CLI can still turn a layer-supplied
        ``True`` back off). Returns the ``argparse.Action`` that was added.
        """
        help = self.help
        if callable(help):  # type: ignore
            help = help()
        kwargs = self._kwargs(layered=layered)
        action = parser.add_argument(*self.flags, help=help, **kwargs)
        if isinstance(action, _argparse.BooleanOptionalAction):
            # 3.9/3.10's BooleanOptionalAction.__init__ unconditionally
            # appends " (default: %(default)s)" to any non-None help
            # (removed in 3.11) -- reset to the exact help duho passed in so
            # an NS(help=argparse.SUPPRESS) flag stays hidden (the identity
            # check argparse itself uses to hide it) and no literal
            # "%(default)s" leaks into agent-help JSON on the floor versions.
            action.help = help
        return action

    def _effective_default_(self):
        """The value argparse would leave this field at when not supplied.

        Reuses ``_kwargs()`` so it agrees exactly with what ``add_to_parser``
        registers (e.g. a ``store_true`` bool resolves to ``False`` even when no
        ``default`` was declared). Returns :data:`NOT_DEFINED` for a required
        field with no default -- callers seeding an instance leave those unset.
        A non-required OPTION with no declared default (e.g. ``Optional[int]``)
        resolves to ``None``, matching what argparse itself leaves on the
        namespace when the flag is absent.
        """
        kwargs = self._kwargs()
        if "default" in kwargs:
            return kwargs["default"]
        if kwargs.get("required") is False:
            return None
        return NOT_DEFINED


#: `nargs` values that make a positional variable-arity -- the shape that
#: triggers argparse's greedy positional-run-matching papercut (bpo-15112)
#: when ANOTHER positional sits in the same parser. Verified this session
#: (bare stdlib, no duho): `"*"` and `"?"` fail identically for a flag placed
#: between the two positionals; `"+"` fails too, just at a different arg
#: count (2+ trailing values instead of 1) -- all three are the trigger set.
_VARIADIC_NARGS = ("*", "+", "?")


def _has_variadic_positional(parser: "_argparse.ArgumentParser") -> bool:
    """True if ``parser`` has at least one variable-arity positional.

    Two DIFFERENT argparse papercuts both need this reorder, and between them
    a single variable-arity positional is already enough to trigger one:

    * A flag placed BETWEEN a variable-arity positional and ANOTHER
      positional breaks under argparse's greedy positional-run matching
      (bpo-15112): the run is settled against the argv slice before the next
      optional token, so the variadic one can close out empty/short and
      never reopen.
    * A flag placed anywhere touching a LONE variable-arity positional's own
      run -- with no sibling positional at all -- ALSO gets swallowed as
      "unrecognized arguments" (bpo-14191); a previous version of this
      docstring claimed a lone variadic positional was unaffected, which was
      not actually true (verified this session, bare stdlib).

    Both shapes are handled by the same reorder pass below, so this only
    needs to detect "at least one variable-arity positional" -- no sibling
    required.

    The subparsers action itself (``dest="_duho_command_"``, added by
    ``add_subparsers``) is NOT option-string-bearing either, but it is not a
    user-declared positional field -- excluded explicitly so a root class
    with subcommands and no OTHER declared positional doesn't false-trigger.
    """
    positionals = [
        action
        for action in parser._actions  # type: ignore[attr-defined]
        if not action.option_strings
        and not isinstance(action, _argparse._SubParsersAction)  # type: ignore[attr-defined]
    ]
    return any(action.nargs in _VARIADIC_NARGS for action in positionals)


def _add_fields(
    parser: "_argparse.ArgumentParser",
    cls: type,
    exclusive_groups: "dict | None" = None,
    *,
    parent_dests: "_ty.FrozenSet[str] | None" = None,
    strict: bool = True,
) -> "dict":
    """Add ``cls``'s own declared fields to ``parser`` (titled/mutually-
    exclusive groups included).

    The one field/group wiring routine shared by :meth:`Args._initparser_`
    (class commands) and ``duho.runtime._add_module_declared_fields`` (module
    commands) -- previously duplicated by hand, which is why a module command
    could not use ``NS(conflicts=...)``/``NS(group=...)``: that support lived
    only in ``_initparser_``'s own copy. Iterates ``cls._getargs_()``,
    resolves each field's titled group (``NS(group=...)``) and
    mutually-exclusive group (``NS(conflicts=...)``, precomputing group
    requiredness first so argparse -- which fixes ``required`` at
    group-creation time -- sees the right value on the first member), then
    calls the field's own ``ArgumentBuilder.add_to_parser``.

    ``strict=True`` (the ``_initparser_`` case) raises on an unexpected dest
    collision unless it is an explicit ``parents=[...]`` share (``arg.name in
    parent_dests``). ``strict=False`` (the module-command case) silently
    skips any colliding dest instead, matching a module command's existing
    "never re-adds/conflicts with an inherited global" contract.

    Returns the ``exclusive_groups`` dict (created fresh when not given),
    already merged onto ``parser.exclusive_groups``; ``parser``'s titled-group
    state (``_duho_titled_groups_``) is updated in place too.
    """
    parent_dests = parent_dests if parent_dests is not None else frozenset()
    exclusive_groups = exclusive_groups or {}

    # A mutually-exclusive group is *required* when ANY of its members
    # declares NS(conflicts_required=True). Groups are created lazily on the
    # first member, so pre-compute requiredness across all members here and
    # pass it at creation.
    required_by_conflicts: "dict[str, bool]" = {}
    # A `conflicts=` key must use the SAME `group=` title (or no title)
    # everywhere it appears -- otherwise members that share a conflicts=
    # string land in TWO separate mutex groups (one per title) and are no
    # longer mutually exclusive at all, silently.
    conflicts_titles: "dict[str, object]" = {}
    for arg in cls._getargs_():
        conflicts = arg.conflicts
        if conflicts:
            required_by_conflicts[conflicts] = required_by_conflicts.get(
                conflicts, False
            ) or bool(arg.conflicts_required)
            group_title = arg.group
            if conflicts in conflicts_titles:
                if conflicts_titles[conflicts] != group_title:
                    raise ValueError(
                        f"argument {arg.name!r}: conflicts={conflicts!r} is "
                        f"declared with group={group_title!r} here but "
                        f"group={conflicts_titles[conflicts]!r} elsewhere; "
                        f"every field sharing a conflicts= key must use the "
                        f"same group= (or none)"
                    )
            else:
                conflicts_titles[conflicts] = group_title

    # A bool field that can receive True from a layer OTHER than the
    # CLI needs a way to turn it back off from the command line (see
    # `ArgumentBuilder._kwargs`'s `layered` parameter). `env=` is a
    # per-field signal; a config source is a per-CLASS one.
    _has_config_source = getattr(cls, "_config_", None) is not None

    # Titled argument groups (NS(group="...")), created lazily per title.
    # Persisted on the parser so a parents=[...] merge / subclass override can
    # reuse them, mirroring `exclusive_groups`.
    titled_groups: "dict[str, object]" = (
        getattr(parser, "_duho_titled_groups_", None) or {}
    )

    actions_by_dest = {action.dest: action for action in parser._actions}
    for arg in cls._getargs_():
        _existing_action = actions_by_dest.get(arg.name)
        if _existing_action:
            if strict:
                if arg.name in parent_dests:
                    # A genuine `parents=[...]` merge: the CALLER deliberately
                    # shares this global option with the parent (e.g. a
                    # subcommand inheriting `-v`/`-q`) -- reuse it silently,
                    # same as before.
                    continue
                # Anything else sharing this dest was added by duho ITSELF,
                # moments ago, for this same class (`-h`/`--help`,
                # `--version`, `--print-completion`) -- silently dropping the
                # user's field here (the previous behavior) lost both its
                # value and its declared flags with no error at all,
                # contradicting the documented "no reserved field names"
                # policy. Fail loud at build time instead.
                raise ValueError(
                    f"field {arg.name!r} on {cls.__name__} collides with a "
                    f"dest that {cls.__name__}'s own parser already uses "
                    f"(-h/--help, --version, or --print-completion each "
                    f"reserve their field name); rename the field (a "
                    f"field's dest is always its name -- there is no "
                    f"dest= override; NS(kwargs={{'dest': ...}}) is the raw "
                    f"add_argument escape hatch if you truly need one)"
                )
            # Not strict (a module command's own declared fields): a dest
            # already present -- whether inherited from the root or added by
            # duho itself -- is skipped rather than re-added or conflicted.
            continue

        conflicts = arg.conflicts
        group_title = arg.group

        # The container the field's argument is added to: a titled group when
        # NS(group=...) is set, else the parser itself.
        if group_title is not None:
            if group_title not in titled_groups:
                titled_groups[group_title] = parser.add_argument_group(group_title)
            container = titled_groups[group_title]
        else:
            container = parser

        if conflicts:
            # A conflicts= member lives in a mutually-exclusive group. When it
            # ALSO declares group=, nest the exclusive group inside the titled
            # group (argparse supports it), keyed by (group, conflicts);
            # otherwise it's a top-level group keyed by conflicts (so
            # `parser.exclusive_groups["type"]` keeps working).
            key = (group_title, conflicts) if group_title is not None else conflicts
            if key not in exclusive_groups:
                exclusive_groups[key] = container.add_mutually_exclusive_group(
                    required=required_by_conflicts.get(conflicts, False)
                )
            group = exclusive_groups[key]
        else:
            group = container

        layered = _has_config_source or bool(getattr(arg, "env", None))
        arg.add_to_parser(group, layered=layered)

    # Expose the built mutually-exclusive groups on the parser so a subclass
    # `_parser_` override can add extra options into a `conflicts=`-built
    # group (e.g. a short-flag alias that must stay mutually exclusive with a
    # declared field). Merge rather than overwrite so a parents=[...] parser
    # that already carries groups keeps them.
    existing = getattr(parser, "exclusive_groups", None)
    if existing:
        existing.update(exclusive_groups)
    else:
        parser.exclusive_groups = exclusive_groups
    parser._duho_titled_groups_ = titled_groups  # type: ignore[attr-defined]
    return exclusive_groups


def _patch_parser_for_reorder(parser: "_argparse.ArgumentParser") -> None:
    """Install JUST the flag-between-positionals reorder on a plain parser.

    For parsers built outside ``Args._initparser_`` -- a module command's
    subparser (``runtime._register_module_command``), built via a bare
    ``subparsers.add_parser(...)`` -- so they never get the patched
    ``parse_known_args`` a declarative ``Args``/``Cmd`` subparser does. That
    parser needs neither ``"#cls"`` dispatch nor ``_passthrough_`` splitting
    (a module command has no subclass to select and the root already owns the
    ``--`` split), only this one fix, so a full ``_initparser_`` call would be
    the wrong tool -- it would also try to construct an ``Args`` instance from
    a parser that was never declared as one.
    """
    real_parse_known_args = parser.parse_known_args

    def parse_known_args(
        args: "_ty.Sequence[str] | None" = None, namespace: "NS | None" = None
    ):
        if args is not None and _has_variadic_positional(parser):
            args = _reorder_argv_for_variadic_positional(parser, list(args))
        return real_parse_known_args(args, namespace)

    parser.parse_known_args = parse_known_args  # type: ignore


def _reorder_argv_for_variadic_positional(
    parser: "_argparse.ArgumentParser", argv: "list[str]"
) -> "list[str]":
    """Hoist recognized flags (+ their values) before the positional run.

    Preprocessing ONLY -- never decides an input is invalid. Scans ``argv``
    against ``parser``'s OWN, already-built ``_option_string_actions``
    registry; a token recognized there (bare, or via its ``key`` half of a
    ``--flag=value`` form) is hoisted, along with the value(s) its action's
    ``nargs``/type says it consumes, into a leading ``flags`` run. Everything
    else stays in a trailing ``positionals`` run, in original relative order.
    Concatenating ``flags + positionals`` gives argparse a slice where the
    positional run is contiguous and never interrupted by a flag, sidestepping
    the greedy-matching papercut this function exists for.

    Recognizes a flag by exact key, by its ``--flag=value`` split, by an
    attached short-option value (``-fVALUE``), and by an unambiguous
    ``allow_abbrev`` long-option prefix (``--filt`` for ``--filter``)
    -- the same spellings argparse itself accepts, so a flag written that way
    between two positionals is hoisted exactly like its long/separate-token
    form is.

    **Bails (returns ``argv`` UNCHANGED) on anything it isn't certain about**
    -- a `-`-prefixed token NOT recognized by any of the above (and not a
    negative-number value the parser itself would accept, per its own
    ``_negative_number_matcher``/``_has_negative_number_optionals``), or a
    flag needing a value with none left in ``argv``. This is deliberate: a
    genuine typo (``--filtr`` for ``--filter``) must still surface argparse's
    own honest "unrecognized arguments" error through the UNMODIFIED real
    parse, never get silently absorbed as a phantom positional value. This
    function only ever makes a VALID input parse correctly, or gets out of
    the way entirely.
    """
    known = parser._option_string_actions  # type: ignore[attr-defined]
    negative_number_matcher = getattr(parser, "_negative_number_matcher", None)
    has_negative_number_optionals = bool(
        getattr(parser, "_has_negative_number_optionals", [])
    )
    allow_abbrev = getattr(parser, "allow_abbrev", True)

    flags: "list[str]" = []
    positionals: "list[str]" = []
    i = 0
    n = len(argv)
    while i < n:
        token = argv[i]
        if token == "--":
            # A literal `--` separator is never part of THIS parser's own
            # flag/positional grammar (duho's `_passthrough_` handling
            # already splits argv on the FIRST `--` before this function
            # ever sees it -- see `_initparser_`'s patched
            # `parse_known_args` -- so reaching one here means a SECOND
            # `--`, which is itself part of the payload). Stop reordering;
            # everything from here on is left exactly as-is.
            positionals.extend(argv[i:])
            break

        action = known.get(token)
        self_contained = False
        if action is None and "=" in token:
            action = known.get(token.split("=", 1)[0])
            # `--flag=value` is a single self-contained token; no separate
            # value token to hoist alongside it.
            self_contained = action is not None
        if (
            action is None
            and token.startswith("--")
            and allow_abbrev
            and "=" not in token
        ):
            # An unambiguous long-option PREFIX under argparse's own
            # `allow_abbrev` rule (e.g. `--filt` for `--filter`) -- recognized
            # only when exactly one ACTION (aliases of the same one still
            # count as one) has an option string starting with this token.
            candidates = {
                id(a): a
                for opt, a in known.items()
                if opt.startswith("--") and opt.startswith(token)
            }
            if len(candidates) == 1:
                (action,) = candidates.values()
        if action is None and len(token) > 2 and token[0] == "-" and token[1] != "-":
            # An attached short-option value (`-fVALUE`, `-j3`): recognized
            # only when the two-character prefix maps to a registered short
            # option that itself takes exactly one value -- a zero-value
            # action (`-v`, `-h`) cannot absorb a trailing value this way, and
            # a variadic-nargs one is ambiguous just like the separate-token
            # case below.
            short_action = known.get(token[:2])
            if short_action is not None and short_action.nargs is None:
                action = short_action
                self_contained = True

        if self_contained:
            flags.append(token)
            i += 1
            continue

        if action is not None:
            # Every zero-value action class (store_true/store_false/count/help)
            # already reports `nargs == 0` -- no need to also isinstance-check
            # the specific classes.
            if action.nargs == 0:
                flags.append(token)
                i += 1
                continue
            if action.nargs is not None:
                # The flag's OWN nargs is variable (`"*"`/`"+"`/`"?"`) or an
                # explicit fixed count -- e.g. a `list[T]` field's default
                # `action="extend", nargs="*"` builder. Confirmed this
                # session (bare stdlib): a variadic-nargs FLAG placed
                # directly before positional values, with no separator, is
                # AMBIGUOUS FOR ARGPARSE ITSELF -- even correctly-ordered
                # argv silently misparses (extra tokens get absorbed into
                # the flag's own value list instead of the positional they
                # belonged to; see `docs/guide/arguments.md` /
                # `AGENTS.md`'s note on this shape). Reordering cannot
                # resolve an ambiguity the REAL parser cannot resolve either
                # -- bail unreordered rather than guess and risk swallowing
                # a token that belonged to a positional.
                return argv
            if i + 1 >= n:
                # A flag needing exactly one value, with none left in argv --
                # malformed input. Bail: let the REAL parse produce its own
                # error rather than guessing here.
                return argv
            flags.append(token)
            flags.append(argv[i + 1])
            i += 2
            continue

        if token.startswith("-") and len(token) > 1:
            looks_negative_number = bool(
                negative_number_matcher and negative_number_matcher.match(token)
            )
            if looks_negative_number and not has_negative_number_optionals:
                # A negative-number-shaped token this parser has no
                # negative-number-shaped OPTIONAL to collide with -- treat
                # as a genuine positional value (mirrors argparse's own
                # `_negative_number_matcher` logic for the same decision).
                positionals.append(token)
                i += 1
                continue
            # An unrecognized `-`-prefixed token. Could be a real typo, or a
            # flag this reorder pass doesn't understand -- either way, NOT
            # confident enough to reorder. Bail unchanged; the real parse
            # surfaces its own honest error.
            return argv

        positionals.append(token)
        i += 1

    return flags + positionals


def _suppress_inherited_defaults(child_parser, root_dests, root_defaults=None):
    """Make a subcommand's inherited-option defaults not clobber the root's value.

    For each action on ``child_parser`` whose ``dest`` is also a field declared
    on the root (``root_dests``), set the action's default to ``SUPPRESS`` so
    that, when the flag is absent from the subcommand's argv, argparse leaves the
    namespace value the root already parsed (from an option given before the
    subcommand) intact. Only optional (flagged, non-required) actions are
    touched -- positionals and required options keep their behavior, and the
    subparsers action itself (``dest="_duho_command_"``) is never a root field.

    ``root_defaults`` (optional ``{dest: effective_default}``) lets the caller
    skip suppression for a dest the child DELIBERATELY re-declares with a default
    differing from the root's: that override is intentional and must win, so the
    child keeps its own default rather than deferring to the root.

    ``value_sources`` / config layering are unaffected: they operate on the root
    parser's own actions, not these child copies.
    """
    root_defaults = root_defaults or {}
    for action in child_parser._actions:
        if action.dest not in root_dests:
            continue
        if not action.option_strings:  # positional
            continue
        if getattr(action, "required", False):
            continue
        if (
            action.dest in root_defaults
            and action.default != root_defaults[action.dest]
        ):
            # The child re-declares this field with a different default -- a
            # deliberate override; keep it.
            continue
        action.default = _argparse.SUPPRESS


class Args(_argparse.Namespace):
    """Base class for a duho command's declared fields.

    Subclass this and declare CLI fields as annotated, non-underscore class
    attributes (see the "Naming & Collision Policy" -- every OTHER member is
    sandwich-named or a dunder, so the field namespace stays user-owned); an
    optional flags tuple and a trailing docstring string customize each
    field's ``add_argument`` call. ``Args`` itself is data-only -- it defines
    no ``__call__`` (:func:`duho.main`/:func:`duho.run_command` raise a clear
    ``NotImplementedError`` for a bare ``Args``); subclass :class:`Cmd`
    instead for a RUNNABLE command, or :class:`Cli` for an app root.

    ``Args`` subclasses ``argparse.Namespace``, whose stub declares
    ``__getattr__(self, name) -> Any`` -- so a type checker does NOT catch a
    typo'd field read (``self.regoin`` on a class with a ``region`` field);
    only a real run surfaces it, as an ``AttributeError``.
    """

    #: Annotation-only: declares the shape (a class command's subcommand name)
    #: WITHOUT creating a runtime class/instance attribute -- ``_parsername_``
    #: is set per-subclass, dynamically (``duho.command(...)``'s generated
    #: class body, or a user's own ``_parsername_ = "..."``), never here. A
    #: ``ClassVar`` annotation with no assigned value never appears in
    #: ``vars(cls)``, so it changes no reader; it exists purely so a type
    #: checker considers ANY ``Cmd`` subclass a structural match for
    #: ``duho.discovery.Command`` (whose ``Protocol`` requires this member) --
    #: without it, the documented ``app(commands=[SomeCmd])``/
    #: ``run_command(SomeCmd, ...)`` failed type-checking even though they run
    #: correctly. Already excluded from CLI-field discovery like every
    #: other leading-underscore name, ``ClassVar`` or not.
    _parsername_: "_ty.ClassVar[str]"

    #: Pre-seeded empty class-body-constants cache (P2). ``_class_constants``
    #: (``_introspect``) short-circuits on ``"_duho_constants_" in vars(cls)``,
    #: so seeding it here means building ANY user parser never AST-parses
    #: duho's own ``args.py`` to scan these framework base classes for
    #: class-body flag/env/docstring metadata -- they declare none. This is
    #: safe ONLY because ``Args``/``Cmd``/``Cli`` carry no real CLI-field
    #: declarations in their bodies. A subclass that DOES declare fields gets
    #: its OWN ``vars(cls)`` entry populated by the normal scan (the seed is
    #: per-class in ``vars``, never inherited into the MRO walk), and
    #: ``presets.LoggingArgs`` deliberately is NOT seeded because its class body
    #: declares real fields whose flags-tuples must still be scanned.
    _duho_constants_: dict = {}

    def __init__(self, **kwargs: object) -> None:
        # Namespace.__init__ only setattrs what's passed, so a directly-built
        # instance (or the self-cloning `type(self)(**self._get_kwargs())`
        # pattern) would be missing any declared field not supplied -- notably
        # `store_true` bools, whose default only materializes via argparse.
        # Seed each declared field to its effective default when absent, so a
        # direct instance has the same attribute surface as a parsed one. Only
        # GAPS are filled: passed kwargs (incl. parsed values) always win, and a
        # required field with no default (NOT_DEFINED) is left unset.
        #
        # `name not in vars(self)` (rather than `hasattr`) is deliberate: a
        # field with an explicit CLASS-level default (`files: list = []`) is
        # already `hasattr`-true via inheritance, which used to skip seeding
        # entirely -- so a direct instance read the CLASS ATTRIBUTE itself,
        # and mutating a mutable one (`instance.files.append(...)`) mutated
        # every other instance and every later parse's default too.
        # `vars(self)` only sees THIS instance's own attributes, so the gap
        # still gets filled with `_effective_default_()`'s fresh copy.
        super().__init__(**kwargs)
        for builder in type(self)._getargs_():
            name = builder.name
            if name in kwargs or name in vars(self):
                continue
            default = builder._effective_default_()
            if default is not NOT_DEFINED:
                setattr(self, name, default)
        # Remember exactly which fields THIS CALL passed, outside
        # vars(self) -- see `_duho_explicit_instance_fields`. A subclass that
        # isn't weak-referenceable (e.g. declares `__slots__` without
        # `__weakref__`) simply isn't tracked; `duho.parse(instance)` then
        # falls back to treating every attribute as explicit (today's
        # behavior), same as before this fix.
        try:
            _key = id(self)
            _duho_explicit_instance_fields[_key] = frozenset(kwargs)
            _weakref.finalize(self, _duho_explicit_instance_fields.pop, _key, None)
        except TypeError:
            pass

    @classmethod
    def _getargs_(cls) -> "list[ArgumentBuilder]":
        """This class's own declared fields, as one :class:`ArgumentBuilder`
        per field, in declaration order.

        Built once from ``_introspect.get_clsargs(cls)`` (each field's type
        routed through :meth:`Argument.from_type`/its own ``_argbuilder_``,
        then any ``NS(...)``/``Meta(...)`` metadata applied) and cached on
        ``cls`` (``_duho_builders_``) -- override to add/hide/reorder fields
        programmatically (call ``super()._getargs_()`` and adjust the list).
        """
        if "_duho_builders_" in vars(cls):
            return cls._duho_builders_

        clsargs = _introspect.get_clsargs(cls)
        args: list[ArgumentBuilder] = []
        for name, decl in clsargs.items():
            if decl.annotations:
                # SUPPRESS anywhere in the metadata hides the field.
                if _argparse.SUPPRESS in decl.annotations:
                    continue
                options: dict = {}
                for opts in decl.annotations:
                    # Only configuration-shaped metadata is consumed: a Mapping
                    # or a namespace-like object (has __dict__, e.g. NS(...)). A
                    # PEP-727-style object with a str `.documentation` contributes
                    # help text. Any other metadata (a bare `Annotated[int, "doc"]`
                    # string, an int, ...) is ignored silently, as Annotated's
                    # own semantics require.
                    if isinstance(opts, Meta):
                        # Typed metadata: merge only the explicitly-set fields so
                        # an unset (sentinel) field never overrides a type-derived
                        # kwarg (F5).
                        options.update(opts._duho_options_())
                    elif isinstance(opts, _ty.Mapping):
                        options.update(opts)
                    elif isinstance(getattr(opts, "documentation", None), str):
                        options.setdefault("help", opts.documentation)
                    elif hasattr(opts, "__dict__"):
                        options.update(vars(opts))
                if isinstance(decl.type, Argument):
                    # A CUSTOM Argument type wrapped in Arg[...]/NS(...)
                    # (e.g. `Arg[Port, NS(env="PORT")]`) previously routed
                    # through `Argument.from_type(decl.type, **options)`,
                    # whose `super()._argbuilder_` is the PLAIN protocol
                    # default -- `decl.type`'s own `_argbuilder_` override
                    # never ran, so the custom type became a bare, unresolved
                    # `type=` factory instead of using its own parsing logic.
                    # Build via the type's own override, then apply the same
                    # NS(...)/Meta(...) post-processing `from_type` gives a
                    # plain type (help=/env=/Extend()/... all still work).
                    built = decl.type._argbuilder_(name, decl)
                    _apply_argument_options(built, options)
                else:
                    built = Argument.from_type(decl.type, **options)._argbuilder_(
                        name, decl
                    )
            elif isinstance(decl.type, Argument):
                built = decl.type._argbuilder_(name, decl)
            else:
                built = Argument.from_type(decl.type)._argbuilder_(name, decl)
            args.append(built)

        setattr(cls, "_duho_builders_", args)
        return args

    @classmethod
    def _parser_(
        cls,
        subparser: "_argparse._SubParsersAction | None" = None,
        name: "str | None" = None,  # type: ignore
        parents: _ty.Sequence[_argparse.ArgumentParser] = (),
        _inherited_formatter_class_=None,
        _inherited_agent_root_cls_=None,
        **kwargs,
    ) -> "_Parser[_Self]":
        """Build (or attach) this class's ``argparse.ArgumentParser``.

        ``subparser`` given -- a ``_SubParsersAction`` -- attaches a new
        sub-parser via ``subparser.add_parser(name or _command_name(cls),
        parents=parents, **kwargs)``; omitted, builds a fresh top-level
        ``ArgumentParser`` the same way. Registers every declared field
        (:meth:`_getargs_`) plus ``-h``/``--version``/``--print-completion``
        and, if any, the ``_subcommands_`` tree, then calls
        :meth:`_initparser_` to add them. Override to customize parser
        construction itself (e.g. a shared ``formatter_class``); override
        :meth:`_initparser_` instead to add parser-level configuration that
        doesn't change how the parser object is created.
        """
        if subparser:
            method = subparser.add_parser
        else:
            method = _argparse.ArgumentParser

        # Resolve the name WITHOUT ever writing it back onto
        # the class. A derived name used to be persisted here (`setattr(cls,
        # "_parsername_", name)`) so a later `getattr` could reuse it -- but
        # `getattr` also follows the MRO, so the derived name leaked to every
        # SUBCLASS built afterwards (a subcommand and a subclass of it,
        # registered as siblings, collapsed onto one name; on 3.11+ the build
        # itself raised "conflicting subparser"). `_command_name` resolves
        # only a `_parsername_` declared ON `cls` ITSELF (`vars(cls)`, not
        # `getattr`) -- a subclass that wants to deliberately share its
        # base's declared name has to say so itself; a bare subclass always
        # gets its own class name, never one inherited from a base's build.
        name_given = name is not None
        name: str = name or _command_name(cls)
        # Whether `name` reflects something the user actually
        # declared (an explicit `name=` here, or `cls`'s own `_parsername_`)
        # rather than the bare class-name fallback -- read by
        # `_PrintCompletionAction`/`print_completion` to decide whether the
        # emitted completion script should default to the invoked command
        # (`sys.argv[0]`'s stem) instead of a class name nobody types.
        explicit_prog = name_given or bool(vars(cls).get("_parsername_"))
        if not subparser and "prog" in kwargs:
            # `ArgumentParser(name, parents=..., **kwargs)` fills the
            # positional `prog` with `name`, so a caller-supplied `prog=`
            # kwarg (`duho.parser(cls, prog=...)`, `duho.parse(cls, argv,
            # parser_kwargs={"prog": ...})`) collided with it outright
            # (`TypeError: got multiple values for argument 'prog'`). An
            # explicit `prog=` names the actual program the user wants and
            # wins outright (a subparser's own `add_parser(name, ...)` has no
            # such collision -- `name` there is the subcommand's registration
            # key, a different argparse parameter from `prog`).
            name = kwargs.pop("prog")
            explicit_prog = True
        # Escape a literal `%` in docstring-derived text for
        # argparse's `%`-expansion. `help=` (this class's own one-line
        # subcommand summary) is ALWAYS expanded by argparse and must always
        # be escaped; `description=` is only formatted when it contains a
        # real `%(prog)` placeholder, so escaping it unconditionally (an
        # earlier fix did) showed a literal `%` DOUBLED in `--help`.
        _doc = cls.__doc__ or ""
        kwargs.setdefault("description", _escape_description(_doc))
        # F8: opt-in help formatter (``_help_formatter_`` class attr, e.g.
        # ``duho.DefaultsFormatter``/``duho.ColorHelpFormatter``) plumbed into
        # argparse's ``formatter_class``. ``setdefault`` so a caller-supplied
        # ``formatter_class=`` still wins; unset means argparse's plain default.
        # A class that declares none of its own inherits the nearest
        # ANCESTOR's effective formatter (`_inherited_formatter_class_`,
        # threaded down from the parent's own build below) so the whole
        # subcommand tree is styled consistently, not just direct children.
        own_formatter = getattr(cls, "_help_formatter_", None)
        effective_formatter = (
            own_formatter if own_formatter is not None else _inherited_formatter_class_
        )
        if effective_formatter is not None:
            kwargs.setdefault("formatter_class", effective_formatter)
        # The APP's true root class, threaded down through the whole
        # `_subcommands_` tree the same way `_inherited_formatter_class_` is
        # (see the recursive `sub._parser_(...)` call below) -- `None` here
        # means THIS call is the actual top level, so `cls` itself is the
        # root; a deeper node always keeps propagating the same value it
        # received, never resetting to its own `cls`. Consumed by
        # `_install_agent_help` (via `_initparser_`) so a subcommand-scoped
        # agent-help document still reports the app's own version/exit codes.
        agent_root_cls = (
            _inherited_agent_root_cls_
            if _inherited_agent_root_cls_ is not None
            else cls
        )
        if subparser:
            kwargs.setdefault(
                "help",
                _escape_help(_doc.strip().splitlines()[0]) if _doc.strip() else "",
            )
            # Subcommand aliases (argparse's add_parser accepts `aliases`; the
            # top-level ArgumentParser does not, so only apply when nested).
            aliases = getattr(cls, "_parseraliases_", None)
            if aliases:
                kwargs.setdefault("aliases", list(aliases))

        # Guard against `cls` already being built higher up in this same
        # call tree (a subcommand that subclasses its own root, directly or
        # via a shared/inherited `_subcommands_` list) -- otherwise building
        # it as its own child recurses without end.
        _build_ids = _guard_recursive_build(cls)
        try:
            # Dests the framework will add to THIS parser (below,
            # and via `_subcommands_`) so a same-named user field collides
            # loudly instead of vanishing silently -- except a dest that came
            # from a `parents=` parser, which is a deliberate, silent reuse
            # (a subcommand inheriting a shared global option).
            parent_dests = frozenset(
                a.dest for p in parents for a in getattr(p, "_actions", ())
            )
            parser = _ty.cast(
                "_Parser[_Self]",
                method(name, parents=list(parents), **kwargs),
            )

            cls._initparser_(
                parser,
                is_subcommand=bool(subparser),
                parent_dests=parent_dests,
                explicit_prog=explicit_prog,
                agent_root_cls=agent_root_cls,
            )

            # A private, sandwich-named dest -- never a name a
            # user field could plausibly declare -- so a root field literally
            # named `command` (or a nested `_subcommands_` tree reusing the
            # same dest) is never silently clobbered by subcommand selection.
            subcommands = (
                vars(cls).get("_subcommands_")
                if subparser is not None
                else getattr(cls, "_subcommands_", None)
            )
            if subcommands:
                subparsers = parser.add_subparsers(dest="_duho_command_", required=True)
                # Dests this (root) class declares itself: an option given BEFORE the
                # subcommand parses into these on the root namespace. A child that
                # inherits the same field (via MRO) re-declares it with its own
                # default and would clobber that value back when the flag is absent
                # from the child's argv. Suppress the child's default for those
                # shared, optional dests so the parent's parsed value survives; the
                # child still accepts the flag AFTER the subcommand (overwrites) and
                # its own unique args are untouched.
                root_builders = {b.name: b for b in cls._getargs_()}
                root_dests = set(root_builders)
                root_defaults = {
                    n: b._effective_default_() for n, b in root_builders.items()
                }
                for sub in subcommands:
                    child = sub._parser_(
                        subparsers,
                        _inherited_formatter_class_=effective_formatter,
                        _inherited_agent_root_cls_=agent_root_cls,
                    )
                    # Link child -> parent so `_merge_layers_upward`
                    # (run from the child's own `_initparser_`-patched
                    # `parse_known_args`, only when it actually executes) folds
                    # this child's provenance into the parent's, one level at a
                    # time, only for the parser chain actually selected.
                    child._duho_parent_parser_ = parser  # type: ignore[attr-defined]
                    _suppress_inherited_defaults(child, root_dests, root_defaults)
        finally:
            _build_ids.discard(id(cls))

        return parser

    @classmethod
    def _initparser_(
        cls,
        parser: _argparse.ArgumentParser,
        is_subcommand: bool = False,
        parent_dests: "_ty.FrozenSet[str] | None" = None,
        explicit_prog: bool = False,
        agent_root_cls: "type | None" = None,
    ):
        """Populate an already-created ``parser`` with this class's own fields.

        Called by :meth:`_parser_` right after creating/attaching the parser:
        adds each declared field's argument (respecting
        ``NS(group=...)``/``NS(conflicts=...)``
        titled/mutually-exclusive groups), patches ``parse_known_args`` to
        build the final ``Args``/``Cmd`` instance from the raw ``Namespace``,
        and -- for a non-subcommand root -- injects ``--print-completion``
        when ``_completion_`` is set. Override to add parser-level
        configuration ``_parser_`` doesn't itself expose (call
        ``super()._initparser_(parser, ...)`` first to keep this behavior).
        """
        parent_dests = parent_dests if parent_dests is not None else frozenset()

        def parse_known_args(
            args: "_ty.Sequence[str] | None" = None, namespace: "NS | None" = None
        ):
            if namespace is None:
                namespace = _argparse.Namespace()

            setattr(namespace, "#cls", cls)

            # `_passthrough_`: capture argv after the FIRST literal `--`
            # separator. Only the
            # top-level parse owns the split -- a subparser is invoked by
            # argparse._SubParsersAction with an already-sliced arg list and
            # namespace=None, so splitting there would double-consume. The
            # left side is parsed normally; the right side is stashed on the
            # constructed instance as `_passthrough_` (empty list when absent
            # or when multiple `--` appear, only the first splits).
            passthrough: "list[str] | None" = None
            if not is_subcommand:
                if args is None:
                    argv = _sys.argv[1:]
                else:
                    argv = list(args)
                if "--" in argv:
                    idx = argv.index("--")
                    passthrough = argv[idx + 1 :]
                    argv = argv[:idx]
                args = argv

            # A flag placed BETWEEN two positional groups (one of them
            # variable-arity) breaks under argparse's own greedy
            # positional-run matching (bpo-15112) -- reorder recognized
            # flags to the front of the slice THIS parser will actually see
            # so the positional run stays contiguous. Scoped to parsers with
            # the risky shape only (cheap check, no cost/behavior change
            # otherwise); never touches anything after the `--` split above
            # (that's `passthrough`, already carved out); bails unreordered
            # on anything it isn't certain about, so a genuine typo still
            # surfaces argparse's own honest error through the real,
            # UNMODIFIED parse below.
            if args is not None and _has_variadic_positional(parser):
                args = _reorder_argv_for_variadic_positional(parser, list(args))

            # Install this class's own env/config/instance placeholders as
            # not-yet-converted defaults (Design Q2/Q4) -- lazily, right here,
            # so a value belonging to a subcommand this invocation never
            # reaches is never even resolved.
            _stage_layers(parser, cls)

            parsed, unk = _argparse.ArgumentParser.parse_known_args(
                parser, args, namespace
            )

            # Convert whichever placeholders the CLI left untouched, report a
            # bad one through `parser.error`, and merge this parser's
            # own provenance up into its parent's -- see
            # `_finalize_layers`/`_merge_layers_upward`.
            _finalize_layers(parser, cls, parsed)
            _merge_layers_upward(parser)

            # Per-PARSE mutable-default copy, scoped to THIS parser's
            # own actions (a subcommand's redeclared fields are a different
            # action set, handled the same way when ITS OWN wrapped
            # parse_known_args runs).
            for _pa in parser._actions:
                _dest = _pa.dest
                if _dest is None or _dest is _argparse.SUPPRESS:
                    continue
                _default = _pa.default
                if isinstance(_default, (list, set, dict)) and (
                    getattr(parsed, _dest, None) is _default
                ):
                    # `_kwargs` already gives each parser BUILD its own
                    # copy of a mutable default, but argparse puts that SAME
                    # object onto every namespace a REUSED parser produces
                    # when the field is never touched by this parse -- so two
                    # `parse_args()` calls on one cached parser would share
                    # (and, on mutation, leak into each other's) that list/
                    # set/dict. Break the aliasing once more, per parse.
                    setattr(parsed, _dest, _copy.copy(_default))

            if is_subcommand:
                # Invoked via argparse._SubParsersAction.__call__, which
                # calls us with namespace=None and then copies vars(result)
                # back onto the *parent* namespace. Keep "#cls" in the
                # returned dict (rather than popping/constructing here) so
                # it propagates upward and overwrites the parent's own
                # "#cls" -- subparsers are parsed after the parent's own
                # actions, so the deepest selection always lands last and
                # wins. Only the true top-level call (is_subcommand=False)
                # pops "#cls" and constructs the final instance.
                return parsed, unk

            _cls: "type[_Self]" = parsed.__dict__.pop("#cls")
            parser._duho_selected_cls_ = _cls  # type: ignore
            # Drop the `_CollectionAction`/`UpdateAction` sidecars
            # (`_duho_items_<dest>` / `_duho_dict_seen_<dest>`) before
            # constructing the instance so this internal bookkeeping never leaks
            # into vars(instance) or the documented clone pattern.
            for _sidecar in [
                k
                for k in parsed.__dict__
                if k.startswith("_duho_items_") or k.startswith("_duho_dict_seen_")
            ]:
                del parsed.__dict__[_sidecar]
            instance = _cls(**parsed.__dict__)
            # Attach captured passthrough (empty list when no `--` was seen).
            instance._passthrough_ = passthrough if passthrough is not None else []
            # Debug-aid linkage for duho.value_sources(): remember the parser
            # (which carries _duho_value_sources_/_duho_merged_defaults_,
            # merged up the selected chain by `_merge_layers_upward` as each
            # level's own `parse_known_args` finished) that produced instances
            # of this class. `parser` here is THIS closure's own variable --
            # for a nested selection `_cls` has already been rebound to the
            # DEEPEST selected class by the `pop("#cls")` above, while
            # `parser` is still the ROOT's, which by now holds the merged
            # chain. Per-class, not per-instance -- keeps Args
            # instances themselves free of framework bookkeeping in
            # vars()/__dict__.
            _cls._duho_last_parser_ = parser  # type: ignore[attr-defined]
            return instance, unk

        parser.parse_known_args = parse_known_args  # type: ignore

        def _unsupported_intermixed_args(*_a, **_kw):
            # argparse's own `parse_intermixed_args` (both the public
            # method and `parse_known_intermixed_args` it calls) route
            # differently across versions -- 3.9 calls back into
            # `self.parse_known_args` (our patch above) and then crashes
            # trying to `delattr` positional dests off the constructed `Args`
            # instance it gets back instead of a plain `Namespace`; 3.14 calls
            # a private `_parse_known_args2` directly, bypassing the patch
            # entirely and returning a bare `Namespace` with no `"#cls"`
            # selection, no constructed instance, and no `_passthrough_`.
            # Neither is a supported duho path; fail loud with a pointer to
            # the real one instead of a version-dependent crash or a
            # half-built result.
            raise NotImplementedError(
                "duho parsers do not support parse_intermixed_args()/"
                "parse_known_intermixed_args() (argparse bypasses duho's own "
                "instance-construction hook for interleaved positionals on "
                "at least one supported Python version); use duho.parse(cls, "
                "argv) instead"
            )

        parser.parse_known_intermixed_args = _unsupported_intermixed_args  # type: ignore

        version = _resolve_version(cls)
        actions_by_dest_pre = {action.dest: action for action in parser._actions}
        if version and "version" not in actions_by_dest_pre:
            parser.add_argument(
                "--version",
                # Escape a literal `%` in the resolved version string --
                # argparse `%`-formats `version=` the same as any other help
                # text, so `_version_ = "2.0 (100% rewrite)"` crashed
                # `--version` with a bare `TypeError`.
                action="version",
                version="%(prog)s " + version.replace("%", "%%"),
            )

        # --print-completion: opt-in via _completion_ = True, only injected
        # on the top-level parser (a subcommand's own parser only sees its
        # own subtree, not the whole app) -- skipped if a "print_completion"
        # dest already exists (e.g. from a parents=[...] parser).
        if not is_subcommand and getattr(cls, "_completion_", False):
            actions_by_dest_pre2 = {action.dest: action for action in parser._actions}
            if "print_completion" not in actions_by_dest_pre2:
                parser.add_argument(
                    "--print-completion",
                    # One shared tuple, also used by the standalone
                    # `print_completion()` function's own validation, instead
                    # of a second hardcoded copy that could silently drift.
                    choices=_COMPLETION_SHELLS,
                    action=_PrintCompletionAction,
                    root_parser=parser,
                    # Default the emitted script's command name to
                    # the invoked `sys.argv[0]` stem when the root's own name
                    # is only the class-name fallback (nobody types `MyApp`).
                    explicit_prog=explicit_prog,
                    dest="print_completion",
                    help="Print a shell completion script for the given shell and exit.",
                )

        # Wire this class's own declared fields (and their titled/mutually-
        # exclusive groups) onto the parser -- shared with
        # `runtime._add_module_declared_fields` so a module command
        # gets the exact same `NS(group=...)`/`NS(conflicts=...)` support a
        # class command does.
        _add_fields(parser, cls, parent_dests=parent_dests, strict=True)

        # Agent help: stash the class for the emitter, make --help env-aware, and
        # add the opt-in --help-agents flag. See `_install_agent_help`.
        _install_agent_help(parser, cls, is_subcommand, agent_root_cls=agent_root_cls)

        return parser


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

    #: Own empty class-body-constants cache (P2): ``Cmd``'s body declares no
    #: real CLI fields (only ``_passthrough_`` and ``__call__``), so seeding
    #: this skips AST-parsing ``args.py`` for it. See ``Args._duho_constants_``.
    _duho_constants_: dict = {}

    #: argv captured after the first literal ``--`` separator (parse-time);
    #: an empty list when no ``--`` was present. Populated on the parsed
    #: instance by ``_initparser_``'s patched ``parse_known_args``.
    _passthrough_: "list[str]"

    def __call__(self):  # noqa: D401 - contract stub, overridden by subclasses
        """Run the command. Override ``__call__`` in a ``Cmd`` subclass.

        The base raises ``NotImplementedError`` naming the concrete class,
        so a ``Cmd`` that forgets to implement ``__call__`` fails loud when
        dispatched rather than silently doing nothing.
        """
        raise NotImplementedError(
            f"{type(self).__name__} is a Cmd but does not implement '__call__'"
        )


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
       (``args.py``/``runtime.py``), so declaring them here changes no
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

    #: Own empty class-body-constants cache (P2): every field ``Cli`` declares
    #: is sandwich-named (``_version_``, ``_completion_``, ...) and gets filtered
    #: out by ``get_clsargs`` anyway, so seeding this skips AST-parsing
    #: ``args.py`` for ``Cli``. See ``Args._duho_constants_``.
    _duho_constants_: dict = {}

    #: ``--version`` string, the ``AUTO`` sentinel (resolve via
    #: ``importlib.metadata``), or ``None`` for no ``--version`` flag. Read by
    #: ``_resolve_version`` (``args.py``).
    #:
    #: NOTE: every annotation on this class is written with ``typing.Union`` /
    #: ``typing.Optional`` and quoted, NEVER PEP-604 ``X | Y`` -- even sandwich-
    #: named fields are evaluated by ``typing.get_type_hints`` in
    #: ``_introspect.get_clsargs`` (before the ``_``-prefix filter drops them),
    #: so a ``|`` union would raise ``TypeError`` at parser-build time on 3.9.
    _version_: "_ty.Optional[_ty.Union[str, _AutoVersion]]" = None

    #: Distribution name override for ``_version_ = duho.AUTO`` when the import
    #: package differs from the PyPI distribution name. Read by
    #: ``_resolve_version``.
    _distribution_: "_ty.Optional[str]" = None

    #: When ``True``, inject ``--print-completion {bash,zsh,fish,powershell}``
    #: on the top-level parser. Read by ``_initparser_`` (``args.py``);
    #: defaults off.
    _completion_: bool = False

    #: Path to a config file whose values become layered defaults (precedence
    #: CLI > env > config > class default). ``None`` disables it. A ``.json`` file
    #: is parsed as JSON, any other suffix (``.toml``/unspecified) as TOML. A
    #: path that does not exist YET is treated as no config at all (skipped,
    #: logged at debug) rather than raising -- an explicit ``config=`` kwarg to
    #: ``duho.main``/``duho.parse``/``duho.app`` stays strict. Read by
    #: ``_resolve_config_dict`` (``args.py``) via ``_apply_layers``.
    _config_: "_ty.Optional[_ty.Union[str, _pathlib.Path]]" = None

    #: Optional custom config loader ``Callable[[Path], dict]`` (F7). When set it
    #: is used INSTEAD of duho's built-in JSON/TOML dispatch, so a user can plug a
    #: format duho does not ship (e.g. YAML via their own ``yaml.safe_load``)
    #: WITHOUT duho depending on it -- keeping the zero-runtime-deps contract.
    #: Read by ``_load_config`` (``args.py``) via ``_resolve_config_dict`` /
    #: ``duho.app``.
    _config_loader_: "_ty.Optional[_ty.Callable[[_pathlib.Path], dict]]" = None

    #: Opt-in argparse help ``formatter_class`` (F8). ``None`` (default) uses
    #: argparse's plain formatter; set it to ``duho.DefaultsFormatter``,
    #: ``duho.ColorHelpFormatter``, ``duho.ColorDefaultsFormatter``, or any
    #: ``HelpFormatter`` subclass. Plumbed into ``formatter_class`` by
    #: ``Args._parser_`` (and inherited onto every subcommand parser).
    _help_formatter_: "_ty.Optional[type]" = None

    #: The static subcommand tree. ``None`` (the default) means "no declared
    #: subcommands"; self-registration lazily materializes a per-class list.
    #: Read via ``getattr(cls, "_subcommands_", None)`` (``args.py`` +
    #: ``runtime.py``) -- declaring it here does NOT change that contract.
    _subcommands_: "_ty.Optional[_ty.Sequence[_ty.Type[Cmd]]]" = None

    #: When ``True``, add the opt-in ``--help-agents`` flag (a detailed,
    #: machine-readable description of the whole CLI for AI agents). Read by
    #: ``_install_agent_help`` (``args.py``); defaults off. Independent of the
    #: always-on ``AGENT_HELP`` env-var trigger, which needs no opt-in.
    _agent_help_: bool = False

    #: Environment variable whose truthy value flips ``--help`` into agent mode.
    #: ``None`` (default) uses :data:`duho.agenthelp.DEFAULT_ENV` (``AGENT_HELP``).
    #: Read by ``_AgentHelpAction`` (``args.py``) via ``agent_help_requested``.
    _agent_help_env_: "_ty.Optional[str]" = None

    #: Optional examples surfaced in the agent-help document. A sequence of
    #: command strings, or of ``(command, description)`` pairs. ``None`` (default)
    #: lets duho synthesize a minimal invocation line. Read by
    #: ``duho.agenthelp`` when building the document.
    _examples_: "_ty.Optional[_ty.Sequence[_ty.Any]]" = None

    #: Optional exit-code overrides/additions for the agent-help document, as a
    #: ``{code: meaning}`` mapping merged over duho's defaults (0/1/2). ``None``
    #: (default) uses the defaults alone. Read by ``duho.agenthelp``.
    _exit_codes_: "_ty.Optional[_ty.Mapping[_ty.Any, str]]" = None

    @classmethod
    def _register_subcmd_(cls, child: "_C") -> "_C":
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

    @classmethod
    def subcommand(cls, child: "_C") -> "_C":
        """Decorator form of :meth:`_register_subcmd_`.

        Lets a command file self-attach to the root::

            @MyApp.subcommand
            class Deploy(Cmd):
                ...

        Returns ``child`` unchanged, so the decorated class keeps its
        identity. Equivalent to calling ``MyApp._register_subcmd_(Deploy)``.
        """
        return cls._register_subcmd_(child)


def command(
    args_cls: "type[Args]",
    func: "_ty.Callable[[_ty.Any], object]",
    *,
    name: "str | None" = None,
    module: "str | None" = None,
) -> "type[Cmd]":
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
    namespace: "dict[str, object]" = {
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
            frame.f_globals.get("__name__", __name__) if frame is not None else __name__
        )
    namespace["__module__"] = resolved_module
    # Never matches a real ClassDef -- `duho.command(...)` synthesizes this
    # class, it has no body of its own for AST introspection to find.
    namespace["__qualname__"] = f"command.<locals>.{cls_name}"
    namespace.setdefault("__doc__", args_cls.__doc__)

    return _ty.cast("type[Cmd]", type(cls_name, bases, namespace))


def Extend(
    split: "str | _ty.Callable[[str], _ty.Iterable]", **kwargs: object
) -> "_argparse.Namespace":
    """Create a collection argument whose text is split on ``split`` first.

    ``list[str]``'s own default builder sets ``nargs="*"`` (so a plain list
    field accepts both ``--x a --x b`` and ``--x a b``); combined with a
    factory that SPLITS one token into several, that combination
    double-collects: argparse gathers ``nargs="*"`` tokens first and applies
    the factory to EACH ONE individually, so a token's split result (itself a
    list, e.g. ``"a,b"`` -> ``["a", "b"]``) would be appended as ONE nested
    element instead of being flattened -- ``--rcopts '!*,build'`` becoming
    ``[['!*', 'build']]``, not ``['!*', 'build']``. Explicitly overriding
    ``nargs=None`` here (a single string per flag occurrence, argparse's own
    default) avoids the double-collection.

    The split parts are mapped through the field's OWN element factory (so
    ``Arg[list[int], Extend(",")]`` yields ints, not strings) and fed to
    whichever collection action the field's declared type already uses --
    ``list``, ``set``, or ``tuple`` -- so this composes with a typed
    or non-list collection instead of forcing a stdlib list-only action. The
    field's own declared default (or the type ladder's empty collection when
    none is declared) is used as-is; it is NOT overridden here, so it is kept
    when the flag is absent and replaced -- like any other collection option
    -- on the first CLI occurrence.
    """
    kwargs.setdefault("nargs", None)
    if isinstance(split, str):
        splitter: _ty.Callable[[str], list] = lambda x: x.split(split)  # type: ignore
    else:

        def splitter(text: str):
            result = split(text)
            if isinstance(result, list):
                return result
            return list(result)

    return _argparse.Namespace(split=splitter, kwargs=kwargs)


def Count(**kw: object) -> "_argparse.Namespace":
    """Create a count-action argument (e.g. `-vvv` -> 3)."""
    return NS(action="count", kwargs=kw)


def Append(type: "Factory" = str, **kw: object) -> "_argparse.Namespace":
    """Create an append-action argument, accumulating repeated flag values.

    Explicitly clears nargs: a bare `list`/`list[T]` annotation's implicit
    builder defaults to action="extend", nargs="*" (space-separated), which
    would make append() collect a *list* per occurrence instead of a scalar.
    """
    return NS(action="append", type=type, nargs=None, kwargs=kw)


def Const(value: object, **kw: object) -> "_argparse.Namespace":
    """Create a store_const-action argument that stores `value` when present."""
    return NS(action="store_const", const=value, kwargs=kw)


def Choice(*choices: object, **kw: object) -> "_argparse.Namespace":
    """Restrict an argument's accepted values to `choices`."""
    return NS(choices=tuple(choices), kwargs=kw)


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

    ``prog`` overrides the command name the emitted script binds
    to. It defaults to the built parser's own ``prog`` (``_parsername_``, or
    the class name when that is unset) -- which, for a CamelCase class name,
    is almost never the command someone actually types (``myapp``, not
    ``MyApp``); pass ``prog="myapp"`` (or set ``_parsername_``/use
    ``duho.app(name=...)``) to bind the script to the real installed command.
    """
    from . import completion as _completion

    if shell not in _COMPLETION_SHELLS:
        raise ValueError(
            f"unknown shell {shell!r}; choose from " f"{', '.join(_COMPLETION_SHELLS)}"
        )
    parser = cls._parser_()
    emitter = getattr(_completion, shell)
    _write_machine_text(emitter(parser, prog=prog), file)


def print_agent_help(cls, file=None) -> None:
    """Print a detailed, machine-readable (JSON) agent-help document for `cls`.

    Standalone counterpart to the ``--help-agents`` flag / the ``AGENT_HELP``
    env-var trigger: builds ``cls``'s parser tree fresh and describes it
    (independent of whether either trigger is wired up), then writes the JSON to
    ``file`` (default ``sys.stdout``). Delegates to
    :func:`duho.agenthelp.print_agent_help`.
    """
    from . import agenthelp as _agenthelp

    _agenthelp.print_agent_help(cls, file=file)


def _setup_instance_logging(
    instance, setup_logging: bool, root_cls: "type | None" = None
) -> None:
    """Initialize stderr logging + apply verbosity for a parsed instance:
    the identical block ``duho.main`` and ``duho.app`` each ran
    inline, now shared by both entry points.

    A no-op unless `setup_logging` is true. Prefers the parsed instance's own
    ``_set_loglevels_`` (present when it mixes in ``LoggingArgs``). When the
    DEEPEST selected class is a plain ``Cmd`` with no such method, but
    `root_cls` -- the class `duho.main`/`duho.app` were actually called with
    -- IS a ``LoggingArgs``, the verbosity fields are still on `instance`
    (argparse copies the parent parser's parsed values onto the shared
    instance regardless of which class gets constructed); apply them under
    the root's own command name via the module-level
    :func:`duho.presets._apply_loglevels` instead of the missing bound method.
    This is the documented ``class MyApp(LoggingArgs, Cli)`` +
    plain ``Cmd`` leaves shape from the README, which previously left
    ``-v``/``-q``/``--loglevel`` silently doing nothing.

    ``init_stderr_logging`` is idempotent, so it is called
    unconditionally here rather than only when the root logger has no
    handlers yet -- a caller managing its own logging entirely should pass
    ``setup_logging=False`` instead.
    """
    if not setup_logging:
        return
    setter = getattr(instance, "_set_loglevels_", None)
    if setter is None and root_cls is not None:
        from . import presets as _presets

        if issubclass(root_cls, _presets.LoggingArgs):
            logger_name = _command_name(root_cls)
            setter = lambda: _presets._apply_loglevels(instance, logger_name)
    if setter is None:
        return
    _duho_logging.init_stderr_logging()
    setter()


def _maybe_await(result):
    """Drive a coroutine result to completion, returning its value (F4).

    A ``Cmd.__call__`` (or a ``duho.main`` target) may be ``async def``; its
    invocation returns a coroutine. This runs it with ``asyncio.run`` at the
    call site so the awaited value becomes the command's result/exit code, and
    passes any non-coroutine result through unchanged.

    ``asyncio`` is imported lazily here (not at module top) so a plain
    ``import duho`` never pays its import cost -- only a command that actually
    returns a coroutine triggers the load (startup budget).

    Gates on ``inspect.isawaitable`` (not the narrower
    ``inspect.iscoroutine``, which only recognizes NATIVE coroutine objects)
    so a Cython-/mypyc-compiled ``async def`` -- whose result registers under
    ``collections.abc.Coroutine`` without being a ``types.CoroutineType`` --
    is still driven to completion instead of silently returned as-is (and
    then, e.g., used as a process exit code). An ``async def`` written as an
    async GENERATOR (``yield`` instead of ``return``) is rejected outright:
    it is not awaitable at all, so it would otherwise pass through unchanged
    and be returned as the command's result. Also refuses to nest
    ``asyncio.run`` inside an event loop that is already running (e.g. inside
    Jupyter, or an async host such as an MCP server) with a message naming
    the fix, instead of a bare ``asyncio`` internals error plus a leaked
    "coroutine was never awaited" warning.
    """
    import inspect as _inspect

    if _inspect.isasyncgen(result):
        raise TypeError(
            "a Cmd.__call__ (or duho.main target) must not be an async "
            "generator; write a plain `async def` that returns a value "
            f"instead of one that `yield`s (got {result!r})"
        )
    if not _inspect.isawaitable(result):
        return result

    import asyncio as _asyncio

    try:
        _asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        close = getattr(result, "close", None)
        if callable(close):
            close()
        raise RuntimeError(
            "duho: an async command returned an awaitable, but an asyncio "
            "event loop is already running -- duho.main/run_command/"
            "call_tool cannot nest asyncio.run() inside one. Await the "
            "command yourself (`await cmd()`), or run the sync entry point "
            "via `asyncio.to_thread(...)`."
        )

    if _asyncio.iscoroutine(result):
        return _asyncio.run(result)

    async def _await_result(awaitable=result):
        return await awaitable

    return _asyncio.run(_await_result())


def main(
    cls: "type[Args]",
    argv: "_ty.Sequence[str] | None" = None,
    *,
    setup_logging: bool = True,
    config: "str | _pathlib.Path | None" = None,
) -> object:
    """Build a parser for cls, parse argv, and dispatch the selected Cmd.

    Module-level (not a classmethod) so the Args subclass namespace stays
    entirely user-owned. Steps: build parser (auto-registers _subcommands_),
    apply the env/config/class-default layers (`config` overrides `cls._config_`;
    precedence CLI > env > config > class default), parse argv (SystemExit from
    argparse propagates), optionally set up stderr logging + apply verbosity,
    then run the command and map a None return to 0. Logging setup runs when
    the resulting instance provides `_set_loglevels_` directly, OR -- when the
    selected leaf is a plain `Cmd` under a `LoggingArgs` root -- when `cls`
    itself is a `LoggingArgs` (see `_setup_instance_logging`).

    Since the Args/Cmd split, dispatch expects the selected class to be
    a ``Cmd`` (executable, defines ``__call__``). A bare data ``Args`` -- with
    no ``__call__`` -- raises a clear ``NotImplementedError`` ("Args holds data;
    make it a Cmd to run it") rather than silently doing nothing.

    Typed ``-> object``, not ``-> int``: a ``None`` result maps to ``0``, but
    any OTHER value the command returns (an ``int``, or anything else destined
    for ``sys.exit``) passes straight through unchanged.
    """
    parser = cls._parser_()
    _apply_layers(parser, cls, config=config)
    instance = parser.parse_args(argv)

    _setup_instance_logging(instance, setup_logging, cls)

    run = getattr(instance, "__call__", None)
    if run is None:
        raise NotImplementedError(
            f"{type(instance).__name__} holds data but is not runnable "
            f"(no '__call__'); make it a Cmd (subclass duho.Cmd or "
            f"build one with duho.command(...)) to run it"
        )

    result = _maybe_await(run())
    return 0 if result is None else result


def parse(
    spec: "type[_A] | _A",
    argv: "_ty.Sequence[str] | None" = None,
    *,
    parser_kwargs: "_ty.Optional[_ty.Mapping[str, object]]" = None,
    config: "str | _pathlib.Path | None" = None,
) -> "_A":
    """Build a parser from `spec` and parse `argv` into a new instance.

    `spec` may be:
    - An `Args` subclass (type): equivalent to `spec._parser_().parse_args(argv)`,
      with the env/config/class-default layers applied first (see below).
    - An instance of an `Args` subclass: the field values the caller EXPLICITLY
      passed to its `__init__` (not a placeholder `Args.__init__` itself seeded
      for an omitted field -- a bare `bool`/collection field left at its own
      default is not "an instance value" just because it materializes onto
      `vars(spec)`) are used as argparse defaults. CLI args still override
      those defaults. Returns a NEW instance of `type(spec)`; `spec` itself is
      never mutated.

    `config` (a path, or None to fall back to `cls._config_`) layers config-file
    and environment-variable defaults under the instance/CLI ones. Full
    precedence: CLI args > instance field values > env > config file > class
    defaults. Note this means a required field (no class default) that is
    supplied by *any* layer becomes effectively optional for this call.
    `duho.value_sources` reports such a field as ``"instance"``.
    """
    parser_kwargs = parser_kwargs or {}
    if isinstance(spec, type):
        cls = spec
        parser = cls._parser_(**parser_kwargs)
        _apply_layers(parser, cls, config=config)
        return parser.parse_args(argv)

    cls = type(spec)
    parser = cls._parser_(**parser_kwargs)
    _apply_layers(parser, cls, config=config, instance=spec)
    return parser.parse_args(argv)


def parse_globals(
    cls: "type[_A]",
    argv: "_ty.Sequence[str] | None" = None,
    *,
    config: "str | _pathlib.Path | None" = None,
    **parser_kwargs: object,
) -> "_A":
    """Parse ONLY a root command's global args, ignoring/relaxing subcommands.

    Builds ``cls``'s root parser (``cls._parser_(**parser_kwargs)``), applies
    the same env/config/class-default layers ``duho.main``/``duho.parse`` do
    (``config`` overrides ``cls._config_``; precedence CLI > env > config >
    class default), and parses ``argv`` with help/version/print-
    completion suppressed and subcommand descent skipped entirely, so a
    consumer can resolve config-file-driven command search paths (or any
    other global, including one backed by ``NS(env=...)`` or only made
    non-required by a layer) BEFORE building/committing to the full
    subcommand parser. This is the documented, public form of the internal
    prepass ``duho.app`` already runs -- it wraps
    :func:`duho.parsers.prerun_parse` verbatim rather than reimplementing the
    subparser-detach/terminal-action patching it performs and restores in a
    ``finally`` (this used to duplicate a buggy, dead-branch version of
    that same detach here; ``prerun_parse`` now does it once, correctly, for
    every caller).

    Returns the parsed root instance with globals set. Subcommand arguments are
    NOT validated in this pass: a missing subcommand does not error, and an
    unknown trailing token does not crash the globals parse (it is simply
    ignored here). A caller that also wants the leftover argv should call
    ``parser.parse_known_args`` directly -- ``parse_globals`` deliberately
    returns a single value (the globals-only instance), mirroring the shape
    ``prerun_parse`` yields.

    ``**parser_kwargs`` are forwarded to ``cls._parser_`` (e.g. ``add_help=False``),
    mirroring :func:`duho.parser`.
    """
    from .parsers import prerun_parse as _prerun_parse

    parser = cls._parser_(**parser_kwargs)
    _apply_layers(parser, cls, config=config)
    return _prerun_parse(parser, argv)


def finish_parse(namespace: "_argparse.Namespace") -> "Args":
    """Build the selected ``Cmd``/``Args`` instance from a ``Namespace``
    produced by attaching a duho subparser to a PLAIN, non-duho argparse
    parser -- the documented "manual subparsers" recipe
    (``Serve._parser_(subparsers, name="serve")`` attached to a
    hand-built ``argparse.ArgumentParser()`` root).

    A duho root (``Args._parser_``'s own patched ``parse_known_args``) does
    this automatically as part of normal dispatch: the internal ``"#cls"``
    marker (recording which subcommand class argparse selected) is popped and
    used to construct the real instance. A PLAIN argparse root has no such
    hook, so ``root.parse_args(...)`` returns a raw ``Namespace`` still
    carrying ``"#cls"`` -- not a ``Serve`` instance, with no ``_passthrough_``
    and no methods, and a stray ``"#cls"`` key that breaks
    ``func(**vars(args))``/``json.dumps(vars(args))``. Call this once, right
    after ``parse_args``, to get the real instance the manual recipe was
    always meant to produce.

    Raises ``ValueError`` if `namespace` carries no ``"#cls"`` marker (e.g. no
    duho subparser was ever reached for this invocation).
    """
    ns = vars(namespace)
    if "#cls" not in ns:
        raise ValueError(
            "finish_parse: no duho command was selected -- 'namespace' has "
            "no '#cls' marker. Pass the Namespace argparse.parse_args() "
            "returned when a duho `_parser_(subparsers, ...)` subcommand was "
            "attached to a plain argparse parser."
        )
    cls = ns.pop("#cls")
    return cls(**ns)


__all__ = [
    "Append",
    "Argument",
    "ArgumentBuilder",
    "ArgumentMeta",
    "Args",
    "Arg",
    "AUTO",
    "Choice",
    "Cli",
    "Cmd",
    "command",
    "Const",
    "Count",
    "Extend",
    "Factory",
    "finish_parse",
    "main",
    "Meta",
    "NS",
    "NOT_DEFINED",
    "parse",
    "parse_globals",
    "print_agent_help",
    "print_completion",
    "UpdateAction",
    "value_sources",
]
