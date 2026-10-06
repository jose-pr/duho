"""Dotted-name algebra for command qualnames.

A :class:`QualName` base defines the parts/parent/join/split algebra, a
:class:`DotQualNamed` mixes it into ``str`` with ``.`` as the separator, and
:class:`PythonName`, whose :meth:`PythonName.new` runs parts through
:func:`duho.text.pysafe`."""

from __future__ import annotations

import functools as _functools
import pathlib as _pathlib
import typing as _ty

from . import text as _text

__all__ = ["QualName", "DotQualNamed", "PythonName"]

_P = _ty.TypeVar("_P", bound=_pathlib.PurePath)


class QualName:
    """Abstract dotted-name algebra: parts, parent, join/split, path mapping."""

    @property
    def parts(self) -> _ty.Sequence[str]:
        """The name's parts, most-significant first (e.g. ``("a", "b", "c")``)."""
        raise NotImplementedError()

    @_functools.cached_property
    def name(self) -> str:
        """The last part (e.g. ``"c"`` for ``"a.b.c"``)."""
        return self.parts[-1]

    @_functools.cached_property
    def parent(self) -> QualName:
        """The name with its last part dropped (e.g. ``"a.b"`` for ``"a.b.c"``)."""
        return self.qualjoin(self.parts[:-1])

    @classmethod
    def _qualparts(cls, *parts: str | _ty.Iterable[str] | QualName) -> list[str]:
        _parts: list[str] = []
        for part in parts:
            if hasattr(part, "parts"):
                _parts.extend(_ty.cast(QualName, part).parts)
            elif isinstance(part, str):
                _parts.append(part)
            else:
                _parts.extend(_ty.cast(list, part))
        return [part for part in _parts if part]

    @classmethod
    def qualjoin(cls, *parts: _ty.Union[str, _ty.Iterable[str], QualName]) -> QualName:
        """Join ``parts`` (strings, iterables of strings, or other qualnames)."""
        return cls._qualjoin(cls._qualparts(*parts))

    @classmethod
    def qualsplit(cls, name: _ty.Union[str, QualName]) -> _ty.Sequence[str]:
        """Split ``name`` into its parts (a qualname's ``.parts`` if it has one)."""
        if hasattr(name, "parts"):
            return _ty.cast(QualName, name).parts
        return cls._qualsplit(_ty.cast(str, name))

    def with_name(self, name: str) -> QualName:
        """This qualname's parent joined with a new last part, ``name``."""
        return self.qualjoin(self.parent, name)

    @classmethod
    def _qualsplit(cls, name: str) -> list[str]:
        raise NotImplementedError()

    @classmethod
    def _qualjoin(cls, parts: list[str]) -> QualName:
        raise NotImplementedError()

    def __truediv__(self, key: str | _ty.Iterable[str] | QualName) -> QualName:
        """``self / key`` -- alias for ``self.qualjoin(self, key)``."""
        return self.qualjoin(self, key)

    def relative_to(self, name: QualName) -> QualName:
        """This qualname's parts with ``name``'s (a required prefix) stripped.

        Raises :class:`ValueError` with a readable message if ``name`` is not a
        prefix of this qualname's parts (including if it is longer).
        """
        parts = [*self.parts]
        other = list(name.parts)

        if len(other) > len(parts):
            raise ValueError(f"{self} is not relative to {name}")

        for idx, part in enumerate(other):
            if parts[idx] != part:
                raise ValueError(f"{self} is not relative to {name}")

        # Slice by the length of the (validated) prefix, NOT ``idx + 1``: an empty
        # base leaves the loop unentered, and ``idx + 1`` then dropped the first
        # part instead of returning self unchanged.
        return self.qualjoin(*parts[len(other) :])

    def camelcase(
        self,
        start: int = 0,
        end: _ty.Optional[int] = None,
        *,
        separators: _ty.Optional[_ty.Union[str, _ty.Sequence[str]]] = None,
    ) -> str:
        """``CamelCase`` over ``parts[start:end]``, then re-split on ``separators``.

        An empty part (e.g. from a name with a leading, trailing or doubled
        separator) is skipped rather than indexed into -- matching
        :func:`duho.text.camelcase`'s own empty-part guard.
        """
        parts = self.parts
        camelcased = "".join(
            [
                part[0].upper() + part[1:]
                for part in parts[start : len(parts) if end is None else end]
                if part
            ]
        )
        return _text.camelcase(camelcased, separators=separators)

    def as_path(self, root: _ty.Union[str, _P] = "/") -> _P:
        """This qualname's parts joined onto ``root`` (a :class:`~pathlib.PurePath`)."""
        if not hasattr(root, "joinpath"):
            root = _ty.cast(_P, _pathlib.PurePosixPath(root))

        return _ty.cast(_P, root).joinpath(*self.parts)


class DotQualNamed(QualName, str):
    """A :class:`QualName` that is also a ``str``, split on :attr:`SEPARATOR`."""

    SEPARATOR: str = "."

    @_functools.cached_property
    def parts(self) -> _ty.Sequence[str]:  # type: ignore[override]
        return self._qualsplit(self)

    @classmethod
    def _qualsplit(cls, name: str) -> list[str]:
        # Drop empty segments (a leading, trailing or doubled separator), same
        # as `_qualparts` -- otherwise a name like "a." or "a..b" carries a ""
        # part downstream, and `QualName.camelcase`'s `part[0]` raises
        # IndexError on it.
        return [part for part in name.split(cls.SEPARATOR) if part]

    @classmethod
    def _qualjoin(cls, parts: list[str]) -> DotQualNamed:
        return cls(cls.SEPARATOR.join(parts))


class PythonName(DotQualNamed):
    """A dotted name whose parts are Python-safe (via :func:`duho.text.pysafe`)."""

    @classmethod
    def new(cls, *parts: _ty.Union[str, QualName], sanitize: bool = True) -> PythonName:
        """Build a new instance by joining ``parts``.

        Each dotted part is coerced through :func:`duho.text.pysafe` unless
        ``sanitize=False``. Returns ``cls(name)`` (not hard-coded to
        ``PythonName``), so a subclass calling ``.new()`` keeps its own type.
        """
        name = cls.qualjoin(*parts)
        if sanitize:
            name = _text.pysafe(name)

        return cls(name)
