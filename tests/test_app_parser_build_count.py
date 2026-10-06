"""duho.app builds a root's static subcommand tree once, not twice."""

import argparse

import duho
from duho import Cli, Cmd


class S1(Cmd):
    def __call__(self):
        return 0


class S2(Cmd):
    def __call__(self):
        return 0


class S3(Cmd):
    def __call__(self):
        return 0


class S4(Cmd):
    def __call__(self):
        return 0


class CountRoot(Cli):
    verbose: bool = False
    ("-v", "--verbose")

    _subcommands_ = [S1, S2, S3, S4]


def _count_parsers(monkeypatch, run):
    built = []
    real_init = argparse.ArgumentParser.__init__

    def counting_init(self, *args, **kwargs):
        built.append(self)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(argparse.ArgumentParser, "__init__", counting_init)
    run()
    return len(built)


def test_app_builds_each_static_subcommand_parser_once(monkeypatch):
    via_main = _count_parsers(
        monkeypatch, lambda: duho.main(CountRoot, ["s1"], setup_logging=False)
    )
    via_app = _count_parsers(
        monkeypatch,
        lambda: duho.app(CountRoot, argv=["s1"], setup_logging=False),
    )
    # The root, its four subcommands, plus the root-options donor parser.
    assert via_app <= via_main + 1
