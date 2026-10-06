"""Shared test isolation: global-state snapshot/restore fixtures.

Several duho internals are process-global by design (the discovery provider
registry, RunPath's own registration bookkeeping, the logging module's level
table, ``sys.modules``/``sys.path``, and assorted environment variables the
library reads). Before this file existed, every test module that touched one
of them re-implemented its own snapshot/restore fixture, the variants
disagreed, and the gaps let state leak between tests -- order-dependent
failures that only showed up under reordering or a `-k` selection. These
fixtures are autouse so no test has to remember to ask for them.
"""

from __future__ import annotations

import logging
import os
import sys
import sysconfig
from pathlib import Path

import pytest

from duho import discovery as _discovery
from duho import runpath as _runpath
from duho.logging import DefaultFormatter as _DefaultFormatter
from duho import logging as _duho_logging

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REPO_SRC = (_REPO_ROOT / "src").resolve()
_STDLIB_DIRS = tuple(
    Path(p).resolve()
    for p in {sysconfig.get_path("stdlib"), sysconfig.get_path("platstdlib")}
)


def _has_toml_backend() -> bool:
    """Whether a TOML reader is importable (stdlib ``tomllib`` or ``tomli``)."""
    try:
        import tomllib  # noqa: F401
    except ImportError:
        try:
            import tomli  # noqa: F401
        except ImportError:
            return False
    return True


def pytest_collection_modifyitems(config: pytest.Config, items) -> None:
    if _has_toml_backend():
        return
    skip_toml = pytest.mark.skip(
        reason="no TOML backend (needs Python 3.11+ or the tomli backport)"
    )
    for item in items:
        if "requires_toml" in item.keywords:
            item.add_marker(skip_toml)


def subprocess_env(*, extra_path=None, remove=(), extra=None) -> "dict[str, str]":
    """Build a subprocess environment with the working tree's ``src`` on PYTHONPATH.

    A child ``sys.executable`` otherwise resolves whatever ``duho`` the
    interpreter finds on its own -- a stale, non-editable site-packages copy
    on a venv that isn't installed in editable mode -- not the code under
    test. ``extra_path`` (a directory holding a fixture module) is put ahead
    of ``src`` on the child's PYTHONPATH; ``remove`` names variables dropped
    from the child's environment and ``extra`` maps variables added to it.
    """
    src = str(_REPO_SRC)
    parts = [src] if extra_path is None else [str(extra_path), src]
    env = dict(os.environ)
    for name in remove:
        env.pop(name, None)
    env.update(extra or {})
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


# --------------------------------------------------------------------------
# Command discovery / RunPath provider registry
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_command_providers():
    """Snapshot/restore the discovery provider registry and RunPath state.

    ``duho.discovery._PROVIDERS`` is a module-global list consulted by every
    directory-shaped command source; ``duho.runpath`` registers itself on
    import as a side effect and tracks what it registered in
    ``_REGISTERED`` (plus the configurable ``_BASE``/``_ADAPTER``). Restore
    the exact prior values rather than unconditionally unregistering, so a
    provider already registered before this test (whether from import time
    or an earlier test) survives into the next one.
    """
    saved_providers = list(_discovery._PROVIDERS)
    saved_registered = _runpath._REGISTERED
    saved_base = _runpath._BASE
    saved_adapter = _runpath._ADAPTER
    try:
        yield
    finally:
        _discovery._PROVIDERS[:] = saved_providers
        _runpath._REGISTERED = saved_registered
        _runpath._BASE = saved_base
        _runpath._ADAPTER = saved_adapter


# --------------------------------------------------------------------------
# sys.modules / sys.path
# --------------------------------------------------------------------------


def _is_loose_test_module(module: object) -> bool:
    """Whether ``module`` was loaded from outside stdlib/site-packages/src.

    That is exactly the set of modules a test fixture creates on the fly: a
    companion/config module written under ``tmp_path``, a directory-discovered
    command file, or an ``examples/`` script imported by name. Anything
    genuinely installed (the standard library, a site-packages dependency, or
    duho's own ``src`` tree) is left alone even if a test happens to be the
    first thing in the session to import it.
    """
    file = getattr(module, "__file__", None)
    if not file:
        return True  # namespace packages such as duho._discovered.*
    try:
        path = Path(file).resolve()
    except OSError:
        return False
    if _REPO_SRC in path.parents:
        return False
    if any(root in path.parents for root in _STDLIB_DIRS):
        return False
    if "site-packages" in path.parts:
        return False
    return True


