"""The backend contract, the registry of formats and the module functions."""

from __future__ import annotations

import os as _os
import pathlib as _pathlib
import typing as _ty

from ..exceptions import (
    ConfigDependencyError,
    ConfigError,
    UnsupportedFormatError,
)

#: Registered backend classes by primary name, in registration order.
_REGISTRY: "dict[str, type]" = {}

_PROBLEM_LIMIT = 120


def _plain(text: str) -> str:
    """A parser's problem text as one bounded line."""
    line = " ".join(str(text).split())
    if len(line) <= _PROBLEM_LIMIT:
        return line
    return line[: _PROBLEM_LIMIT - 3] + "..."


def _claims(obj: _ty.Any) -> "tuple[list[str], list[str]]":
    """The lowercase names (primary first) and suffixes ``obj`` answers to."""
    names = [obj.name.lower(), *(a.lower() for a in obj.aliases)]
    return names, [s.lower() for s in obj.suffixes]


def _register(cls: type, replace: bool) -> None:
    names, suffixes = _claims(cls)
    clashes = []
    for other in _REGISTRY.values():
        other_names, other_suffixes = _claims(other)
        if set(names) & set(other_names) or set(suffixes) & set(other_suffixes):
            clashes.append(other)
    if clashes and not replace:
        raise ValueError(
            f"{cls.__qualname__}: {', '.join(c.__qualname__ for c in clashes)} "
            "already holds one of its names or suffixes; pass replace=True to "
            "take them over"
        )
    for other in clashes:
        del _REGISTRY[other.name.lower()]
    _REGISTRY[names[0]] = cls


class ConfigBackend:
    """One configuration format: reads and writes a document as a plain value.

    Subclass it, set ``name`` in the class body and implement ``loads`` and
    ``dumps``: the class is then registered and every ``duho.config`` function
    and every config file duho reads can use it. ``replace=True`` in the class
    statement takes over the names and suffixes of backends already registered
    (the replaced class leaves the registry whole). A subclass that sets no
    ``name`` of its own is not registered.

    An instance may carry other ``name``, ``aliases`` and ``suffixes`` than its
    class: ``YAMLBackend(suffixes=(".conf",))``. Names, aliases and suffixes are
    lowercase and a suffix carries its dot; the suffix ``""`` matches any file
    name. ``extra`` names the pip extra a backend's library comes with.
    """

    name: str
    aliases: "_ty.Tuple[str, ...]" = ()
    suffixes: "_ty.Tuple[str, ...]" = ()
    extra: _ty.Optional[str] = None

    def __init_subclass__(cls, *, replace: bool = False, **kwargs: _ty.Any) -> None:
        super().__init_subclass__(**kwargs)
        if "name" in cls.__dict__:
            _register(cls, replace)

    def __init__(
        self,
        *,
        name: _ty.Optional[str] = None,
        aliases: _ty.Optional[_ty.Iterable[str]] = None,
        suffixes: _ty.Optional[_ty.Iterable[str]] = None,
    ) -> None:
        if name is not None:
            self.name = name
        if aliases is not None:
            self.aliases = tuple(aliases)
        if suffixes is not None:
            self.suffixes = tuple(suffixes)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r})"

    def loads(self, text: str) -> _ty.Any:
        """Parse ``text``; a bad document raises ``ConfigError`` with no document text."""
        raise NotImplementedError(f"{type(self).__name__} does not implement loads()")

    def dumps(self, data: _ty.Any) -> str:
        """``data`` as the text of a document."""
        raise NotImplementedError(f"{type(self).__name__} does not implement dumps()")

    def load(self, source: _ty.Any) -> _ty.Any:
        """Read a document from a path (``~`` expanded) or a file object.

        The bytes are strict UTF-8 and one leading byte-order mark is dropped.
        A parse failure is a ``ConfigError`` that names the format and the file.
        """
        if hasattr(source, "read"):
            shown = getattr(source, "name", None)
            shown = shown if isinstance(shown, str) else "<stream>"
            raw = source.read()
        else:
            path = _pathlib.Path(source).expanduser()
            shown = _os.fspath(path)
            with path.open("rb") as handle:
                raw = handle.read()
        label = self.name.upper()
        if isinstance(raw, (bytes, bytearray)):
            try:
                raw = bytes(raw).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ConfigError(
                    f"duho: invalid {label} in config file {shown}: "
                    f"not valid UTF-8 (byte offset {exc.start})",
                    path=shown,
                ) from None
        text = raw[1:] if raw.startswith("\ufeff") else raw
        try:
            return self.loads(text)
        except ConfigDependencyError:
            raise
        except ConfigError as exc:
            raise ConfigError(
                f"duho: invalid {label} in config file {shown}: {exc.message}",
                path=shown,
                lineno=exc.lineno,
                colno=exc.colno,
            ) from None

    def dump(self, data: _ty.Any, target: _ty.Any) -> None:
        """Write ``data`` to a path (``~`` expanded) or a text file object.

        The document is built first, so a failure leaves an existing file as it was.
        """
        text = self.dumps(data)
        if hasattr(target, "write"):
            target.write(text)
            return
        path = _pathlib.Path(target).expanduser()
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)


