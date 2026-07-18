#!/usr/bin/env python3
"""An ``install(1)``-like file installer.

Copies (or decompresses, symlinks, ...) a single source path to a destination,
optionally setting mode/owner/group. This example is a stub -- it logs the
resolved install plan instead of touching the filesystem, since the point here
is exercising duho's heavier argparse surface (positionals, Union types,
mutually-exclusive groups via ``NS(conflicts=...)``, ``NS(nargs="?")``, a custom
``action=``, and ``enum.Enum`` choices), not re-implementing real install logic.

Note: duho resolves `enum.Enum` CLI values by member *name* (not `.value`),
so `FileType` members are named lowercase (`dir`/`file`/`link`) to match the
`--type dir` CLI spelling directly.
"""
import enum
import sys
from pathlib import Path
from typing import Optional, Union

import duho
from duho import Arg, Cmd, LoggingArgs, NS, UpdateAction


class FileType(enum.Enum):
    dir = "directory"
    file = "regular"
    link = "softlink"


class Install(LoggingArgs, Cmd):
    """Install SOURCE at DESTINATION."""

    _parsername_ = "install"

    mode: str = "-"
    "File mode (octal), or '-' to leave unchanged."
    ("--mode", "-m")

    group: str = "-"
    "Group name, or '-' to leave unchanged."
    ("--group", "-g")

    owner: str = "-"
    "Owner name, or '-' to leave unchanged."
    ("--owner", "-o")

    parents: bool = False
    "Create missing parent directories of the destination."
    ("--parents", "-p")

    no_target_directory: bool = False
    "Treat DESTINATION as a normal file, not a directory to install into."
    ("--no-target-directory", "-T")

    root: Optional[Path] = None
    "Optional staging root to prefix the (absolute) destination with."
    ("--root", "-r")

    type: Arg[Union[FileType, str], NS(conflicts="type")] = "-"
    "Install type ('dir'/'file'/'link'); '-' autodetects from the source."
    ("--type",)

    decompress: Arg[Union[str, bool], NS(nargs="?")] = False
    "Decompress the source; bare flag autodetects from its suffix."
    ("-x", "--decompress")

    options: Arg[
        dict,
        NS(action=UpdateAction, type=lambda x: [x.split("=", maxsplit=1)]),
    ] = {}
    "Extra key=value install options (repeatable)."
    ("-O",)

    source: Path
    "Path to install from ('-' for stdin)."
    ("source",)

    destination: Path
    "Path to install to."
    ("destination",)

    def __call__(self) -> int:
        self._logger_.info(
            "would install %s -> %s (type=%s, mode=%s, owner=%s, group=%s)",
            self.source,
            self.destination,
            self.type,
            self.mode,
            self.owner,
            self.group,
        )
        if self.root:
            self._logger_.info("under staging root %s", self.root)
        if self.decompress:
            self._logger_.info("would decompress using %s", self.decompress)
        if self.options:
            self._logger_.info("extra options: %s", self.options)
        return 0


class FileInstall(LoggingArgs, Cmd):
    """Umbrella CLI for the file installer."""

    _version_ = duho.__version__
    _subcommands_ = [Install]

    def __call__(self) -> int:
        self._logger_.info("pick a subcommand, e.g. `install`")
        return 0


if __name__ == "__main__":
    sys.exit(duho.main(FileInstall))
