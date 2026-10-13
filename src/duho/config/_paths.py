from __future__ import annotations

import ntpath as _ntpath
import os as _os
import typing as _ty


def _config_home(nt: bool, environ: _ty.Mapping[str, str], home: str) -> str:
    """The platform's per-user configuration directory for the given environment.

    Windows: ``APPDATA`` when set to an absolute path, else
    ``~/AppData/Roaming``. Elsewhere: ``XDG_CONFIG_HOME`` when set to an
    absolute path (the XDG base directory rule: a relative or empty value is
    ignored), else ``~/.config``.
    """
    if nt:
        appdata = environ.get("APPDATA", "")
        if appdata and _ntpath.isabs(appdata):
            return appdata
        return _os.path.join(home, "AppData", "Roaming")
    xdg = environ.get("XDG_CONFIG_HOME", "")
    if xdg and _os.path.isabs(xdg):
        return xdg
    return _os.path.join(home, ".config")


class _UserPath(_os.PathLike[str]):
    """The path :func:`user_path` returns: resolved afresh at every ``fspath``."""

    __slots__ = ("_parts", "_nt", "_environ", "_home")

    def __init__(
        self,
        parts: tuple[str, ...],
        nt: _ty.Optional[bool] = None,
        environ: _ty.Optional[_ty.Mapping[str, str]] = None,
        home: _ty.Optional[str] = None,
    ) -> None:
        # nt/environ/home are None in use and read live; tests pin them.
        self._parts = parts
        self._nt = nt
        self._environ = environ
        self._home = home

    def __fspath__(self) -> str:
        environ = _os.environ if self._environ is None else self._environ
        home = _os.path.expanduser("~") if self._home is None else self._home
        nt = _os.name == "nt" if self._nt is None else self._nt
        return _os.path.join(_config_home(nt, environ, home), *self._parts)

    def __str__(self) -> str:
        return self.__fspath__()

    def __repr__(self) -> str:
        return f"user_path({', '.join(repr(p) for p in self._parts)})"


def user_path(app: str, *names: str) -> "_os.PathLike[str]":
    """The per-user configuration path ``<config dir>/<app>/<names...>``.

    The result resolves each time it is used as a path, so it is safe as a class
    attribute (``_config_ = user_path("tool", "config.toml")``): the environment
    is read when the file is looked up, not when the class is defined. The
    configuration directory is ``APPDATA`` (when absolute, else
    ``~/AppData/Roaming``) on Windows and ``XDG_CONFIG_HOME`` (when absolute,
    else ``~/.config``) elsewhere.
    ``str(path)`` is the resolved path; ``repr`` shows the call.

    Raises ``ValueError`` when ``app`` is empty.
    """
    if not app:
        raise ValueError("user_path: app must be a non-empty name")
    return _UserPath((app, *names))
