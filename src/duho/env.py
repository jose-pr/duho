"""Prefixed, app-wide environment accessor.

An :class:`Env` presents a single typed view over ``os.environ`` keys sharing a
common prefix (``MYAPP_DEBUG``, ``MYAPP_CMDS_PATH``, ...), plus an optional
companion ``<prefix>env`` module of defaults an app may ship.

Distinct from the per-field ``NS(env=...)`` default layer (Plan 05): that resolves
one argparse field; this is the app-level accessor a driver reads settings through
(command search paths, ``DEBUG``, import hooks).

All union annotations are quoted so the module imports cleanly on Python 3.9,
where an unquoted PEP-604 ``X | Y`` in a signature evaluates at def time and
raises ``TypeError``.
"""

import collections.abc as _abc
import importlib as _importlib
import os as _os
import typing as _ty

_T = _ty.TypeVar("_T")

__all__ = ["Env"]


class Env(_abc.MutableMapping):
    """A prefixed, mutable view over ``os.environ`` with typed accessors.

    ``Env("my-app")`` reads keys named ``MY_APP_<KEY>`` from the process
    environment (the prefix is uppercased, ``-`` -> ``_``, and a trailing ``_``
    is ensured). An empty prefix reads bare environment keys.

    On construction, when ``autoload`` is true (the default), the accessor tries
    to import a companion ``<prefix-lower>env`` module (e.g. ``my_app_env``) and
    seeds its **upper-case, non-underscore** module variables as *defaults* --
    genuinely lowest-precedence, consulted only when a key is set neither
    explicitly (``**env`` kwargs / a runtime ``env[k] = v`` write) nor in the
    real process environment. Precedence, highest first:
    ``**env`` kwargs / runtime writes  >  real ``os.environ``  >  the
    ``<prefix>env`` companion module's shipped defaults. All seeded values --
    module and kwargs -- are ``str()``-coerced, so ``env.bool``/``env.list``
    never see a raw non-string.

    SECURITY: autoloading imports ``<prefix-lower>env`` from anywhere on
    ``sys.path`` (which normally includes the current working directory), so a
    hostile ``<prefix>env.py`` in the CWD would run its module body. Pass
    ``autoload=False`` to disable the import entirely if the prefix is not fully
    under your control.
    """

    def __init__(self, prefix: str, autoload: bool = True, **env: object) -> None:
        prefix = prefix.upper().replace("-", "_")
        if prefix and not prefix.endswith("_"):
            prefix += "_"
        self.prefix = prefix

        #: Explicit values: `**env` kwargs and any later `env[k] = v` runtime
        #: write. Outranks BOTH the real environment and the companion
        #: module -- a caller/runtime write is a deliberate override.
        self._env: "dict[str, object]" = {}
        #: Companion-module-seeded values. Genuinely lowest precedence: a
        #: shipped default must never shadow a real exported environment
        #: variable (that inversion was a bug -- see module CHANGELOG entry).
        #: Kept separate from `self._env` so `__getitem__` can consult
        #: `os.environ` BEFORE falling back to this layer.
        self._defaults: "dict[str, object]" = {}
        if autoload:
            try:
                module = _importlib.import_module(f"{prefix.lower()}env")
            except ImportError:
                # A missing companion module is normal, not an error: an app may
                # or may not ship a "<prefix>env.py" of defaults.
                pass
            else:
                for key, value in vars(module).items():
                    # Only real settings: skip dunders/private and lower-case
                    # helpers/imports (``__builtins__``, ``os``, a ``_helper``),
                    # matching the UPPER_CASE env-var convention.
                    if key.isupper() and not key.startswith("_"):
                        self._defaults[key] = str(value)
        for key, value in env.items():
            self[key] = value

    # -- MutableMapping protocol ------------------------------------------

    def __getitem__(self, key: str) -> str:
        if key in self._env:
            return self._env[key]
        envkey = f"{self.prefix}{key}"
        if envkey in _os.environ:
            return _os.environ[envkey]
        return self._defaults[key]

    def __setitem__(self, key: str, value: object) -> None:
        self._env[key] = str(value)

    def __delitem__(self, key: str) -> None:
        del self._env[key]

    def __iter__(self) -> "_ty.Iterator[str]":
        seen: "set[str]" = set()
        for key in self._env:
            seen.add(key)
            yield key
        for key in _os.environ:
            if key.startswith(self.prefix):
                stripped = key[len(self.prefix):]
                if stripped not in seen:
                    seen.add(stripped)
                    yield stripped
        for key in self._defaults:
            if key not in seen:
                yield key

    def __len__(self) -> int:
        # A generator has no ``__len__``; count the unique keys instead.
        return sum(1 for _ in self)

    # -- Typed accessors --------------------------------------------------

    def bool(self, key: str) -> bool:
        """Return ``key`` interpreted as a boolean.

        Truthy values (case-insensitive) are ``1``, ``true``, ``yes``, ``y``,
        ``t``; anything else (including a missing key) is ``False``.
        """
        return self.get(key, "0").lower() in {"1", "true", "yes", "y", "t"}

    def list(
        self, key: str, sep: str = ":", ty: "_ty.Callable[[str], _T]" = str
    ) -> "list[_T]":
        """Return ``key`` split on ``sep`` with ``ty`` applied to each part.

        A missing or empty value yields ``[]`` -- an empty list, NOT ``[ty("")]``.
        The old single-empty-string contract turned a missing ``<PREFIX>_CMDS_PATH``
        into ``[Path("")] == [Path(".")]`` and glob-imported the whole CWD (C11);
        ``[""]`` as "one empty path" has no legitimate use.
        """
        raw = self.get(key, "")
        if not raw:
            return []
        return [ty(part) for part in raw.split(sep)]

    def paths(
        self, key: str, ty: "_ty.Callable[[str], _T]" = str
    ) -> "list[_T]":
        """Return a path-list env var (e.g. ``CMDS_PATH``) split on the OS separator.

        Unlike :meth:`list` (whose ``sep`` defaults to ``":"`` for generic lists
        like ``HOSTS``), this splits on the **platform path-list separator** --
        ``os.pathsep`` (``";"`` on Windows, ``":"`` on POSIX) -- so an absolute
        Windows path's drive-letter colon (``C:\\...``) is never mis-split into a
        bogus ``C`` entry. A ``PATHSEP`` environment variable overrides the
        separator when set (``sep = os.environ.get("PATHSEP") or os.pathsep``),
        so a caller can force a separator regardless of platform. Missing/empty
        yields ``[]``, exactly like :meth:`list`.
        """
        sep = _os.environ.get("PATHSEP") or _os.pathsep
        return self.list(key, sep=sep, ty=ty)
