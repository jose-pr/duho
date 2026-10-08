"""The INI backend."""

from __future__ import annotations

import typing as _ty

from ..exceptions import ConfigError
from ._backend import ConfigBackend, _plain

# A section name no file can hold, so configparser's own [DEFAULT] handling
# (every section inheriting its keys) is off and [DEFAULT] reads as plain keys.
_NO_DEFAULTS = "\0"
_TOP = "DEFAULT"


class INIBackend(ConfigBackend):
    """INI through ``configparser``, read as plain text with no interpolation.

    The keys of ``[DEFAULT]`` are the top level, every other section is a table
    one level deep, sections do not inherit ``[DEFAULT]``, key case is kept and
    ``%`` is text. Every value is a string.
    """

    name = "ini"
    suffixes = (".ini", ".cfg")

    def loads(self, text: str) -> _ty.Any:
        import configparser

        parser = configparser.ConfigParser(
            interpolation=None, default_section=_NO_DEFAULTS
        )
        parser.optionxform = str  # type: ignore[assignment,method-assign]
        try:
            parser.read_string(text)
        except configparser.MissingSectionHeaderError as exc:
            raise ConfigError(
                "no section header before the first key", lineno=exc.lineno
            ) from None
        except configparser.ParsingError as exc:
            errors = getattr(exc, "errors", None) or []
            raise ConfigError(
                "not a key and value", lineno=errors[0][0] if errors else None
            ) from None
        except configparser.DuplicateSectionError as exc:
            raise ConfigError(
                f"section [{_plain(exc.section)}] is defined twice", lineno=exc.lineno
            ) from None
        except configparser.DuplicateOptionError as exc:
            raise ConfigError(
                f"key {_plain(exc.option)!r} is defined twice in "
                f"[{_plain(exc.section)}]",
                lineno=exc.lineno,
            ) from None
        except configparser.Error:
            raise ConfigError("not valid INI") from None
        result: "dict[str, _ty.Any]" = {}
        if parser.has_section(_TOP):
            result.update(parser.items(_TOP))
        for section in parser.sections():
            if section != _TOP:
                result[section] = dict(parser.items(section))
        return result

    def dumps(self, data: _ty.Any) -> str:
        import configparser
        import io

        if not isinstance(data, _ty.Mapping):
            raise ConfigError("cannot write as INI: the top level must be a table")
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str  # type: ignore[assignment,method-assign]
        for key, value in data.items():
            if isinstance(value, _ty.Mapping):
                if str(key) == _TOP:
                    raise ConfigError(f"cannot write as INI: a table named {_TOP}")
                parser.add_section(str(key))
                for sub, item in value.items():
                    parser.set(str(key), str(sub), _scalar(f"{key}.{sub}", item))
            else:
                parser.set(_TOP, str(key), _scalar(str(key), value))
        out = io.StringIO()
        parser.write(out)
        return out.getvalue()


def _scalar(where: str, value: _ty.Any) -> str:
    """A scalar as INI text; anything else is an error naming ``where``."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    raise ConfigError(
        f"cannot write as INI: {where} holds {type(value).__name__}, "
        "not a string, number or bool"
    )
