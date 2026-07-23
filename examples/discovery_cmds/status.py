"""A module command with a full init/success/finally_ lifecycle."""

import argparse


def init(args: argparse.Namespace) -> dict:
    return {"checks": 0}


def main(args: argparse.Namespace) -> int:
    print("all systems nominal")
    return 0


def success(ctx: dict, args: argparse.Namespace) -> None:
    print(f"status check completed ({ctx['checks']} sub-checks)")


def finally_(ctx: dict, args: argparse.Namespace) -> None:
    pass
