"""A class command: discovery finds `Cmd` subclasses in a loose file too."""

from duho import Cmd


class Whoami(Cmd):
    """Print the name this CLI was invoked as."""

    _parsername_ = "whoami"

    def __call__(self) -> int:
        print("discovery-app")
        return 0
