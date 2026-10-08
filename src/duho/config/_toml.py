"""The TOML backend."""

from __future__ import annotations

import re as _re
import typing as _ty

from ..exceptions import ConfigDependencyError, ConfigError
from ._backend import ConfigBackend

_POSITION = _re.compile(r"\(at line (\d+), column (\d+)\)")


def _reader() -> _ty.Any:
    try:
        import tomllib  # type: ignore[import-not-found]

        return tomllib
    except ImportError:
        pass
    try:
        import tomli  # type: ignore[import-not-found]

        return tomli
    except ImportError:
        raise ConfigDependencyError(
            "duho: reading a config file requires a TOML backend. "
            "Python 3.11+ has one built in (tomllib); on earlier "
            "versions, install the optional 'tomli' package "
            "(e.g. `pip install tomli` or `pip install duho[config]`).",
            extra="config",
        ) from None


class TOMLBackend(ConfigBackend):
    """TOML: ``tomllib`` (or ``tomli``) reads, ``tomli_w`` writes."""

    name = "toml"
    suffixes = (".toml",)
    extra = "config"

    def loads(self, text: str) -> _ty.Any:
        reader = _reader()
        try:
            return reader.loads(text)
        except reader.TOMLDecodeError as exc:
            message = str(exc)
            lineno = getattr(exc, "lineno", None)
            colno = getattr(exc, "colno", None)
            found = _POSITION.search(message)
            if lineno is None and found is not None:
                lineno, colno = int(found.group(1)), int(found.group(2))
            raise ConfigError(message, lineno=lineno, colno=colno) from None
        except RecursionError:
            raise ConfigError("nested too deeply") from None

    def dumps(self, data: _ty.Any) -> str:
        try:
            import tomli_w
        except ImportError:
            raise ConfigDependencyError(
                "duho: writing TOML requires the optional 'tomli-w' package "
                "(e.g. `pip install tomli-w` or `pip install duho[config]`).",
                extra="config",
            ) from None
        try:
            return tomli_w.dumps(data)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"cannot write as TOML: {exc}") from None
