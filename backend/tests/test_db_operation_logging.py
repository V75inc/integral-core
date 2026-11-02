"""Verify published jvspatial logging levels through Core's database recorder."""

from importlib.metadata import distribution

import pytest
from jvspatial.db import create_database

from app.services.db_metrics import RequestDBOpRecorder, _RequestDBOps, request_db_ops

pytestmark = [pytest.mark.smoke, pytest.mark.unit]


async def database_with_data(tmp_path, **kwargs):
    raw = create_database("json", base_path=str(tmp_path))
    await raw.save("object", {"id": "example"})
    return create_database("json", base_path=str(tmp_path), observe=True, **kwargs)


def test_core_pins_published_jvspatial_version():
    # Read Core's installed wheel metadata rather than resolving the dependency
    # distribution by name: source-checkout tests may put the JV repository on
    # sys.path, whose generated egg-info intentionally has another version.
    assert "jvspatial==0.2.3" in distribution("integral-core").requires


@pytest.mark.asyncio
async def test_default_hides_routine_queries_without_disabling_metrics(
    tmp_path, caplog
):
    holder = _RequestDBOps()
    database = await database_with_data(
        tmp_path, metrics=RequestDBOpRecorder(), slow_query_ms=10_000
    )
    token = request_db_ops.set(holder)
    try:
        with caplog.at_level("DEBUG", logger="jvspatial.db.observable"):
            assert await database.find("object", {}) == [{"id": "example"}]
        assert not any("db.find" in record.message for record in caplog.records)
        assert holder.ops == 1
        assert holder.duration_seconds >= 0
    finally:
        request_db_ops.reset(token)


@pytest.mark.asyncio
async def test_slow_operation_is_counted_once_using_adapter_threshold(tmp_path):
    holder = _RequestDBOps()
    database = await database_with_data(
        tmp_path, metrics=RequestDBOpRecorder(), slow_query_ms=0
    )
    token = request_db_ops.set(holder)
    try:
        assert await database.find("object", {}) == [{"id": "example"}]
        assert holder.ops == 1
        assert holder.slow_ops == 1
        assert holder.duration_seconds >= 0
    finally:
        request_db_ops.reset(token)


@pytest.mark.asyncio
async def test_successful_slow_query_is_silent_but_errors_remain_visible(
    tmp_path, caplog
):
    import logging

    database = await database_with_data(tmp_path, slow_query_ms=0)
    with caplog.at_level(logging.DEBUG, logger="jvspatial.db.observable"):
        await database.find("object", {})
        logging.getLogger("jvspatial.db.observable").error("Database failure")
    assert not any("db.find" in record.message for record in caplog.records)
    assert any(
        record.levelno == logging.ERROR and record.message == "Database failure"
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_fast_failed_query_logs_error_and_preserves_exception(
    tmp_path, caplog, monkeypatch
):
    import logging

    database = await database_with_data(tmp_path, slow_query_ms=10_000)

    async def fail(*args, **kwargs):
        raise RuntimeError("query failed")

    monkeypatch.setattr(database.inner, "find", fail)
    with (
        caplog.at_level(logging.DEBUG, logger="jvspatial.db.observable"),
        pytest.raises(RuntimeError, match="query failed"),
    ):
        await database.find("object", {})
    assert any(
        record.levelno == logging.ERROR
        and "FAILED db.find" in record.message
        and record.success is False
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_debug_logging_still_suppresses_successful_query_tracing(
    tmp_path, caplog
):
    import logging

    database = await database_with_data(tmp_path, slow_query_ms=10_000)
    with caplog.at_level(logging.DEBUG, logger="jvspatial.db.observable"):
        await database.find("object", {})
    assert not any("db.find" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_upstream_info_is_quiet_without_core_logger_configuration(
    tmp_path, caplog
):
    import logging

    database = await database_with_data(tmp_path, slow_query_ms=10_000)
    with caplog.at_level(logging.INFO, logger="jvspatial.db.observable"):
        await database.find("object", {})
    assert not any("db.find" in record.message for record in caplog.records)


def test_request_recorder_preserves_upstream_and_survives_exporter_failure():
    from unittest.mock import Mock

    from app.services.db_metrics import _RequestAndUpstreamRecorder

    upstream = Mock()
    upstream.increment_counter.side_effect = RuntimeError("export unavailable")
    recorder = _RequestAndUpstreamRecorder(upstream)
    holder = _RequestDBOps()
    token = request_db_ops.set(holder)
    try:
        recorder.record_duration("jvspatial.db.op.duration_seconds", 0.2)
        recorder.increment_counter("jvspatial.db.op.count")
        recorder.increment_counter("jvspatial.db.op.slow_count")
        recorder.record_value("custom.gauge", 3)
        assert (holder.ops, holder.slow_ops, holder.duration_seconds) == (1, 1, 0.2)
        upstream.record_duration.assert_called_once()
        assert upstream.increment_counter.call_count == 2
        upstream.record_value.assert_called_once_with("custom.gauge", 3)
    finally:
        request_db_ops.reset(token)
