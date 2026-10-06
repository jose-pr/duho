"""A subcommand description is offered as the author wrote it."""

import argparse

from duho import completion


def test_fish_description_is_unescaped_from_the_argparse_template():
    parser = argparse.ArgumentParser(prog="fx")
    subs = parser.add_subparsers(dest="cmd")
    # argparse help templates write a literal percent sign as `%%`.
    subs.add_parser("sub", help="Sub does 50%% and %%percent.")
    script = completion.fish(parser)
    assert "-d 'Sub does 50% and %percent.'" in script
    assert "%%" not in script
