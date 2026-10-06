from __future__ import annotations

import logging as _logging
import sys as _sys
import typing as _ty

from ..text import kebabcase as _kebabcase

from ._meta import AUTO

_LOGGER = _logging.getLogger(__package__)


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
_AUTO_VERSION_CACHE: dict[str, str | None] = {}


def _resolve_auto_version(dist: str) -> str | None:
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
    # `_version_ = duho.AUTO` triggers the load, and only at parser-build time,
    # once per distinct distribution name thanks to the cache above.
    import importlib.metadata as _importlib_metadata

    def _lookup(name: str) -> str | None:
        try:
            return _importlib_metadata.version(name)
        except _importlib_metadata.PackageNotFoundError:
            return None

    version: str | None = None
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


def _resolve_version(cls) -> str | None:
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

    A CLASS-derived name (the ``__name__`` fallback -- never an explicit
    ``_parsername_``, which is a user's own literal choice and passes through
    verbatim) is kebab-cased (:func:`duho.text.kebabcase`): ``BuildPyz`` ->
    ``build-pyz``. This was always the intended behaviour -- the class-name
    fallback previously returned the exact class name, mixed-case included.
    """
    explicit = vars(command).get("_parsername_")
    if explicit:
        return explicit
    class_name = getattr(command, "__name__", "")
    return _kebabcase(class_name) if class_name else ""


def _app_name(cls: type | None, name: str | None = None) -> str:
    """The one name an application goes by on every surface: the usage
    line, the completion script, the ``<NAME>_MCP`` variable, MCP tool names
    and the default logger. Independent of how the program was launched.

    Precedence: ``name`` (``app(name=...)``), then ``cls``'s OWN
    ``_parsername_``, then its top-level import package (unless that is
    ``__main__`` or ``duho`` itself), then its kebab-case class name.
    ``"app"`` when there is no ``cls``.
    """
    if name:
        return name
    if cls is None:
        return "app"
    own = vars(cls).get("_parsername_")
    if own:
        return own
    package = _top_level_dist_name(cls)
    if package and package not in ("__main__", "duho"):
        return package
    return _command_name(cls)


def _default_long_flag(name: str) -> str:
    """A field's default long flag: ``"--" + kebabcase(name)``.

    Kebab-case (:func:`duho.text.kebabcase`), not the older plain
    ``name.replace("_", "-")`` -- so a camelCase/acronym field name gets a
    real kebab flag too (``testMe`` -> ``--test-me``, ``HTTPPort`` ->
    ``--http-port``), while a already-snake_case name is unaffected
    (``dry_run`` -> ``--dry-run``, same as before). Never applied to an
    explicitly spelled flag, the field's own attribute/dest name, or a
    config/env key -- only this ONE derived default, and the ``"--"``
    shorthand below that expands to it.
    """
    return "--" + _kebabcase(name)


def _expand_flag_shorthand(
    name: str, flags: _ty.Sequence[str], default_flag: str
) -> tuple[str, ...]:
    """Expand a bare ``"--"`` entry in a declared flag tuple to
    ``default_flag`` (the field's default long flag). Any other entry passes
    through unchanged (an explicitly spelled flag is never rewritten). A
    tuple containing ``"--"`` more than once is a build-time ``ValueError``
    naming the field -- there is only one default long flag to expand to.
    """
    count = sum(1 for flag in flags if flag == "--")
    if count > 1:
        raise ValueError(
            f'argument {name!r}: the "--" flag shorthand can appear at '
            f"most once in a flag tuple, got {tuple(flags)!r}"
        )
    if count == 0:
        return tuple(flags)
    return tuple(default_flag if flag == "--" else flag for flag in flags)