@pytest.fixture(autouse=True)
def _isolate_sys_modules():
    """Drop modules a test loaded from a scratch location so they don't leak.

    A directory-discovered command, a companion config module, or a generated
    property-test fixture is imported under a synthesized name and cached in
    ``sys.modules`` for the rest of the process. Left alone, a later test
    that writes a same-named fixture silently reuses the earlier one's
    content instead of its own.
    """
    before = set(sys.modules)
    try:
        yield
    finally:
        for name in list(sys.modules):
            if name in before:
                continue
            if name.startswith("duho._discovered"):
                del sys.modules[name]
                continue
            module = sys.modules.get(name)
            if module is not None and _is_loose_test_module(module):
                del sys.modules[name]


@pytest.fixture(autouse=True)
def _isolate_sys_path():
    """Undo any ``sys.path`` entry a test added without cleaning up after itself."""
    before = list(sys.path)
    try:
        yield
    finally:
        sys.path[:] = before


# --------------------------------------------------------------------------
# Environment variables
# --------------------------------------------------------------------------

# Variables duho itself reads that are plausible to have set in a developer's
# or an agent host's outer shell, so a test asserting the "unset" default
# must not depend on the environment it happens to run in.
_RISKY_ENV_VARS = (
    "AGENT_HELP",
    "AGENTS_HELP",
    "PYTHON_COLORS",
    "NO_COLOR",
    "FORCE_COLOR",
    "PATHSEP",
    "DUHO_TRACEBACK",
    "PYTHONUTF8",
    "PYTHONIOENCODING",
)

# The terminal width argparse wraps help to; pinned so help-text assertions do
# not depend on the width of the terminal the suite is run from.
_PINNED_COLUMNS = "80"

# Env-var prefixes used by fixture ``Env`` instances across the suite (e.g.
# ``Env("ma")``); a stray same-prefixed variable in the outer environment
# would otherwise show up as an extra key.
_RISKY_ENV_PREFIXES = ("MA_", "ZZ_")


@pytest.fixture(autouse=True)
def _isolate_environ(monkeypatch: pytest.MonkeyPatch):
    for name in _RISKY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        # A `<PROG>_MCP` variable launches the in-process `duho.main` as an MCP
        # server, and the runner's own name (`PYTEST_MCP`) is such a variable.
        if name.startswith(_RISKY_ENV_PREFIXES) or name.endswith("_MCP"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("COLUMNS", _PINNED_COLUMNS)


# --------------------------------------------------------------------------
# Logging globals
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_logging_globals():
    """Snapshot/restore process-global logging state.

    ``add_logging_level`` installs new attributes on ``logging``/``Logger``
    and mutates the stdlib level-name tables; ``initverbose`` (which it
    calls) rebuilds duho's own ``-v``/``-q`` verbosity table from whatever
    levels are registered at the time. Without a restore, a level a test
    registers (or a color it assigns) outlives the test and can shift what
    later tests see ``-v`` map to.
    """
    logging_attrs_before = set(vars(logging))
    logger_cls = logging.getLoggerClass()
    logger_attrs_before = set(vars(logger_cls))
    name_to_level_before = dict(logging._nameToLevel)
    level_to_name_before = dict(logging._levelToName)
    colors_before = dict(_DefaultFormatter.COLORS)
    verbose_levels_before = dict(_duho_logging.VERBOSE_LEVELS)
    verbose_help_before = _duho_logging.VERBOSE_HELP
    levelsize_before = _duho_logging._LEVELSIZE
    root = logging.root
    root_handlers_before = list(root.handlers)
    root_level_before = root.level
    try:
        yield
    finally:
        for name in set(vars(logging)) - logging_attrs_before:
            delattr(logging, name)
        for name in set(vars(logger_cls)) - logger_attrs_before:
            delattr(logger_cls, name)
        logging._nameToLevel.clear()
        logging._nameToLevel.update(name_to_level_before)
        logging._levelToName.clear()
        logging._levelToName.update(level_to_name_before)
        _DefaultFormatter.COLORS.clear()
        _DefaultFormatter.COLORS.update(colors_before)
        _duho_logging.VERBOSE_LEVELS = verbose_levels_before
        _duho_logging.VERBOSE_HELP = verbose_help_before
        _duho_logging._LEVELSIZE = levelsize_before
        root.handlers[:] = root_handlers_before
        root.setLevel(root_level_before)
