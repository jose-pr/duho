"""RunPath lifecycle: runs once before any step, hands `ctx` to steps that want it."""

import logging

from duho.runpath import RunPathCmd


def init(cmd: RunPathCmd, logger: logging.Logger) -> dict:
    logger.info("connecting once for this run...")
    return {"connection": "fake-handle"}


def success(ctx: dict, cmd: RunPathCmd, logger: logging.Logger) -> None:
    logger.info("all enabled steps completed cleanly")


def finally_(ctx: dict, cmd: RunPathCmd, logger: logging.Logger) -> None:
    logger.info("tearing down %s", ctx["connection"])
