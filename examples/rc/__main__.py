"""RunPath lifecycle: runs once before any step, hands `ctx` to steps that want it."""


def init(cmd, logger):
    logger.info("connecting once for this run...")
    return {"connection": "fake-handle"}


def success(ctx, cmd, logger):
    logger.info("all enabled steps completed cleanly")


def finally_(ctx, cmd, logger):
    logger.info("tearing down %s", ctx["connection"])
