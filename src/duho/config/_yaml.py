"""The YAML backend."""

from __future__ import annotations

import typing as _ty

from ..exceptions import ConfigDependencyError, ConfigError
from ._backend import ConfigBackend, _plain


def _module() -> _ty.Any:
    try:
        import yaml
    except ImportError:
        raise ConfigDependencyError(
            "duho: reading or writing YAML requires the optional 'PyYAML' "
            "package (e.g. `pip install PyYAML` or `pip install duho[yaml]`).",
            extra="yaml",
        ) from None
    return yaml


class YAMLBackend(ConfigBackend):
    """YAML through PyYAML's safe loader and dumper."""

    name = "yaml"
    aliases = ("yml",)
    suffixes = (".yaml", ".yml")
    extra = "yaml"

    def loads(self, text: str) -> _ty.Any:
        yaml = _module()
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            problem = getattr(exc, "problem", None)
            lineno = None if mark is None else mark.line + 1
            colno = None if mark is None else mark.column + 1
            detail = _plain(problem) if problem else "not valid YAML"
            if lineno is not None:
                detail += f" (at line {lineno}, column {colno})"
            raise ConfigError(detail, lineno=lineno, colno=colno) from None
        except RecursionError:
            raise ConfigError("nested too deeply") from None
        except (ValueError, TypeError, OverflowError):
            raise ConfigError("a value that is not valid YAML") from None

    def dumps(self, data: _ty.Any) -> str:
        yaml = _module()
        try:
            return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
        except yaml.YAMLError:
            # PyYAML's own text quotes the value it could not represent.
            raise ConfigError(
                "cannot write as YAML: a value is not a plain YAML type"
            ) from None
