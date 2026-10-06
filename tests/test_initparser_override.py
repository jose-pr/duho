"""An `_initparser_` override that accepts and forwards `**kwargs` builds."""

import duho
from duho import Cmd


class Extra(Cmd):
    x: int = 0

    @classmethod
    def _initparser_(cls, parser, is_subcommand=False, parent_dests=None, **kwargs):
        super()._initparser_(parser, is_subcommand, parent_dests, **kwargs)
        parser.add_argument("--extra", default="none")
        return parser

    def __call__(self):
        return 0


def test_override_forwarding_kwargs_builds_and_parses():
    parser = Extra._parser_()
    assert parser.parse_known_args(["--extra", "1"])[0].extra == "1"
    assert isinstance(duho.parse(Extra, []), Extra)
