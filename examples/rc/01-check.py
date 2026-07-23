"""A 2-arg step: receives the `ctx` `__main__.py`'s `init()` produced."""

BEFORE = ["provision"]


def main(cmd, ctx):
    print(f"checking prerequisites over {ctx['connection']}")
