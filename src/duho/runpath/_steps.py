from __future__ import annotations

import inspect as _inspect
import logging as _logging
import re as _re
import typing as _ty

#: The token in ``--rcopts`` that toggles strict mode (``strict`` enables,
#: ``!strict`` disables). A bare ``strict`` is a run-wide marker, not a step name.
_STRICT_TOKEN = "strict"


def _strict_or_warn(
    message: str,
    strict: bool,
    logger: _logging.Logger,
    warn_suffix: str = "",
) -> None:
    """Raise ``ValueError(message)`` if ``strict``, else log it as a warning.

    ``warn_suffix`` is appended on the warning path only. A duplicate enabled
    step name does not use this: it always raises (see :func:`_load_steps`).
    """
    if strict:
        raise ValueError(message)
    logger.warning(message + warn_suffix)


def _reject_coroutine(result: object, where: str) -> None:
    """Raise ``TypeError`` for a coroutine ``result`` from RunPath user code.

    RunPath never awaits user code, so an ``async def`` step would otherwise
    produce a coroutine that is never run.
    """
    if _inspect.iscoroutine(result):
        result.close()
        raise TypeError(
            "duho.runpath: %s returned a coroutine; duho awaits only "
            "Cmd.__call__ -- RunPath steps and __main__.py hooks must be "
            "synchronous" % where
        )


# --------------------------------------------------------------------------
# Step model
# --------------------------------------------------------------------------


class _Step:
    """One resolved step: a name, an ordering priority, deps, and an entrypoint.

    ``priority`` is the module's ``PRIORITY`` if it converts to ``int``, else the
    ``NN`` prefix. ``required``/``before``/``after`` are the module's lists;
    ``opts`` is the filename-derived :class:`_Opts` with concrete booleans.
    """

    __slots__ = (
        "name",
        "priority",
        "required",
        "before",
        "after",
        "entrypoint",
        "opts",
    )

    def __init__(
        self,
        name: str,
        priority: int,
        required: _ty.Sequence[str],
        entrypoint: _ty.Callable[..., object],
        before: _ty.Sequence[str] = (),
        after: _ty.Sequence[str] = (),
        opts: _ty.Optional[_Opts] = None,
    ) -> None:
        self.name = name
        self.priority = priority
        self.required = list(required)
        self.before = list(before)
        self.after = list(after)
        self.entrypoint = entrypoint
        self.opts = opts if opts is not None else _Opts(strict=True, enabled=True)

    @property
    def file_strict(self) -> bool:
        return bool(self.opts.strict)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "_Step(name=%r, priority=%r, required=%r)" % (
            self.name,
            self.priority,
            self.required,
        )


#: Separators between a matcher and its option tokens. Both work on every
#: platform (not ``os.pathsep``), so a filename parses the same everywhere;
#: ``:`` is invalid in a Windows filename, so those use ``;``.
_TOKEN_SEPARATORS = ":;"


def _split_tokens(text: str) -> _ty.List[str]:
    """Split ``text`` on each ``:``/``;`` character (see :data:`_TOKEN_SEPARATORS`).

    A doubled separator (``"a::b"``) yields an empty-string token between
    them (``["a", "", "b"]``), not a collapsed run.
    """
    return _re.split("[" + _TOKEN_SEPARATORS + "]", text)


#: Token key that toggles a matcher's enabled state, equivalent to a leading
#: ``!``. Bare boolean only (``enable``/``!enable``), never ``enable=...``.
_ENABLED_TOKEN = "enable"


class _Opts:
    """A parsed matcher's option tokens: ``strict``/``enable`` plus extras.

    Shared by :meth:`_Selection.parse` and :func:`_parse_file_modifiers`. A token
    is ``key`` (``True``), ``!key`` (``False``) or ``key=value``. ``.strict`` and
    ``.enabled`` are ``None`` when no such token was present, which callers must
    tell apart from ``False``; other tokens land in ``extra``.
    """

    __slots__ = ("strict", "enabled", "extra")

    def __init__(
        self,
        strict: _ty.Optional[bool] = None,
        enabled: _ty.Optional[bool] = None,
        extra: _ty.Optional[_ty.Dict[str, _ty.Union[bool, str]]] = None,
    ) -> None:
        self.strict = strict
        self.enabled = enabled
        self.extra = dict(extra or {})

    @classmethod
    def parse(cls, tokens: _ty.Sequence[str]) -> _Opts:
        """Parse token strings into an :class:`_Opts`; callers fold the ``None`` defaults."""
        strict: _ty.Optional[bool] = None
        enabled: _ty.Optional[bool] = None
        extra: dict[str, _ty.Union[bool, str]] = {}
        for raw in tokens:
            token = raw.strip()
            if not token:
                continue
            if "=" in token:
                key, _, value = token.partition("=")
                key = key.strip()
                if key:
                    # `strict=...`/`enable=...` are not recognized (boolean-only);
                    # kept as ordinary extras.
                    extra[key] = value
                continue
            token_enabled = not token.startswith("!")
            key = token[1:] if token.startswith("!") else token
            if not key:
                continue
            if key == _STRICT_TOKEN:
                strict = token_enabled
            elif key == _ENABLED_TOKEN:
                enabled = token_enabled
            else:
                extra[key] = token_enabled
        return cls(strict=strict, enabled=enabled, extra=extra)


def _parse_file_modifiers(stem: str) -> _ty.Tuple[str, _Opts]:
    """Strip filename modifiers from ``stem``; return ``(clean_stem, _Opts)``.

    Runs before :func:`_parse_step_filename`, so ``!02-provision`` keeps its
    prefix. A leading ``!`` disables; the first ``:``/``;`` splits the name from
    its tokens. The returned opts are resolved: ``enabled`` and ``strict``
    default to ``True``.
    """
    text = stem.strip()
    bang_disabled = text.startswith("!")
    if bang_disabled:
        text = text[1:]
    name, *raw_tokens = _split_tokens(text)
    name = name.strip()
    opts = _Opts.parse(raw_tokens)
    # An explicit `enable` token wins over a leading `!`.
    enabled = (not bang_disabled) if opts.enabled is None else opts.enabled
    strict = True if opts.strict is None else opts.strict
    return name, _Opts(strict=strict, enabled=enabled, extra=opts.extra)


def _parse_step_filename(stem: str) -> _ty.Optional[_ty.Tuple[int, str]]:
    """Parse a ``<digits>-<name>`` file stem into ``(NN, name)``, or None.

    Uses ``isdecimal()``, not ``isdigit()``: a superscript digit passes the
    latter but makes ``int()`` raise.
    """
    if "-" not in stem:
        return None
    prefix, name = stem.split("-", 1)
    if not prefix.isdecimal() or not name:
        return None
    return int(prefix), name
