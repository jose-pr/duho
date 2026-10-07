from __future__ import annotations

import logging as _logging
import typing as _ty
from pathlib import Path as _Path

from .. import discovery as _discovery

_LOGGER = _logging.getLogger(__package__)


#: The magic per-directory lifecycle filename. Already excluded from step
#: discovery by ``_iter_step_files``'s leading-``_`` skip, so it can never
#: accidentally become a step itself.
_LIFECYCLE_FILENAME = "__main__.py"


class _Lifecycle:
    """The optional ``__main__.py`` lifecycle hooks for one RunPath directory.

    Each of ``init``/``success``/``finally_`` is an optional callable read off
    the ``__main__.py`` module (limited by its ``__all__`` when it has one); a
    missing hook no-ops (mirrors ``ModuleCommand``'s existing default-hook precedent).
    """

    __slots__ = ("init", "success", "finally_")

    def __init__(
        self,
        init: _ty.Optional[_ty.Callable[..., object]],
        success: _ty.Optional[_ty.Callable[..., object]],
        finally_: _ty.Optional[_ty.Callable[..., object]],
    ) -> None:
        self.init = init
        self.success = success
        self.finally_ = finally_


def _load_lifecycle(
    directory: _Path,
    qualname: str,
    logger: _logging.Logger = _LOGGER,
) -> _ty.Optional[_Lifecycle]:
    """Load ``__main__.py`` from ``directory`` and read its optional hooks; ``None`` if absent.

    Runs before :func:`_load_steps`: module-level setup in ``__main__.py`` (such
    as extending ``sys.path``) must be in effect before step modules import,
    or those imports fail and are skipped as environmental errors.
    """
    path = directory / _LIFECYCLE_FILENAME
    if not path.is_file():
        return None
    module = _discovery.import_from_path(
        "duho._runpath." + qualname.replace(".", "_") + ".__main__", path
    )
    # Without `__all__` any module-level name is a hook, an imported one included;
    # with it, only a listed name is.
    exported = getattr(module, "__all__", None)

    def hook(name: str) -> _ty.Optional[_ty.Callable[..., object]]:
        if exported is not None and name not in exported:
            return None
        return getattr(module, name, None)

    return _Lifecycle(
        init=hook("init"), success=hook("success"), finally_=hook("finally_")
    )
