"""A 2-arg step: receives the `ctx` `__main__.py`'s `init()` produced."""

from duho.runpath import RunPathCmd

BEFORE = ["provision"]


def main(cmd: RunPathCmd, ctx: dict) -> None:
    print(f"checking prerequisites over {ctx['connection']}")
