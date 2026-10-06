"""Prefixed, app-wide environment accessor.

An :class:`Env` presents a single typed view over ``os.environ`` keys sharing a
common prefix (``MYAPP_DEBUG``, ``MYAPP_CMDS_PATH``, ...), plus an optional
companion ``<prefix>env`` module of defaults an app may ship.

Distinct from the per-field ``NS(env=...)`` default layer: that resolves
one argparse field; this is the app-level accessor a driver reads settings through
(command search paths, ``DEBUG``, import hooks)."""

from __future__ import annotations

import collections.abc as _abc
import importlib as _importlib
import logging as _logging
import os as _os
import re as _re
import typing as _ty
from pathlib import Path as _Path

from . import _compat as _compat

_T = _ty.TypeVar("_T")

_LOGGER = _logging.getLogger(__name__)

#: Alias for the builtin, used in annotations that live in the SAME class body
#: as a method named ``bool`` (see :meth:`Env.bool`). Under PEP 649 lazy
#: annotations (Python 3.14+), an UNQUOTED ``bool`` written directly in that
#: class body would resolve to the class-scope name -- the ``Env.bool`` method
#: itself -- rather than the builtin type, because the annotate function's
#: scope sees the class namespace being built. Referencing this module-level
#: alias instead sidesteps the shadow entirely, on every Python version.
_bool = bool

#: Same alias trick as ``_bool`` above, for ``list``: ``Env`` also declares a
#: method named ``list`` (:meth:`Env.list`), so a quoted ``"list[_T]"``
#: annotation elsewhere in the class body (e.g. :meth:`Env.paths`'s return
#: type) resolves, under static type-checking, to the sibling METHOD rather
#: than the builtin generic -- mypy reported ``Function "duho.env.Env.list"
#: is not valid as a type``. Referencing this alias instead of the bare name
#: sidesteps the shadow.
_List = list

#: A prefix is only ever auto-loaded as a companion-module name after this
#: matches its NORMALISED form (upper-cased, ``-`` -> ``_``, trailing ``_``
#: ensured -- see ``__init__``). Restricting autoload to ``[A-Za-z0-9_]``
#: keeps a dotted prefix like ``"my.app"`` (normalised to ``"MY.APP_"``) from
#: importing an unrelated top-level package (``my``) while looking for
#: ``my.app_env``.
_VALID_PREFIX_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_"
)

