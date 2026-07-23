"""A class command: discovery finds `Cmd` subclasses in a loose file too."""

from duho import Cmd

from discovery_app import DiscoveryAppArgs


class Whoami(DiscoveryAppArgs, Cmd):
    """Print the name this CLI was invoked as.

    Inherits ``DiscoveryAppArgs`` DIRECTLY (unlike a module command, whose
    ``args`` parameter is already the parsed root instance): a class command
    is authored by the app, so it can just declare the base itself and get
    ``_logger_``/``_tag_line_``/the shared fields as real inherited members --
    no provider-level "which base do I build with" problem the way
    ``duho.runpath``'s dynamically-built ``RunPathCmd`` subclasses have (see
    ``duho.runpath.register(base=...)``).
    """

    _parsername_ = "whoami"

    def __call__(self) -> int:
        self._logger_.debug("label=%s tags=%s", self.label, self.tags)
        print(self._tag_line_("discovery-app"))
        return 0
