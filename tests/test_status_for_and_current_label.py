"""``duho.status_for`` is ``main``'s status lookup; ``duho.fanout.current_label``
is the label text of the target a worker is running."""

import duho
from duho import Cli, CommandError, status_for
from duho.fanout import current_label, run_targets


class Root(Cli):
    _parsername_ = "root"
    _errors_ = {KeyError: 66, LookupError: 67, OSError: 74}


class NoErrors(Cli):
    _parsername_ = "noerrors"


def test_a_command_error_carries_its_own_status():
    assert status_for(Root, CommandError("x", 9)) == 9
    assert status_for(NoErrors, CommandError("x", 9)) == 9
    assert status_for(None, duho.UsageError("x")) == 2


def test_a_mapped_exception_takes_the_first_match_in_order():
    assert status_for(Root, KeyError("k")) == 66
    assert status_for(Root, IndexError("i")) == 67
    assert status_for(Root, FileNotFoundError("f")) == 74


def test_an_unmapped_exception_and_a_root_without_errors_give_none():
    assert status_for(Root, RuntimeError("r")) is None
    assert status_for(NoErrors, KeyError("k")) is None
    assert status_for(None, KeyError("k")) is None


def test_it_agrees_with_what_main_returns():
    class Boom(duho.Cmd):
        _parsername_ = "boom"

        def __call__(self):
            raise KeyError("k")

    class App(Root):
        _subcommands_ = [Boom]

    assert duho.main(App, ["boom"]) == status_for(App, KeyError("k")) == 66


def test_the_label_is_the_text_inside_a_target_and_none_outside():
    seen = {}

    def work(target):
        seen[target] = current_label.get()

    assert current_label.get() is None
    run_targets(work, ["a", "b"], label=lambda t: "host-" + t)
    assert seen == {"a": "host-a", "b": "host-b"}
    assert current_label.get() is None


def test_the_label_is_none_without_label():
    seen = []
    run_targets(lambda t: seen.append(current_label.get()), ["a"])
    assert seen == [None]
