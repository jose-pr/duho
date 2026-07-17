"""Dotted-name algebra for command qualnames.

A :class:`QualName` base defines the parts/parent/join/split algebra, a
:class:`DotQualNamed` mixes it into ``str`` with ``.`` as the separator, and
:class:`PythonName`, whose :meth:`PythonName.new` runs parts through
:func:`duho.text.pysafe`.

All union annotations are quoted so the module imports cleanly on Python 3.9.
"""

import functools as _functools
import pathlib as _pathlib
import typing as _ty

from . import text as _text

__all__ = ["QualName", "DotQualNamed", "PythonName"]

_P = _ty.TypeVar("_P", bound=_pathlib.PurePath)


class QualName:
    """Abstract dotted-name algebra: parts, parent, join/split, path mapping."""

    @property
    def parts(self) -> "_ty.Sequence[str]":
        raise NotImplementedError()

    @_functools.cached_property
    def name(self) -> str:
        return self.parts[-1]

    @_functools.cached_property
    def parent(self) -> "QualName":
        return self.qualjoin(self.parts[:-1])

    @classmethod
    def _qualparts(
        cls, *parts: "str | _ty.Iterable[str] | QualName"
    ) -> "list[str]":
        _parts: "list[str]" = []
        for part in parts:
            if hasattr(part, "parts"):
                _parts.extend(_ty.cast(QualName, part).parts)
            elif isinstance(part, str):
                _parts.append(part)
            else:
                _parts.extend(_ty.cast(list, part))
        return [part for part in _parts if part]

    @classmethod
    def qualjoin(cls, *parts: "str | _ty.Iterable[str] | QualName") -> "QualName":
        return cls._qualjoin(cls._qualparts(*parts))

    @classmethod
    def qualsplit(cls, name: "str | QualName") -> "_ty.Sequence[str]":
        if hasattr(name, "parts"):
            return _ty.cast(QualName, name).parts
        return cls._qualsplit(_ty.cast(str, name))

    def with_name(self, name: str) -> "QualName":
        return self.qualjoin(self.parent, name)

    @classmethod
    def _qualsplit(cls, name: str) -> "list[str]":
        raise NotImplementedError()

    @classmethod
    def _qualjoin(cls, parts: "list[str]") -> "QualName":
        raise NotImplementedError()

    def __truediv__(self, key: "str | _ty.Iterable[str] | QualName") -> "QualName":
        return self.qualjoin(self, key)

    def relative_to(self, name: "QualName") -> "QualName":
        parts = [*self.parts]
        other = name.parts

        if len(other) > len(parts):
            raise ValueError(other)

        idx = 0

        for idx, part in enumerate(other):
            if parts[idx] != part:
                raise ValueError(other, idx)

        return self.qualjoin(*parts[idx + 1 :])

    def camelcase(
        self,
        start: int = 0,
        end: "int | None" = None,
        *,
        separators: "str | _ty.Sequence[str] | None" = None,
    ) -> str:
        parts = self.parts
        camelcased = "".join(
            [
                part[0].upper() + part[1:]
                for part in parts[start : len(parts) if end is None else end]
            ]
        )
        return _text.camelcase(camelcased, separators=separators)

    def as_path(self, root: "str | _P" = "/") -> "_P":
        if not hasattr(root, "joinpath"):
            root = _ty.cast(_P, _pathlib.PurePosixPath(root))

        return _ty.cast(_P, root).joinpath(*self.parts)


class DotQualNamed(QualName, str):
    """A :class:`QualName` that is also a ``str``, split on :attr:`SEPARATOR`."""

    SEPARATOR: str = "."

    @_functools.cached_property
    def parts(self) -> "_ty.Sequence[str]":  # type: ignore[override]
        return self._qualsplit(self)

    @classmethod
    def _qualsplit(cls, name: str) -> "list[str]":
        split = name.split(cls.SEPARATOR)
        if split == [""]:
            return []
        return split

    @classmethod
    def _qualjoin(cls, parts: "list[str]") -> "DotQualNamed":
        return cls(cls.SEPARATOR.join(parts))


class PythonName(DotQualNamed):
    """A dotted name whose parts are Python-safe (via :func:`duho.text.pysafe`)."""

    @classmethod
    def new(
        cls, *parts: "str | QualName", sanitize: bool = True
    ) -> "PythonName":
        name = cls.qualjoin(*parts)
        if sanitize:
            name = _text.pysafe(name)

        return PythonName(name)
