"""``run_targets``/``fan_out_command`` take ``label`` for the log prefix text."""

import logging
import typing

import pytest

import duho
import duho.fanout as fanout
from duho.fanout import current_target, run_targets


class _Host:
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return "<Host %s>" % self.name


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@pytest.fixture
def captured():
    handler = _Capture()
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)


def test_label_text_is_the_prefix(captured):
    log = logging.getLogger("duho.labeltest")

    def func(target):
        log.info("hello")
        return 0

    rc = run_targets(func, [_Host("a"), _Host("b")], label=lambda t: t.name.upper())
    assert rc == 0
    assert sorted(captured.messages) == ["[A] hello", "[B] hello"]


def test_default_label_is_str_of_the_target(captured):
    log = logging.getLogger("duho.labeltest")
    run_targets(lambda t: log.info("hi") or 0, [_Host("a")])
    assert captured.messages == ["[<Host a>] hi"]


def test_func_still_receives_the_target_and_current_target_is_it():
    seen = []

    def func(target):
        seen.append((target, current_target.get()))
        return 0

    host = _Host("a")
    run_targets(func, [host], label=lambda t: "x")
    assert seen == [(host, host)]


def test_label_that_raises_fails_only_that_target(captured):
    def label(target):
        if target.name == "bad":
            raise RuntimeError("no label")
        return target.name

    ran = []

    def func(target):
        ran.append(target.name)
        return 0

    rc = run_targets(func, [_Host("bad"), _Host("ok")], label=label)
    assert rc == 1
    assert ran == ["ok"]


def test_fan_out_command_label(captured):
    log = logging.getLogger("duho.labeltest")

    class _Ping(duho.Cmd):
        def __call__(self):
            log.info("pong")
            return 0

    rc = fanout.fan_out_command(
        _Ping, lambda target: _Ping(), [_Host("a")], label=lambda t: "L-" + t.name
    )
    assert rc == 0
    assert captured.messages == ["[L-a] pong"]


def test_annotations_resolve():
    # Resolves the one annotation; get_type_hints would resolve every parameter.
    for func in (run_targets, fanout.fan_out_command):
        resolved = eval(func.__annotations__["label"], vars(fanout), {"_ty": typing})
        assert resolved == typing.Optional[typing.Callable[[object], str]]
