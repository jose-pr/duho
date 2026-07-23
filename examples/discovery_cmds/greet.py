"""A module command: greet NAME, discovered by file, no class needed."""

import argparse


def register(parser: argparse.ArgumentParser, args: object) -> None:
    parser.add_argument("name", help="Who to greet.")
    parser.add_argument("--shout", action="store_true", help="Upper-case the greeting.")


def main(args: argparse.Namespace) -> int:
    text = f"Hello, {args.name}!"
    print(text.upper() if args.shout else text)
    return 0
