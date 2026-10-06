from __future__ import annotations

import logging as _logging
import sys as _sys
import typing as _ty

from ..text import kebabcase as _kebabcase

from ._meta import AUTO

_LOGGER = _logging.getLogger(__package__)


def _top_level_dist_name(cls) -> str:
    """The distribution-lookup name for `cls`: its top-level import package.

    A module run as ``python -m pkg`` has ``cls.__module__ == "__main__"``, which
    ``importlib.metadata.version`` cannot find; the real package is recovered
    from ``sys.modules["__main__"].__spec__.name``.
    """
    module_name = cls.__module__
    if module_name == "__main__":
        main_module = _sys.modules.get("__main__")
        spec = getattr(main_module, "__spec__", None)
        spec_name = getattr(spec, "name", None)
        if spec_name:
            return spec_name.split(".")[0]
    return module_name.split(".")[0]


#: Cache for `duho.AUTO` version resolution by distribution name: it is resolved
#: at every parser build but cannot change mid-process, so the
#: `importlib.metadata` scan runs once. A not-found `None` is cached too.
_AUTO_VERSION_CACHE: dict[str, _ty.Optional[str]] = {}


def _resolve_auto_version(dist: str) -> str | None:
    """Resolve (and cache) ``importlib.metadata.version(dist)``; never raises.

    3.10+ normalizes names per PEP 503 and 3.9 does not treat ``.`` as a
    separator, while build backends write ``*.dist-info`` with underscores. So a
    dotted ``_distribution_`` is retried once with ``-``/``_``/``.`` runs
    collapsed to ``_``; the verbatim lookup stays first, since a legacy dotted
    dist-info directory only matches it.
    """
    if dist in _AUTO_VERSION_CACHE:
        return _AUTO_VERSION_CACHE[dist]

    # Imported lazily so a plain `import duho` never pays importlib.metadata's
    # ~30 ms; only a class using `duho.AUTO` loads it.
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

    ``_version_`` may be a str (used as-is), ``AUTO`` (resolved from
    ``_distribution_`` or the top-level package), or unset, in which case a
    class-level ``__version__`` str is the fallback. Never raises: a failure is
    logged at debug level and means "no version".
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
    """A command's subcommand name, the one rule shared by every reader.

    A class command resolves its OWN ``_parsername_`` through ``vars(command)``,
    not ``getattr``: the MRO would let a subclass inherit its base's name, and
    siblings would collapse onto one. A subclass that wants to share the name
    declares it again. A module command's name is read the same way (it is an
    instance attribute). The fallback is the kebab-cased class name
    (``BuildPyz`` -> ``build-pyz``); an explicit ``_parsername_`` is verbatim.
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

    ``testMe`` -> ``--test-me``, ``dry_run`` -> ``--dry-run``. Applied only to
    this derived default and the ``"--"`` shorthand, never to an explicit flag,
    the dest, or a config/env key.
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