_Item = _ty.Union[str, ConfigBackend, _ty.Type[ConfigBackend]]
_Backends = _ty.Optional[_ty.Iterable[_Item]]


def _registered_names() -> "dict[str, type]":
    names: "dict[str, type]" = {}
    for cls in _REGISTRY.values():
        names.update(dict.fromkeys(_claims(cls)[0], cls))
    return names


def _instances(backends: _Backends) -> "list[ConfigBackend]":
    """The backends of a set, in order; ``None`` is every registered one."""
    if backends is None:
        return [cls() for cls in _REGISTRY.values()]
    if isinstance(backends, (str, ConfigBackend, type)):
        backends = [backends]  # type: ignore[list-item]
    names = _registered_names()
    out: "list[ConfigBackend]" = []
    for item in backends:
        if isinstance(item, str):
            cls = names.get(item.lower())
            if cls is None:
                raise UnsupportedFormatError(
                    f"duho: no config backend named {item!r}; registered: "
                    f"{', '.join(sorted(_REGISTRY))}"
                )
            out.append(cls())
        elif isinstance(item, type) and issubclass(item, ConfigBackend):
            out.append(item())
        elif isinstance(item, ConfigBackend):
            out.append(item)
        else:
            raise TypeError(
                "a config backend set holds names, ConfigBackend classes and "
                f"instances, not {item!r}"
            )
    return out


def _index(
    backends: _Backends,
) -> "tuple[dict[str, ConfigBackend], dict[str, ConfigBackend], list[ConfigBackend]]":
    """Name and suffix tables of a set; a later item wins a claim."""
    items = _instances(backends)
    by_name: "dict[str, ConfigBackend]" = {}
    by_suffix: "dict[str, ConfigBackend]" = {}
    for item in items:
        names, suffixes = _claims(item)
        by_name.update(dict.fromkeys(names, item))
        by_suffix.update(dict.fromkeys(suffixes, item))
    return by_name, by_suffix, items


def backend_names(*, backends: _Backends = None) -> "tuple[str, ...]":
    """The primary names of the backends in the set, sorted."""
    _, _, items = _index(backends)
    return tuple(sorted({item.name.lower() for item in items}))


def get_backend(name: str, *, backends: _Backends = None) -> ConfigBackend:
    """The backend answering to ``name`` or an alias, in any case."""
    by_name, _, items = _index(backends)
    found = by_name.get(str(name).lower())
    if found is None:
        raise UnsupportedFormatError(
            f"duho: no config format named {name!r}; use one of "
            f"{', '.join(sorted({i.name.lower() for i in items}))}"
        )
    return found


def backend_for(
    path: "_ty.Union[str, _os.PathLike[str]]",
    format: _ty.Optional[str] = None,
    *,
    default: _ty.Optional[str] = None,
    backends: _Backends = None,
) -> ConfigBackend:
    """The backend for a file: ``format`` if given, else the longest suffix its
    name ends with (case ignored), else ``default``.

    No match and no ``default`` is an ``UnsupportedFormatError`` listing the
    accepted suffixes.
    """
    if format is not None:
        return get_backend(format, backends=backends)
    _, by_suffix, _ = _index(backends)
    file_name = _pathlib.PurePath(_os.fspath(path)).name.lower()
    matches = [s for s in by_suffix if file_name.endswith(s)]
    if matches:
        return by_suffix[max(matches, key=len)]
    if default is not None:
        return get_backend(default, backends=backends)
    accepted = ", ".join(sorted(s for s in by_suffix if s)) or "none"
    raise UnsupportedFormatError(
        f"duho: cannot tell the config format of {_os.fspath(path)!r}; "
        f"accepted suffixes: {accepted}"
    )


def _name_of(stream: _ty.Any) -> str:
    name = getattr(stream, "name", None)
    if isinstance(name, (str, _os.PathLike)):
        return _os.fspath(name)
    raise UnsupportedFormatError(
        "duho: a file object with no name needs an explicit format"
    )


def loads(text: str, format: str, *, backends: _Backends = None) -> _ty.Any:
    """Parse ``text`` as the named format."""
    return get_backend(format, backends=backends).loads(text)


def load(
    source: _ty.Any, format: _ty.Optional[str] = None, *, backends: _Backends = None
) -> _ty.Any:
    """Read a file (a path or a file object); the format is ``format`` or the file name's."""
    where = _name_of(source) if hasattr(source, "read") and not format else source
    return backend_for(where, format, backends=backends).load(source)


def dumps(data: _ty.Any, format: str, *, backends: _Backends = None) -> str:
    """``data`` as a document of the named format."""
    return get_backend(format, backends=backends).dumps(data)


def dump(
    data: _ty.Any,
    target: _ty.Any,
    format: _ty.Optional[str] = None,
    *,
    backends: _Backends = None,
) -> None:
    """Write ``data`` to a file (a path or a text file object)."""
    where = _name_of(target) if hasattr(target, "write") and not format else target
    backend_for(where, format, backends=backends).dump(data, target)
