"""A module command with a full init/success/finally_ lifecycle."""


def init(args):
    return {"checks": 0}


def main(args):
    print("all systems nominal")
    return 0


def success(ctx, args):
    print(f"status check completed ({ctx['checks']} sub-checks)")


def finally_(ctx, args):
    pass
