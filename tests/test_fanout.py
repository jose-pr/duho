"""Tests for duho.fanout: the opt-in target fan-out helper.

Covers the three surfaces of the module:

* ``run_targets`` -- the general concurrent primitive: exit-code aggregation
  (all-success, one-nonzero, all-different, custom reducer, empty), one target's
  exception not aborting the others, and ``max_workers=1`` (serialised) vs the
  default pool both producing correct results;
* per-target logging -- each record emitted while a target is "current" carries a
  ``[<target>]`` prefix, concurrent targets never cross-tag, and the prefixing
  filter is removed after the fan-out (a post-run log line is unprefixed);
* ``fan_out_command`` -- the thin sugar that dispatches one resolved command once
  per target via ``duho.run_command`` with a per-target ``make_instance``.

Leaked filters and cross-tagged records under concurrency are the module's two
top footguns, so both are asserted explicitly.
"""

import logging
import sys
import threading
import time

import pytest

import duho
import duho.fanout as fanout
from duho.fanout import current_target, run_targets, target_logging

# --------------------------------------------------------------------------
# run_targets: exit-code aggregation
# --------------------------------------------------------------------------


def test_all_success_aggregates_to_zero():
    """Every target returning 0 -> aggregate 0, and func is called once per target."""
    calls = []
    rc = run_targets(lambda t: calls.append(t) or 0, ["a", "b", "c"])
    assert rc == 0
    assert sorted(calls) == ["a", "b", "c"]


def test_none_result_maps_to_zero():
    """A target returning None counts as exit code 0."""
    assert run_targets(lambda t: None, ["a", "b"]) == 0


def test_one_nonzero_surfaces_that_code_under_max():
    """max policy: one target returning 2 makes the aggregate 2."""
    rc = run_targets(lambda t: 2 if t == "b" else 0, ["a", "b", "c"])
    assert rc == 2


def test_all_different_codes_aggregate_to_max():
    """Different per-target codes reduce to their max under the default policy."""
    codes = {"a": 1, "b": 5, "c": 3}
    rc = run_targets(lambda t: codes[t], ["a", "b", "c"])
    assert rc == 5


def test_empty_targets_returns_zero_without_calling_reducer():
    """Empty targets -> 0, and the aggregate reducer is never invoked."""
    sentinel = {"called": False}

    def reducer(codes):
        sentinel["called"] = True
        return 99

    assert run_targets(lambda t: 1, [], aggregate=reducer) == 0
    assert sentinel["called"] is False


def test_custom_aggregate_reducer():
    """A custom reducer overrides the default max policy."""
    # Sum policy instead of max.
    rc = run_targets(
        lambda t: {"a": 1, "b": 2, "c": 3}[t], ["a", "b", "c"], aggregate=sum
    )
    assert rc == 6


def test_any_aggregate_policy():
    """aggregate=any yields 1 when any target is nonzero, 0 when all succeed."""
    assert run_targets(lambda t: 0, ["a", "b"], aggregate=any) == 0
    assert run_targets(lambda t: 2 if t == "b" else 0, ["a", "b"], aggregate=any) == 1


def test_default_aggregate_surfaces_negative_code():
    """A negative code (a POSIX subprocess killed by a signal reports -signum)
    is ranked as a failure by the default reducer instead of being hidden
    behind a succeeding (0) target the way plain max would hide it."""
    assert run_targets(lambda t: {"a": 0, "b": -9, "c": 0}[t], ["a", "b", "c"]) == -9
    assert run_targets(lambda t: {"a": -9, "b": -15}[t], ["a", "b"]) == -15
    assert run_targets(lambda t: {"a": -9, "b": 1}[t], ["a", "b"]) == -9
    # Non-negative codes are unaffected: same result as plain max would give.
    assert run_targets(lambda t: {"a": 1, "b": 5, "c": 3}[t], ["a", "b", "c"]) == 5


# --------------------------------------------------------------------------
# run_targets: exception isolation
# --------------------------------------------------------------------------


def test_exception_in_one_target_does_not_abort_others(caplog):
    """An unhandled exception in one target is logged + treated as nonzero; others run."""
    ran = []

    def func(t):
        if t == "boom":
            raise ValueError("kaboom")
        ran.append(t)
        return 0

    with caplog.at_level(logging.ERROR, logger="duho"):
        rc = run_targets(func, ["ok1", "boom", "ok2"])

    # The two good targets still ran.
    assert sorted(ran) == ["ok1", "ok2"]
    # The failing target surfaced as a nonzero aggregate (default code 1).
    assert rc == 1
    # And the failure was logged (not silently swallowed).
    assert any(
        "boom" in rec.getMessage() or "failed" in rec.getMessage()
        for rec in caplog.records
    )


