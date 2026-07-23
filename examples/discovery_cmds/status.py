"""A module command with a full init/success/finally_ lifecycle."""

from discovery_app import DiscoveryAppArgs


def init(args: DiscoveryAppArgs) -> dict:
    args._logger_.debug("initializing status check")
    return {"checks": 0}


def main(args: DiscoveryAppArgs) -> int:
    print(args._tag_line_("all systems nominal"))
    return 0


def success(ctx: dict, args: DiscoveryAppArgs) -> None:
    args._logger_.info("status check completed (%d sub-checks)", ctx["checks"])


def finally_(ctx: dict, args: DiscoveryAppArgs) -> None:
    args._logger_.debug("status check teardown")
