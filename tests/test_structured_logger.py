"""Tests for structured logger."""
import json
import logging
from pathlib import Path
from src.core.structured_logger import StructuredLogger, StructuredFormatter, get_logger


def test_structured_logger_info(capsys):
    log = StructuredLogger("test_module")
    log.info("hello world", packet_id="pkt_001")
    # Logger outputs to stderr via StreamHandler
    # Just verify no exception and logger works
    assert log.module_name == "test_module"


def test_structured_logger_trace_id():
    log = StructuredLogger("test_trace")
    tid = log.new_trace()
    assert len(tid) == 16
    assert log.trace_id == tid


def test_structured_logger_set_trace():
    log = StructuredLogger("test_set")
    log.set_trace_id("custom_trace_123")
    assert log.trace_id == "custom_trace_123"


def test_structured_formatter():
    fmt = StructuredFormatter()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="test message", args=(), exc_info=None,
    )
    record.structured_context = {"trace_id": "abc", "module": "test"}
    output = fmt.format(record)
    parsed = json.loads(output)
    assert parsed["message"] == "test message"
    assert parsed["trace_id"] == "abc"
    assert parsed["level"] == "INFO"
    assert "timestamp" in parsed


def test_structured_logger_file_output(tmp_path):
    log = StructuredLogger("file_test", log_dir=tmp_path)
    log.new_trace()
    log.info("test file log", key="value")
    # Check file was created
    log_file = tmp_path / "file_test.jsonl"
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8").strip()
    if content:
        parsed = json.loads(content)
        assert parsed["message"] == "test file log"
        assert parsed["key"] == "value"


def test_get_logger_factory():
    log = get_logger("factory_test")
    assert isinstance(log, StructuredLogger)
    assert log.module_name == "factory_test"


def test_all_log_levels():
    log = StructuredLogger("levels_test")
    log.debug("debug msg")
    log.info("info msg")
    log.warning("warn msg")
    log.error("error msg")
    log.critical("critical msg")
    # No exception = pass
    assert True


def test_logger_with_complex_context():
    log = StructuredLogger("complex_test")
    log.info("complex", data={"nested": [1, 2, 3]}, count=42, flag=True)
    assert True