# --------------------------------------------------------------------------
# run_targets: SystemExit escaping a target
# --------------------------------------------------------------------------


def test_target_sys_exit_zero_does_not_mask_other_failures():
    """A target calling sys.exit(0) must not discard another target's nonzero
    code -- SystemExit is normalised like a return value, not re-raised."""
    ran = []

    def func(t):
        ran.append(t)
        if t == "a":
            sys.exit(0)
        return 1

    rc = run_targets(func, ["a", "b"])
    assert sorted(ran) == ["a", "b"]
    assert rc == 1


def test_target_sys_exit_nonzero_is_normalised_like_a_return():
    """SystemExit(n)'s int code is normalised and aggregated like a return
    value, and does not abort the other targets."""
    ran = []

    def func(t):
        ran.append(t)
        if t == "a":
            sys.exit(2)
        return 0

    rc = run_targets(func, ["a", "b"])
    assert sorted(ran) == ["a", "b"]
    assert rc == 2


def test_target_sys_exit_non_int_code_logged_as_failure(caplog):
    """A SystemExit carrying a non-int code (e.g. an argparse error message) is
    logged and treated as a plain failure, not re-raised."""

    def func(t):
        sys.exit("boom")

    with caplog.at_level(logging.ERROR, logger="duho.fanout"):
        rc = run_targets(func, ["a"])

    assert rc == 1
    assert any("boom" in rec.getMessage() for rec in caplog.records)


def test_keyboard_interrupt_in_a_target_is_not_swallowed():
    """KeyboardInterrupt is not a SystemExit and must not be normalised into an
    exit code -- it propagates out of run_targets."""

    def func(t):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        run_targets(func, ["a"])


# --------------------------------------------------------------------------
# run_targets: Ctrl-C cancels queued work
# --------------------------------------------------------------------------


def test_ctrl_c_cancels_queued_targets():
    """A KeyboardInterrupt from one target cancels every target still queued
    (already-dispatched ones may finish) instead of draining the whole target
    list to completion, and the interrupt still propagates."""
    started = []
    lock = threading.Lock()

    def func(t):
        if t == 0:
            raise KeyboardInterrupt()
        with lock:
            started.append(t)
        time.sleep(0.05)
        return 0

    targets = list(range(30))
    with pytest.raises(KeyboardInterrupt):
        run_targets(func, targets, max_workers=2)

    # Only the handful of targets already dispatched before the cancellation
    # took effect may have started; the rest of the queue must never run.
    assert len(started) < len(targets) - 5


# --------------------------------------------------------------------------
# run_targets: malformed log call inside a target (must not fail the target)
# --------------------------------------------------------------------------


def test_filter_does_not_raise_immediately_on_malformed_record():
    """The filter must never render the record itself: it defers to whatever
    formats the record later, instead of calling getMessage() eagerly and
    raising straight out of the log call (which used to escape into the
    target and mark it failed).

    (A true end-to-end reproduction -- a mismatched-args log call inside a
    target -- can't be asserted under pytest's own log capture: pytest's
    LogCaptureHandler deliberately overrides handleError to re-raise a bad
    log call so it fails the *test*, which defeats the very handleError
    protection this fix relies on. Testing the filter directly avoids that.)
    """
    record = logging.LogRecord(
        "x", logging.WARNING, __file__, 1, "no placeholder here", ("a",), None
    )
    token = current_target.set("t")
    try:
        assert fanout.TargetPrefixFilter().filter(record) is True
    finally:
        current_target.reset(token)

    # The deferred renderer still surfaces the real error once something
    # actually calls it (a handler's formatter would be protected by its own
    # handleError) -- the error is deferred, not silently discarded.
    with pytest.raises(TypeError):
        record.getMessage()


# --------------------------------------------------------------------------
# run_targets: traceback gating (DUHO_TRACEBACK)
# --------------------------------------------------------------------------


