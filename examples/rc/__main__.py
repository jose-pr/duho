"""RunPath lifecycle: runs once before any step, hands `ctx` to steps that want it.

Self-contained on purpose: this directory must resolve the same way from ANY
entry point (a standalone script, not just ``examples/runpath_app.py``), so it
never imports from ``runpath_app`` -- see ``_tag`` below and the "Sharing more
than data" section of ``runpath_app.py``'s own module docstring.
"""

import logging

from duho.runpath import RunPathCmd


def _tag(cmd: RunPathCmd, message: str) -> str:
    """Format ``message`` tagged with a label, however the caller made it available.

    Uses ``cmd._tag_line_()`` when ``duho.runpath.register(base=RunpathAppArgs)``
    made it a real inherited method (running via
    ``python examples/runpath_app.py rc``); otherwise falls back to reading the
    ``label`` DATA field directly (always present when ``RunpathAppArgs`` is
    passed as ``app()``'s root, since that alone reaches every subcommand's
    parsed instance) -- and a fixed default when neither is available at all.
    """
    tag_line = getattr(cmd, "_tag_line_", None)
    if callable(tag_line):
        return tag_line(message)
    label = getattr(cmd, "label", "runpath-app")
    return f"[{label}] {message}"


def init(cmd: RunPathCmd, logger: logging.Logger) -> dict:
    dry_run = getattr(cmd, "dry_run", False)
    logger.info(_tag(cmd, "connecting once for this run (dry_run=%s)..." % dry_run))
    return {"connection": "fake-handle"}


def success(ctx: dict, cmd: RunPathCmd, logger: logging.Logger) -> None:
    logger.info(_tag(cmd, "all enabled steps completed cleanly"))


def finally_(ctx: dict, cmd: RunPathCmd, logger: logging.Logger) -> None:
    logger.info(_tag(cmd, "tearing down %s" % ctx["connection"]))
