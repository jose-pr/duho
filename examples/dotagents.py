#!/usr/bin/env python3
"""Install (or update) an agent config into ``~/.agents``.

Duho port of the local ``dotagents`` installer: copies a payload tree of
agent-config files into a destination, backing up any file that would be
overwritten with different content. This example is a stub -- it logs the
actions it *would* take instead of touching the filesystem, since the point
here is exercising duho's CLI surface (subcommands, LoggingArgs, __call__),
not re-implementing the real backup/copy logic.
"""
import sys
from pathlib import Path

import duho
from duho import LoggingArgs


class Install(LoggingArgs):
    """Copy the agent-config payload into the destination directory."""

    _parsername_ = "install"

    dest: Path = Path.home() / ".agents"
    "Destination directory for the installed config."
    ("--dest",)

    dry_run: bool = False
    "Show what would be installed/backed up without writing anything."
    ("--dry-run",)

    with_examples: bool = False
    "Additionally copy the opt-in examples/ payload (never overwrites)."
    ("--with-examples",)

    def __call__(self) -> int:
        self._logger_.info("would install payload into %s", self.dest)
        if self.dry_run:
            self._logger_.info("dry-run: no files will be written")
        else:
            self._logger_.info(
                "would back up any changed files under %s/install_backup/<timestamp>/",
                self.dest,
            )
        if self.with_examples:
            self._logger_.info("would additionally copy examples/ into %s/examples", self.dest)
        return 0


class Dotagents(LoggingArgs):
    """Umbrella CLI for installing agent configs."""

    _version_ = duho.__version__
    _subcommands_ = [Install]

    def __call__(self) -> int:
        self._logger_.info("pick a subcommand, e.g. `install`")
        return 0


if __name__ == "__main__":
    sys.exit(duho.main(Dotagents))
