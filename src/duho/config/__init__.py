"""Read and write configuration documents in JSON, TOML, YAML and INI.

A format is a :class:`ConfigBackend`; the module functions pick one by name or
by file suffix. Importing this package loads no parser: each backend imports
its library when it is used.
"""

from __future__ import annotations

from ..exceptions import (
    ConfigDependencyError,
    ConfigError,
    UnsupportedFormatError,
)
from ._backend import (
    ConfigBackend,
    backend_for,
    backend_names,
    dump,
    dumps,
    get_backend,
    load,
    loads,
)
from ._ini import INIBackend
from ._json import JSONBackend
from ._toml import TOMLBackend
from ._yaml import YAMLBackend

__all__ = [
    "ConfigBackend",
    "ConfigDependencyError",
    "ConfigError",
    "INIBackend",
    "JSONBackend",
    "TOMLBackend",
    "UnsupportedFormatError",
    "YAMLBackend",
    "backend_for",
    "backend_names",
    "dump",
    "dumps",
    "get_backend",
    "load",
    "loads",
]