def test_target_exception_traceback_gated_by_duho_traceback(monkeypatch, caplog):
    """A raising target's failure log honors DUHO_TRACEBACK the same way every
    other resilient path does, instead of always attaching a full traceback."""
    monkeypatch.delenv("DUHO_TRACEBACK", raising=False)

    def func(t):
        raise RuntimeError("boom")

    with caplog.at_level(logging.ERROR, logger="duho.fanout"):
        rc = run_targets(func, ["a"])
    assert rc == 1
    assert len(caplog.records) == 1
    assert caplog.records[0].exc_info is None

    caplog.clear()
    monkeypatch.setenv("DUHO_TRACEBACK", "1")
    with caplog.at_level(logging.ERROR, logger="duho.fanout"):
        rc = run_targets(func, ["a"])
    assert rc == 1
    assert caplog.records[0].exc_info is not None


def test_non_int_return_logged_without_traceback(caplog):
    """A target returning a non-int value is a plain error, never a traceback
    (it is a return-type problem, not a caught exception)."""
    with caplog.at_level(logging.ERROR, logger="duho.fanout"):
        rc = run_targets(lambda t: "notanint", ["a"])
    assert rc == 1
    assert caplog.records[0].exc_info is None


# --------------------------------------------------------------------------
# run_targets: input validation
# --------------------------------------------------------------------------


def test_str_or_bytes_target_raises_type_error():
    """A bare str/bytes target would silently fan out one call per
    character/byte; reject it instead of doing that."""
    with pytest.raises(TypeError):
        run_targets(lambda t: 0, "web1")
    with pytest.raises(TypeError):
        run_targets(lambda t: 0, b"web1")


def test_max_workers_validated_even_for_empty_targets():
    """A non-positive max_workers is rejected up front, not only once there is
    work to schedule."""
    with pytest.raises(ValueError):
        run_targets(lambda t: 0, [], max_workers=0)
    with pytest.raises(ValueError):
        run_targets(lambda t: 0, [], max_workers=-1)


def test_async_target_is_awaited():
    """An async def target's coroutine is driven to completion, consistent
    with how duho.run_command handles an async command."""
    ran = []

    async def func(t):
        ran.append(t)
        return 0

    rc = run_targets(func, ["a", "b"])
    assert rc == 0
    assert sorted(ran) == ["a", "b"]


# --------------------------------------------------------------------------
# Module internals: logger name and record attribute naming
# --------------------------------------------------------------------------


def test_default_logger_name_is_module_qualified():
    """The default logger fanout uses for its own messages is 'duho.fanout'."""
    assert fanout._LOGGER.name == "duho.fanout"


def test_tagged_record_attribute_follows_duho_naming_convention():
    """The filter's own marker attribute follows this codebase's _duho_*_
    sandwich naming convention."""
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "hi", (), None)
    token = current_target.set("t")
    try:
        fanout.TargetPrefixFilter().filter(record)
    finally:
        current_target.reset(token)
    assert getattr(record, "_duho_target_tagged_", False) is True


# --------------------------------------------------------------------------
# run_targets: worker count (serialised vs parallel)
# --------------------------------------------------------------------------


def test_serialised_and_parallel_both_correct():
    """max_workers=1 (serialised) and the default pool give the same aggregate."""
    codes = {"a": 0, "b": 4, "c": 1}
    serial = run_targets(lambda t: codes[t], ["a", "b", "c"], max_workers=1)
    parallel = run_targets(lambda t: codes[t], ["a", "b", "c"])
    assert serial == parallel == 4


def test_parallel_actually_overlaps():
    """With max_workers>1 the targets run concurrently (overlap observed)."""
    overlap = {"max": 0}
    active = {"n": 0}
    lock = threading.Lock()

    def func(t):
        with lock:
            active["n"] += 1
            overlap["max"] = max(overlap["max"], active["n"])
        time.sleep(0.02)
        with lock:
            active["n"] -= 1
        return 0

    run_targets(func, list(range(4)), max_workers=4)
    assert overlap["max"] >= 2  # at least two ran at once


# --------------------------------------------------------------------------
# Per-target logging
# --------------------------------------------------------------------------


class _CapturingHandler(logging.Handler):
    """A handler that records (tagged, message) pairs after filters run."""

    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@pytest.fixture
def capture_handler():
    """Install a capturing handler on the root logger; remove it afterwards."""
    handler = _CapturingHandler()
    root = logging.getLogger()
    prev_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(prev_level)


