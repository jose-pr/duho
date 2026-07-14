#!/usr/bin/env python3
"""An ``install(1)``-like file installer.

Duho port of buildutils' ``scripts/install``: copies (or decompresses,
symlinks, ...) a single source path to a destination, optionally setting
mode/owner/group and recording the install in a file database. This example
is a stub -- it logs the resolved install plan instead of touching the
filesystem, since the point here is exercising duho's heavier argparse
surface (positionals, Union types, mutually-exclusive groups via
``NS(conflicts=...)``, ``NS(nargs="?")``, a custom ``action=``, and
``enum.Enum`` choices), not re-implementing the real install logic.

Note: duho resolves `enum.Enum` CLI values by member *name* (not `.value`),
so `FileType` members are named lowercase (`dir`/`file`/`link`) to match the
`--type dir` CLI spelling directly, rather than the upstream script's
`DIRECTORY`/`REGULAR`/`SOFTLINK` names with lowercase string values.

Known duho gap (see .agents/plans/11_union_enum_argument_factory_gap.md):
``Arg[Union[FileType, str], ...]`` does not resolve enum members by name --
the union-branch factory falls back to plain ``str``. Per that field's
original design intent (`type: cli.Arg[Union[FileType, str], ...]`), the
`type` field below is stubbed to a plain `str` annotation instead of hacking
around the gap; `FileType` is still defined/used for illustration.
"""
import enum
import sys
from pathlib import Path
from typing import Optional, Union

import duho
from duho import Arg, LoggingArgs, NS, UpdateAction


class FileType(enum.Enum):
    dir = "directory"
    file = "regular"
    link = "softlink"


class Install(LoggingArgs):
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

    buildroot: Optional[Path] = None
    "Optional root to prefix the (absolute) destination with."
    ("--buildroot", "-r")

    # NOTE: upstream is `Arg[Union[FileType, str], NS(conflicts="type")]`;
    # stubbed to plain str -- see the module docstring's "Known duho gap".
    type: Arg[str, NS(conflicts="type")] = "-"
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

    def __run__(self) -> int:
        self._logger_.info(
            "would install %s -> %s (type=%s, mode=%s, owner=%s, group=%s)",
            self.source,
            self.destination,
            self.type,
            self.mode,
            self.owner,
            self.group,
        )
        if self.buildroot:
            self._logger_.info("under buildroot %s", self.buildroot)
        if self.decompress:
            self._logger_.info("would decompress using %s", self.decompress)
        if self.options:
            self._logger_.info("extra options: %s", self.options)
        return 0


class Buildutils(LoggingArgs):
    """Umbrella CLI for the buildutils file installer."""

    _version_ = duho.__version__
    _subcommands_ = [Install]

    def __run__(self) -> int:
        self._logger_.info("pick a subcommand, e.g. `install`")
        return 0


if __name__ == "__main__":
    sys.exit(duho.main(Buildutils))
