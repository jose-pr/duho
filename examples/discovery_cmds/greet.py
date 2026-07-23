"""A module command: greet NAME, discovered by file, no class needed."""

import argparse

from discovery_app import DiscoveryAppArgs


def register(parser: argparse.ArgumentParser, args: DiscoveryAppArgs) -> None:
    parser.add_argument("name", help="Who to greet.")
    parser.add_argument("--shout", action="store_true", help="Upper-case the greeting.")


def main(args: DiscoveryAppArgs) -> int:
    text = f"Hello, {args.name}!"
    if args.shout:
        text = text.upper()
    args._logger_.debug("retries configured: %d", args.retries)
    print(args._tag_line_(text))
    return 0
