"""A level's method exists on every Logger, the current logger class and LoggerAdapter."""

import logging

import pytest

from duho.logging import add_logging_level


class _Records(logging.Handler):
    def __init__(self):
        super().__init__(logging.NOTSET)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def sink():
    handler = _Records()
    logger = logging.getLogger("duho.test.everywhere")
    logger.addHandler(handler)
    logger.setLevel(1)
    logger.propagate = False
    try:
        yield logger, handler
    finally:
        logger.removeHandler(handler)


class _Prefixed(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return "[%s] %s" % (self.extra["tag"], msg), kwargs


class _Extra(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        kwargs["extra"] = {"tag": self.extra["tag"]}
        return msg, kwargs


@pytest.mark.parametrize("adapter_cls", [logging.LoggerAdapter, _Prefixed])
def test_adapter_gets_the_method_and_applies_process(sink, adapter_cls):
    logger, handler = sink
    name = "adapt" + adapter_cls.__name__.lower().strip("_")
    add_logging_level(name.upper(), 7)
    adapter = adapter_cls(logger, {"tag": "t1"})
    getattr(adapter, name)("hello %s", "x")
    (record,) = handler.records
    assert record.levelno == 7
    if adapter_cls is _Prefixed:
        assert record.getMessage() == "[t1] hello x"
    else:
        assert record.getMessage() == "hello x"


def test_adapter_process_extra_reaches_the_record(sink):
    logger, handler = sink
    add_logging_level("ADAPTEXTRA", 7)
    _Extra(logger, {"tag": "t2"}).adaptextra("m")
    assert handler.records[0].tag == "t2"


def test_adapter_honours_the_level(sink):
    logger, handler = sink
    add_logging_level("ADAPTGATE", 7)
    logger.setLevel(10)
    logging.LoggerAdapter(logger, {}).adaptgate("dropped")
    assert handler.records == []


def test_adapter_record_names_the_caller(sink):
    logger, handler = sink
    add_logging_level("ADAPTWHERE", 7)
    adapter = _Prefixed(logger, {"tag": "t"})

    def caller_of_adapter():
        adapter.adaptwhere("a")  # line marker
        return None

    caller_of_adapter()
    record = handler.records[0]
    assert record.funcName == "caller_of_adapter"
    assert record.filename == __file__.replace("\\", "/").rsplit("/", 1)[-1]
    with open(__file__, encoding="utf-8") as fh:
        line = fh.read().splitlines()[record.lineno - 1]
    assert "line marker" in line


def test_logger_record_names_the_caller(sink):
    logger, handler = sink
    add_logging_level("LOGGERWHERE", 7)

    def caller_of_logger():
        logger.loggerwhere("a")

    caller_of_logger()
    assert handler.records[0].funcName == "caller_of_logger"


def test_custom_logger_class_set_before_the_level(sink):
    class Custom(logging.Logger):
        pass

    previous = logging.getLoggerClass()
    logging.setLoggerClass(Custom)
    try:
        add_logging_level("CUSTOMLVL", 7)
        custom = Custom("duho.test.custom")
        plain = logging.Logger("duho.test.plain")
        assert callable(plain.customlvl)
        assert callable(custom.customlvl)
        assert callable(logging.getLogger().customlvl)
    finally:
        logging.setLoggerClass(previous)
    assert callable(logging.Logger.customlvl)
    assert callable(Custom.customlvl)
    assert callable(logging.root.customlvl)


def test_marker_on_all_three_targets():
    add_logging_level("MARKEDLVL", 7)
    assert logging.Logger.markedlvl._duho_level_ == 7
    assert logging.LoggerAdapter.markedlvl._duho_level_ == 7
    assert logging.markedlvl._duho_level_ == 7


@pytest.mark.parametrize("owner", ["Logger", "LoggerAdapter"])
def test_foreign_method_on_each_target_raises_without_force(owner):
    target = getattr(logging, owner)
    name = "foreign" + owner.lower()
    setattr(target, name, lambda self, *a, **k: None)
    with pytest.raises(ValueError, match=name):
        add_logging_level(name.upper(), 7)
    add_logging_level(name.upper(), 7, force=True)
    assert getattr(target, name)._duho_level_ == 7


def test_trace_exists_on_adapter_and_logger():
    assert callable(logging.LoggerAdapter.trace)
    assert callable(logging.Logger.trace)