#: A bare drive segment (``"C:"``, no trailing separator/backslash) -- Windows
#: resolves this to "the current directory on drive C", an implicit, ambient
#: lookup that is never a legitimate :meth:`Env.paths` entry. Splitting an
#: absolute Windows path on its OWN drive-letter colon (e.g. a ``PATHSEP``
#: of ``"\\"`` splitting ``"C:\...\cmds"``) produces exactly this segment as
#: its first piece -- rejecting it outright closes that route to the CWD.
_BARE_DRIVE_RE = _re.compile(r"^[A-Za-z]:$")

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
    under your control. Autoload is skipped outright (no import attempted) for
    an EMPTY prefix -- ``Env("")`` would otherwise autoload a top-level module
    literally named ``env``, a very common name for a project's own settings
    module -- and for a prefix whose normalised form contains any character
    outside ``[A-Za-z0-9_]`` (e.g. ``Env("my.app")``, which would otherwise
    import the unrelated top-level package ``my`` while resolving
    ``my.app_env``). Only the companion module's OWN absence is swallowed: an
    ``ImportError``/``ModuleNotFoundError`` raised *by code inside* an existing
    companion module (a typo, or a stdlib module missing on the 3.9 floor such
    as ``tomllib``) propagates instead of silently dropping every shipped
    default.

    **``MutableMapping`` semantics.** ``del env[key]`` / ``env.pop(key)`` on a
    key served from ``os.environ`` or the companion defaults records an
    in-object tombstone -- it is NEVER written back to the real process
    environment -- so the key reads as absent for the rest of this ``Env``'s
    lifetime (until an explicit ``env[key] = value`` write clears the
    tombstone again). This keeps the stdlib ``MutableMapping`` mixins
    (``pop``/``popitem``/``clear``, all built on ``__delitem__``+``__iter__``)
    internally consistent: a key that is ``in`` the mapping can always be
    ``pop()``-ed, and ``clear()`` always empties it.
    """

    def __init__(self, prefix: str, autoload: _bool = True, **env: object) -> None:
        prefix = prefix.upper().replace("-", "_")
        if prefix and not prefix.endswith("_"):
            prefix += "_"
        self.prefix = prefix

        #: Explicit values: `**env` kwargs and any later `env[k] = v` runtime
        #: write. Outranks BOTH the real environment and the companion
        #: module -- a caller/runtime write is a deliberate override.
        self._env: dict[str, str] = {}
        #: Companion-module-seeded values. Genuinely lowest precedence: a
        #: shipped default must never shadow a real exported environment
        #: variable (that inversion was a bug -- see module CHANGELOG entry).
        #: Kept separate from `self._env` so `__getitem__` can consult
        #: `os.environ` BEFORE falling back to this layer.
        self._defaults: dict[str, str] = {}
        #: Tombstones: keys explicitly `del`eted that are still visible via
        #: `os.environ`/`self._defaults` (an override in `self._env` is
        #: removed outright instead -- see `__delitem__`). Makes the
        #: `MutableMapping` surface (`pop`/`clear`/`popitem`/`in`) consistent
        #: WITHOUT ever mutating the real process environment.
        self._deleted: set[str] = set()
        if autoload and prefix and _VALID_PREFIX_CHARS.issuperset(prefix):
            modname = f"{prefix.lower()}env"
            try:
                module = _importlib.import_module(modname)
            except ModuleNotFoundError as exc:
                # A missing companion module is normal, not an error: an app
                # may or may not ship a "<prefix>env.py" of defaults. Narrowed
                # to the companion's OWN absence (its name, or a missing
                # PARENT package of it for a dotted prefix) -- anything else
                # (a plain `ImportError`, or a `ModuleNotFoundError` for some
                # OTHER name raised by code inside an existing companion
                # module) propagates instead of silently discarding every
                # shipped default.
                missing = exc.name or ""
                if missing != modname and not modname.startswith(missing + "."):
                    raise
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
        if key in self._deleted:
            raise KeyError(key)
        envkey = f"{self.prefix}{key}"
        if envkey in _os.environ:
            return _os.environ[envkey]
        return self._defaults[key]

    def __setitem__(self, key: str, value: object) -> None:
        self._env[key] = str(value)
        # A fresh explicit write always un-deletes: setting a key back after
        # `del env[key]` must make it visible again.
        self._deleted.discard(key)

    def __delitem__(self, key: str) -> None:
        if key in self._env:
            del self._env[key]
            return
        # Not an explicit override: this key (if it exists at all) is served
        # from `os.environ`/`self._defaults`. NEVER mutate the real process
        # environment -- record a tombstone that `__getitem__`/`__iter__`
        # honour instead, so `pop()`/`clear()`/`popitem()` (the stdlib
        # `MutableMapping` mixins, built on `__delitem__`+`__iter__`) see the
        # key as gone without touching `os.environ`. Raise `KeyError`
        # only when the key is not visible from ANY layer, matching a normal
        # mapping's `del`.
        envkey = f"{self.prefix}{key}"
        if key in self._deleted or (
            envkey not in _os.environ and key not in self._defaults
        ):
            raise KeyError(key)
        self._deleted.add(key)

    def __iter__(self) -> _ty.Iterator[str]:
        seen: set[str] = set()
        for key in self._env:
            seen.add(key)
            yield key
        for key in _os.environ:
            if key.startswith(self.prefix):
                stripped = key[len(self.prefix) :]
                if stripped not in seen and stripped not in self._deleted:
                    seen.add(stripped)
                    yield stripped
        for key in self._defaults:
            if key not in seen and key not in self._deleted:
                yield key

    def __len__(self) -> int:
        # A generator has no ``__len__``; count the unique keys instead.
        return sum(1 for _ in self)

    # -- Typed accessors --------------------------------------------------

    def bool(self, key: str) -> _bool:
        """Return ``key`` interpreted as a boolean.

        Truthy values (case-insensitive, whitespace-stripped) are
        ``duho.text.BOOL_TRUE`` (``1``, ``true``, ``yes``, ``y``, ``t``,
        ``on``); anything else (including a missing key) is ``False``.

        The truthy set is the same shared ``duho.text.BOOL_TRUE`` the layered
        (env/config) converter uses -- but this accessor
        stays LENIENT where that one is strict: an unrecognized value here is
        ``False`` rather than a user error.
        """
        return self.get(key, "0").strip().lower() in _compat.BOOL_TRUE

    def list(
        self, key: str, sep: str = ":", ty: _ty.Callable[[str], _T] = str
    ) -> _List[_T]:
        """Return ``key`` split on ``sep`` with ``ty`` applied to each part.

        A missing or empty value yields ``[]`` -- an empty list, NOT ``[ty("")]``.
        The old single-empty-string contract turned a missing ``<PREFIX>_CMDS_PATH``
        into ``[Path("")] == [Path(".")]`` and glob-imported the whole CWD;
        ``[""]`` as "one empty path" has no legitimate use.
        """
        raw = self.get(key, "")
        if not raw:
            return []
        return [ty(part) for part in raw.split(sep)]

    def _resolve_pathsep(self) -> str:
        """Resolve the separator :meth:`paths` splits on: ``<PREFIX>PATHSEP``
        if set and valid, else ``os.pathsep``.

        **Never a bare/global lookup.** An EMPTY prefix (``Env("")``) has no
        scoped key to read at all -- ``self.get("PATHSEP")`` would otherwise
        read the bare, unscoped ``PATHSEP`` straight off ``os.environ`` (its
        own ``envkey`` is ``f"{self.prefix}{key}"``, which is just ``"PATHSEP"``
        when ``self.prefix`` is ``""``), letting ANY process-wide ``PATHSEP``
        (set for a wholly unrelated program) bypass every safety rule
        :meth:`paths` applies, for every unprefixed ``Env`` on the system --
        a security-relevant fix. So an empty prefix always uses
        ``os.pathsep``, full stop.

        **Validated, not trusted verbatim.** A set value must be EXACTLY one
        character and not ``/``, ``\\``, or ``.`` -- a multi-character
        separator splits nothing (the whole value survives as one "segment"),
        and ``/``/``\\``/``.`` each let an absolute path's own directory
        separator (or the path itself) smuggle the current working directory
        past :meth:`paths`'s other safety rules (e.g. a Windows path's
        drive-letter colon split on its own backslash). An invalid value is
        WARNED at :data:`logging.WARNING` (naming the bad value, never
        silently ignored) and ``os.pathsep`` is used instead, exactly like an
        unset one.
        """
        if not self.prefix:
            return _os.pathsep
        raw = self.get("PATHSEP", None)
        if not raw:
            return _os.pathsep
        if len(raw) != 1 or raw in ("/", "\\", "."):
            _LOGGER.warning(
                "%sPATHSEP=%r is not a valid path-list separator (it must "
                "be exactly one character, and not '/', '\\', or '.'); "
                "using the platform default %r instead",
                self.prefix,
                raw,
                _os.pathsep,
            )
            return _os.pathsep
        return raw

    def paths(
        self,
        key: str,
        ty: _ty.Callable[[str], _T] = str,
        *,
        strict: _bool = True,
        on_reject: _ty.Optional[_ty.Callable[[str, str], None]] = None,
    ) -> _List[_T]:
        """Return a path-list env var (e.g. ``CMDS_PATH``) split on the OS separator.

        Unlike :meth:`list` (whose ``sep`` defaults to ``":"`` for generic lists
        like ``HOSTS``), this splits on the **platform path-list separator** --
        ``os.pathsep`` (``";"`` on Windows, ``":"`` on POSIX) -- so an absolute
        Windows path's drive-letter colon (``C:\\...``) is never mis-split into a
        bogus ``C`` entry. This app's OWN ``<PREFIX>PATHSEP`` key (see
        :meth:`_resolve_pathsep`) overrides the separator when set, so a
        caller can still force a separator regardless of platform -- SCOPED
        to this app's prefix, never a bare/global ``PATHSEP`` read straight
        off ``os.environ``: that would let ANY process-wide ``PATHSEP``
        (set for a wholly unrelated program) bypass every safety rule below
        for every duho app on the system, splitting e.g. ``C:\\...\\cmds`` on
        its own drive-letter colon into a bare ``C:`` segment (a
        security-relevant fix). For an EMPTY prefix (``Env("")``) there is no
        scoped key to read at all -- ``PATHSEP`` is always ``os.pathsep`` --
        since ``self.get("PATHSEP")`` would otherwise read that same bare,
        unscoped key. ``<PREFIX>PATHSEP`` is also validated: it must be
        exactly one character and not ``/``, ``\\``, or ``.`` (a multi-char
        value, or one of those three, can itself smuggle the CWD in the same
        way an unvalidated separator did -- e.g. splitting an absolute path
        on its own directory separator, or "separating" on nothing at all) --
        an invalid value is a security-relevant misconfiguration, not a
        silent fallback: it is WARNED at :data:`logging.WARNING` and
        ``os.pathsep`` is used instead. Missing/empty yields ``[]``, exactly
        like :meth:`list`.

        An empty or whitespace-only SEGMENT -- from a leading, trailing, or
        doubled separator (the common ``X="$X:/extra"`` append idiom run while
        ``X`` was unset) -- is dropped BEFORE ``ty`` ever sees it, and is never
        treated as the current directory. This is deliberately unlike POSIX
        ``$PATH``, where an empty entry means the CWD: here, an empty CMDS_PATH
        segment would become ``ty("")`` -- ``Path("")`` is ``Path(".")`` --
        which glob-imported and executed every file in the CWD (a security-relevant fix).
        A caller who genuinely wants the current directory writes it
        explicitly as a ``"."`` segment, which IS still honoured.

        Three more segment shapes are rejected, all ways an attacker-controlled
        separator can still smuggle the CWD in even past the empty-segment
        rule above:

        * a **bare drive letter** (``"C:"``, matching ``^[A-Za-z]:$``) --
          Windows resolves this to "the current directory on drive C", an
          ambient lookup that is never a legitimate entry on its own (the
          shape a backslash/colon ``PATHSEP`` produces by splitting an
          absolute Windows path on its own drive-letter colon).
        * while ``<PREFIX>PATHSEP`` OVERRIDES the platform default (see
          above), any segment that is neither **absolute** nor **explicitly
          relative** (spelled ``"."``, or starting with ``"./"``/``".\\"``)
          -- an ordinary relative-looking segment (``"C"``, ``"my-tools"``)
          is exactly the shape splitting an absolute path on a character
          that also occurs INSIDE it produces (e.g. ``PATHSEP=":"`` splits
          ``"C:\\...\\cmds"`` into ``"C"`` and ``"\\...\\cmds"`` -- ``"C"`` is
          neither a bare drive letter, matched above, nor does it resolve to
          the CWD, checked below: it resolves to a plain SUBDIRECTORY of the
          CWD, e.g. ``"./C"``, which is exactly the ambient lookup this rule
          closes). With the default ``os.pathsep`` this never applies, since
          a real relative entry a caller deliberately wrote (``"cmds"``,
          without ``./``) is not the product of any mis-split and stays
          valid.
        * any OTHER segment that **resolves to the current working
          directory** -- unless it is spelled exactly ``"."`` (the one
          explicitly honoured way to mean the CWD).

        ``strict`` (default ``True``) decides how a rejected segment is
        reported: **strict** raises ``ValueError`` immediately, naming the
        bad segment (the original, whole-value contract -- a direct caller
        that wants "tell me right away, and I'll decide" keeps getting
        exactly that). Passing ``strict=False`` instead **skips** the bad
        segment and keeps going, returning every OTHER valid entry -- for a
        caller like :func:`duho.runtime.app`'s ``CMDS_PATH`` resolution, one
        misconfigured entry among several must not silently drop the whole
        list (raising here would propagate past a bare ``except Exception`` at
        the call site, discarding every valid entry along with the bad one,
        unlogged). ``on_reject``, when
        given, is called as ``on_reject(segment, reason)`` for each skipped
        entry -- ``reason`` a short human phrase (``"bare drive segment"`` /
        ``"ambiguous relative segment under a custom separator"`` /
        ``"resolves to the current working directory"``) -- so the caller can
        report it however it likes (e.g. a ``WARNING`` naming which env var
        it came from); this method itself never logs. Ignored when
        ``strict`` is ``True`` (the raised ``ValueError`` already names it).

        Note this method does NOT delegate to :meth:`list` -- ``list``'s
        generic contract (arbitrary ``sep``/``ty``, e.g. ``env.list("PORTS",
        ty=int)``) is left unchanged, since dropping/rejecting segments here is
        a separate, unrelated decision for non-path lists.
        """
        sep = self._resolve_pathsep()
        # Only when this app's OWN <PREFIX>PATHSEP actually overrides the
        # platform default does an implicit relative segment become
        # suspect -- see the new rejection rule below. An unset/invalid
        # PATHSEP (sep falls back to os.pathsep) never triggers it: an
        # ordinary relative CMDS_PATH entry a caller wrote by hand is not
        # the product of any mis-split under the platform's own separator.
        overridden = sep != _os.pathsep
        raw = self.get(key, "")
        if not raw:
            return []
        result: _List[_T] = []
        cwd: _Path | None = None
        for part in raw.split(sep):
            part = part.strip()
            if not part:
                continue
            if part == ".":
                result.append(ty(part))
                continue
            if _BARE_DRIVE_RE.match(part):
                reason = (
                    "is a bare drive segment -- Windows resolves it to the "
                    "current directory on that drive; use an actual path, "
                    "or '.' for the CWD"
                )
                if not strict:
                    if on_reject is not None:
                        on_reject(part, "bare drive segment")
                    continue
                raise ValueError(f"{key!r} entry {part!r} {reason}")
            if overridden and not (
                part.startswith("./")
                or part.startswith(".\\")
                or _Path(part).is_absolute()
            ):
                reason = (
                    "is a relative entry not spelled explicitly relative "
                    "('.', './...' or '.\\\\...') while <PREFIX>PATHSEP "
                    "overrides the platform separator; this is the shape "
                    "splitting an absolute path on one of its own "
                    "characters produces, and it would otherwise resolve "
                    "against the current working directory"
                )
                if not strict:
                    if on_reject is not None:
                        on_reject(
                            part, "ambiguous relative segment under a custom separator"
                        )
                    continue
                raise ValueError(f"{key!r} entry {part!r} {reason}")
            try:
                resolved = _Path(part).expanduser().resolve()
            except OSError:  # pragma: no cover - an unresolvable path
                resolved = None
            if resolved is not None:
                if cwd is None:
                    cwd = _Path.cwd().resolve()
                if resolved == cwd:
                    if not strict:
                        if on_reject is not None:
                            on_reject(part, "resolves to the current working directory")
                        continue
                    raise ValueError(
                        f"{key!r} entry {part!r} resolves to the current "
                        f"working directory; spell it '.' if that is intended"
                    )
            result.append(ty(part))
        return result
