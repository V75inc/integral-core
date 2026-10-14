"""Verify published jvspatial logging levels through Core's database recorder."""

import logging
from importlib.metadata import distribution, version

import pytest
from jvspatial.db import create_database

from app.config import Settings
from app.logging_config import configure_db_operation_logging
from app.services.db_metrics import RequestDBOpRecorder, _RequestDBOps, request_db_ops

pytestmark = [pytest.mark.smoke, pytest.mark.unit]


@pytest.fixture(autouse=True)
def restore_logger_level():
    logger = logging.getLogger("jvspatial.db.observable")
    original = logger.level
    yield
    logger.setLevel(original)


async def database_with_data(tmp_path, **kwargs):
    raw = create_database("json", base_path=str(tmp_path))
    await raw.save("object", {"id": "example"})
    return create_database("json", base_path=str(tmp_path), observe=True, **kwargs)


def test_published_jvspatial_version():
    assert version("jvspatial") == "0.2.2"
    assert "jvspatial==0.2.2" in distribution("integral-core").requires


@pytest.mark.asyncio
async def test_default_hides_routine_queries_without_disabling_metrics(
    tmp_path, caplog
):
    settings = Settings(_env_file=None)
    assert settings.DB_OPERATION_LOG_LEVEL == "INFO"
    configure_db_operation_logging(settings.DB_OPERATION_LOG_LEVEL)
    holder = _RequestDBOps()
    database = await database_with_data(
        tmp_path, metrics=RequestDBOpRecorder(), slow_query_ms=10_000
    )
    token = request_db_ops.set(holder)
    try:
        with caplog.at_level(logging.INFO):
            assert await database.find("object", {}) == [{"id": "example"}]
        assert not any("db.find" in record.message for record in caplog.records)
        assert holder.ops == 1
        assert holder.duration_seconds >= 0
    finally:
        request_db_ops.reset(token)


@pytest.mark.asyncio
async def test_slow_queries_are_counted_without_log_records(
    tmp_path, caplog, monkeypatch
):
    monkeypatch.setenv("JVSPATIAL_SLOW_QUERY_MS", "0")
    configure_db_operation_logging("INFO")
    holder = _RequestDBOps()
    database = await database_with_data(
        tmp_path, metrics=RequestDBOpRecorder(), slow_query_ms=0
    )
    token = request_db_ops.set(holder)
    try:
        with caplog.at_level(logging.DEBUG):
            await database.find("object", {})
        assert not any("db.find" in record.message for record in caplog.records)
        assert holder.ops == 1
        assert holder.slow_ops >= 1
    finally:
        request_db_ops.reset(token)


@pytest.mark.asyncio
async def test_failed_query_logs_error_and_preserves_exception(
    tmp_path, caplog, monkeypatch
):
    configure_db_operation_logging("INFO")
    database = await database_with_data(tmp_path, slow_query_ms=10_000)

    async def fail(*args, **kwargs):
        raise RuntimeError("query failed")

    monkeypatch.setattr(database.inner, "find", fail)
    with (
        caplog.at_level(logging.INFO),
        pytest.raises(RuntimeError, match="query failed"),
    ):
        await database.find("object", {})
    assert any(
        record.levelno == logging.ERROR
        and "FAILED db.find" in record.message
        and record.collection == "object"
        and record.success is False
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_debug_level_does_not_log_successful_queries(tmp_path, caplog):
    configure_db_operation_logging("DEBUG")
    database = await database_with_data(tmp_path, slow_query_ms=10_000)
    with caplog.at_level(logging.DEBUG):
        await database.find("object", {})
    assert not any("db.find" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_upstream_info_is_quiet_without_core_logger_configuration(
    tmp_path, caplog
):
    # Bypass Core's threshold entirely to prove the package fix, rather than
    # merely exercising our former WARNING-level suppression workaround.
    database = await database_with_data(tmp_path, slow_query_ms=10_000)
    with caplog.at_level(logging.INFO, logger="jvspatial.db.observable"):
        await database.find("object", {})
    assert not any("db.find" in record.message for record in caplog.records)
