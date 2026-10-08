"""The JSON backend."""

from __future__ import annotations

import typing as _ty

from ..exceptions import ConfigError
from ._backend import ConfigBackend


class JSONBackend(ConfigBackend):
    """JSON through the standard library."""

    name = "json"
    suffixes = (".json",)

    def loads(self, text: str) -> _ty.Any:
        import json

        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(str(exc), lineno=exc.lineno, colno=exc.colno) from None
        except RecursionError:
            raise ConfigError("nested too deeply") from None

    def dumps(self, data: _ty.Any) -> str:
        import json

        try:
            return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"cannot write as JSON: {exc}") from None
