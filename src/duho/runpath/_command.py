from __future__ import annotations

import logging as _logging
import typing as _ty
from pathlib import Path as _Path

from ..args import Arg as _Arg, Cmd as _Cmd, Extend as _Extend
from ..fanout import _worst
from ..logging import log_exception as _log_exception

from ._adapter import _adapt_step, _step_wants_ctx
from ._lifecycle import _load_lifecycle
from ._load import _load_steps
from ._selection import _Selection, _validate_required, _validate_selection
from ._steps import _reject_coroutine, _strict_or_warn

_LOGGER = _logging.getLogger(__package__)


# --------------------------------------------------------------------------
# The RunPath command
# --------------------------------------------------------------------------


class RunPathCmd(_Cmd):
    """Run a directory of numbered ``NN-name.py`` steps in order.

    A ``Cmd`` subclass built by the RunPath provider for a step directory. Its
    ``__call__`` loads the optional ``__main__.py`` lifecycle FIRST (before any
    step import -- see the module docstring), loads the steps (applying
    filename-encoded ``!``/``:key`` per-step defaults, importing only enabled/
    selected ones), applies ``--rcopts`` selection, orders them (Kahn's
    algorithm honoring ``REQUIRED`` plus the soft ``BEFORE``/``AFTER``
    relations), and runs each one's entrypoint through ``self._logger_`` --
    arity-detected so a step written ``(cmd, ctx)`` receives the
    ``__main__.py``-produced context and a step written ``(cmd)`` is
    unaffected. Returns the aggregate exit code (max of every step's own,
    ``None``/success -> ``0``, a swallowed failure -> ``1``).

    Not instantiated directly by users; the provider (see :func:`register`)
    subclasses it per directory, binding the directory path and subcommand name.
    A direct subclass need only set the class attrs ``_runpath_dir_`` (the step
    directory :class:`~pathlib.Path`) and ``_parsername_`` (the subcommand name).
    """

    #: The RunPath directory whose ``NN-name.py`` files are the steps. Set by the
    #: provider-built subclass; ``None`` on the base (which is not runnable).
    _runpath_dir_: _ty.Optional[_Path] = None

    rcopts: _Arg[_ty.List[str], _Extend(",")]
    "Step selection, comma-separated fnmatch patterns; `!` disables, `strict` errors on miss (e.g. `!*,build`)."
    ("-O", "--rcopts")  # type: ignore

    def _runpath_logger_(self) -> _logging.Logger:
        """The instance's ``_logger_`` if it has one, else the ``duho.runpath`` logger.

        ``ModuleCommand._logger_for`` falls back to the plain ``"duho"`` logger
        instead, because that one is handed to user hook code.
        """
        logger = getattr(self, "_logger_", None)
        if isinstance(logger, _logging.Logger):
            return logger
        return _LOGGER

    def __call__(self) -> int:
        directory = getattr(type(self), "_runpath_dir_", None)
        if directory is None:
            raise NotImplementedError(
                "RunPathCmd has no _runpath_dir_; build it via duho.runpath's "
                "provider (register()) or subclass it with _runpath_dir_ set"
            )
        directory = _Path(directory)
        logger = self._runpath_logger_()
        selection = _Selection.parse(getattr(self, "rcopts", None) or [])

        # The lifecycle is imported before any step, so its module-level
        # setup is already in effect for every step import.
        lifecycle = _load_lifecycle(directory, self._parsername_, logger)

        steps, present_names, broken_names = _load_steps(
            directory, self._parsername_, selection, logger
        )
        enabled_names = {step.name for step in steps}

        _validate_selection(selection, present_names, logger)
        _validate_required(steps, present_names, enabled_names, selection, logger)

        ctx = None
        if lifecycle is not None and lifecycle.init is not None:
            try:
                ctx = lifecycle.init(self, logger)
                _reject_coroutine(ctx, "%s __main__.init()" % self._parsername_)
            except Exception as exc:
                # An `init` failure is fatal whatever --rcopts says: every
                # step depends on ctx.
                _log_exception(logger, "__main__.py init() failed: %s", exc)
                raise

        failed_names: set[str] = set(broken_names)
        codes: list[int] = [0]
        try:
            for step in steps:
                # A step whose REQUIRED dependency failed is skipped; whether
                # that is fatal follows the skipped step's own strict setting.
                unmet = [dep for dep in step.required if dep in failed_names]
                if unmet:
                    failed_names.add(step.name)
                    _strict_or_warn(
                        "duho.runpath: skipping step %r: REQUIRED step(s) failed: %s"
                        % (step.name, ", ".join(unmet)),
                        selection.step_strict(step.name, step.file_strict),
                        logger,
                    )
                    codes.append(1)
                    continue

                logger.info("running step %s", step.name)
                entrypoint = _adapt_step(step.entrypoint)
                wants_ctx = lifecycle is not None and _step_wants_ctx(entrypoint)
                try:
                    result = entrypoint(self, ctx) if wants_ctx else entrypoint(self)
                    _reject_coroutine(result, "step %s" % step.name)
                except Exception as exc:
                    # A non-strict failure is swallowed, so this log line is the
                    # only record; DUHO_TRACEBACK=1 makes it a full traceback.
                    _log_exception(logger, "step %s failed: %s", step.name, exc)
                    failed_names.add(step.name)
                    codes.append(1)
                    if selection.step_strict(step.name, step.file_strict):
                        raise
                    continue

                # Same convention as ModuleCommand: None is success, a non-zero
                # int is failure, anything else is not an exit code.
                code = result if isinstance(result, int) else 0
                codes.append(code)
                if code:
                    failed_names.add(step.name)
                    message = (
                        "duho.runpath: step %r returned a non-zero exit code: %r"
                        % (step.name, result)
                    )
                    if selection.step_strict(step.name, step.file_strict):
                        # Strict stops on a non-zero return without raising, so
                        # finally_ still runs and the step's own code survives
                        # in the aggregate.
                        logger.error(message)
                        break
                    logger.warning(message)

            if (
                lifecycle is not None
                and lifecycle.success is not None
                and not failed_names
            ):
                result = lifecycle.success(ctx, self, logger)
                _reject_coroutine(result, "%s __main__.success()" % self._parsername_)
        finally:
            if lifecycle is not None and lifecycle.finally_ is not None:
                try:
                    result = lifecycle.finally_(ctx, self, logger)
                    _reject_coroutine(
                        result, "%s __main__.finally_()" % self._parsername_
                    )
                except Exception as exc:
                    # A raising finally_ must not mask a propagating step
                    # failure or the aggregate exit code.
                    _log_exception(logger, "__main__.py finally_() failed: %s", exc)
        return _worst(codes)