def test_each_target_line_carries_its_prefix(capture_handler):
    """Each target's log lines carry that target's [target] prefix."""
    log = logging.getLogger("duho.worker")

    def func(t):
        log.info("working on %s", t)
        return 0

    run_targets(func, ["alpha", "beta"])
    assert "[alpha] working on alpha" in capture_handler.messages
    assert "[beta] working on beta" in capture_handler.messages


def test_filter_removed_after_run_no_leak(capture_handler):
    """After run_targets returns, the prefixing filter is gone (line unprefixed)."""
    log = logging.getLogger("duho.worker")
    run_targets(lambda t: log.info("x") or 0, ["one"])

    # No TargetPrefixFilter left on the handler.
    assert all(
        not isinstance(f, fanout.TargetPrefixFilter) for f in capture_handler.filters
    )
    root = logging.getLogger()
    assert all(not isinstance(f, fanout.TargetPrefixFilter) for f in root.filters)
    # A post-run log line is unprefixed.
    log.info("after")
    assert capture_handler.messages[-1] == "after"


def test_concurrent_records_never_cross_tag(capture_handler):
    """Under concurrency every record's prefix matches the target that emitted it."""
    log = logging.getLogger("duho.worker")

    def func(t):
        for i in range(4):
            time.sleep(0.001)
            log.info("step-%d", i)
        return 0

    targets = ["t%02d" % i for i in range(10)]
    run_targets(func, targets, max_workers=6)

    # 10 targets x 4 messages, all prefixed, each matching a real target.
    assert len(capture_handler.messages) == 40
    for msg in capture_handler.messages:
        assert msg.startswith("["), msg
        prefix = msg[1 : msg.index("]")]
        assert prefix in targets, msg


def test_current_target_is_none_outside_a_run():
    """The current_target context var is None when no fan-out is active."""
    assert current_target.get() is None
    run_targets(lambda t: 0, ["a"])
    assert current_target.get() is None


def test_target_logging_context_manager_installs_and_removes():
    """target_logging installs a filter on a logger's handlers and removes it."""
    logger = logging.getLogger("duho.tl_test")
    handler = _CapturingHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        with target_logging(logger):
            assert any(
                isinstance(f, fanout.TargetPrefixFilter) for f in handler.filters
            )
        assert all(
            not isinstance(f, fanout.TargetPrefixFilter) for f in handler.filters
        )
    finally:
        logger.removeHandler(handler)


def test_target_logging_removes_filter_even_on_exception():
    """A body exception still leaves no leaked filter."""
    logger = logging.getLogger("duho.tl_exc")
    handler = _CapturingHandler()
    logger.addHandler(handler)
    try:
        with pytest.raises(RuntimeError):
            with target_logging(logger):
                raise RuntimeError("boom")
        assert all(
            not isinstance(f, fanout.TargetPrefixFilter) for f in handler.filters
        )
    finally:
        logger.removeHandler(handler)


# --------------------------------------------------------------------------
# fan_out_command
# --------------------------------------------------------------------------


def test_fan_out_command_dispatches_per_target():
    """fan_out_command builds a per-target instance and dispatches the command."""

    class _Target(duho.Cmd):
        host: str = ""
        ("--host",)

        def __call__(self):
            _seen.append(self.host)
            return 0

    _seen = []

    def make_instance(target):
        inst = _Target()
        inst.host = target
        return inst

    rc = fanout.fan_out_command(_Target, make_instance, ["h1", "h2", "h3"])
    assert rc == 0
    assert sorted(_seen) == ["h1", "h2", "h3"]


def test_fan_out_command_aggregates_exit_codes():
    """A per-target instance returning a nonzero code surfaces via aggregation."""

    class _Target(duho.Cmd):
        code: int = 0
        ("--code",)

        def __call__(self):
            return self.code

    def make_instance(target):
        inst = _Target()
        inst.code = target
        return inst

    rc = fanout.fan_out_command(_Target, make_instance, [0, 3, 1])
    assert rc == 3


def test_fan_out_command_over_expanded_targets():
    """End-to-end: fan a command over duho.expand()'d targets, aggregate + prefix."""

    class _Ping(duho.Cmd):
        host: str = ""
        ("--host",)

        def __call__(self):
            return 0

    ran = []

    def make_instance(target):
        inst = _Ping()
        inst.host = target
        ran.append(target)
        return inst

    targets = list(duho.expand("t[1-3]"))
    rc = fanout.fan_out_command(_Ping, make_instance, targets)
    assert rc == 0
    assert sorted(ran) == ["t1", "t2", "t3"]
