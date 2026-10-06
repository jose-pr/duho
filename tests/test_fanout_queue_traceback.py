"""A fan-out target's logged exception keeps its traceback through a QueueHandler."""

import io
import logging
import logging.handlers
import queue

from duho.fanout import run_targets


def _run(fanout: bool, name: str) -> str:
    q: "queue.Queue" = queue.Queue()
    out = io.StringIO()
    sink = logging.StreamHandler(out)
    sink.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    listener = logging.handlers.QueueListener(q, sink)
    log = logging.getLogger(name)
    handler = logging.handlers.QueueHandler(q)
    log.propagate = False
    log.addHandler(handler)
    log.setLevel(logging.INFO)

    def work(target):
        try:
            1 / 0
        except ZeroDivisionError:
            log.exception("work failed on %s", target)
        return 0

    listener.start()
    try:
        if fanout:
            run_targets(work, ["host1"], logger=log)
        else:
            work("host1")
    finally:
        listener.stop()
        log.removeHandler(handler)
    return out.getvalue()


def test_traceback_survives_queue_handler_inside_fanout():
    text = _run(True, "duho.tests.fanout_queue_tb.fan")
    assert "ZeroDivisionError" in text
    assert text.startswith("ERROR [host1] work failed on host1")
    assert text.count("[host1]") == 1


def test_traceback_present_outside_fanout():
    assert "ZeroDivisionError" in _run(False, "duho.tests.fanout_queue_tb.plain")
