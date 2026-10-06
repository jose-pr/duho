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

    Named ``_Lifecycle``/``_load_lifecycle`` (an earlier design used an
    ``_init.py`` file, renamed to ``__main__.py`` during execution, but the
    code kept saying ``_Init``/``_load_init``/a ``"._init"`` module key -- a
    traceback from this file used to show module ``..._init``, a file that
    does not exist).

    Each of ``init``/``success``/``finally_`` is an optional callable read off
    the ``__main__.py`` module (``getattr(module, name, None)``); a missing hook
    no-ops (mirrors ``ModuleCommand``'s existing default-hook precedent).
    """

    __slots__ = ("init", "success", "finally_")

    def __init__(
        self,
        init: "_ty.Optional[_ty.Callable[..., object]]",
        success: "_ty.Optional[_ty.Callable[..., object]]",
        finally_: "_ty.Optional[_ty.Callable[..., object]]",
    ) -> None:
        self.init = init
        self.success = success
        self.finally_ = finally_


def _load_lifecycle(
    directory: "_Path",
    qualname: str,
    logger: "_logging.Logger" = _LOGGER,
) -> "_ty.Optional[_Lifecycle]":
    """Load ``__main__.py`` from ``directory``, if present; else ``None``.

    ``None`` means "no lifecycle" -- callers must treat this as byte-identical
    to before this lifecycle existed (no ``ctx``, steps called with ``self``
    only). When present, imports it the same way steps are imported (the
    public ``discovery.import_from_path``, ending in ``.__main__`` rather than
    the stale ``._init``) and reads the three optional hooks off it.

    Called BEFORE :func:`_load_steps`: a ``__main__.py`` doing
    module-level setup (e.g. adding a sibling ``lib/`` to ``sys.path`` for
    shared step helpers) must already be in effect by the time step modules
    are imported, or those imports fail and are silently skipped as
    environmental errors.
    """
    path = directory / _LIFECYCLE_FILENAME
    if not path.is_file():
        return None
    module = _discovery.import_from_path(
        "duho._runpath." + qualname.replace(".", "_") + ".__main__", path
    )
    return _Lifecycle(
        init=getattr(module, "init", None),
        success=getattr(module, "success", None),
        finally_=getattr(module, "finally_", None),
    )
