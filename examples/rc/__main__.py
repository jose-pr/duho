"""RunPath lifecycle: runs once before any step, hands `ctx` to steps that want it."""

import logging

from duho.runpath import RunPathCmd
from runpath_app import format_tag_line


def init(cmd: RunPathCmd, logger: logging.Logger) -> dict:
    dry_run = getattr(cmd, "dry_run", False)
    logger.info(format_tag_line(cmd, "connecting once for this run (dry_run=%s)..." % dry_run))
    return {"connection": "fake-handle"}


def success(ctx: dict, cmd: RunPathCmd, logger: logging.Logger) -> None:
    logger.info(format_tag_line(cmd, "all enabled steps completed cleanly"))


def finally_(ctx: dict, cmd: RunPathCmd, logger: logging.Logger) -> None:
    logger.info(format_tag_line(cmd, "tearing down %s" % ctx["connection"]))
