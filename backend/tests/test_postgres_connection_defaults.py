"""Core planning defaults preserve operator and pooler connection contracts."""

import os
from urllib.parse import parse_qs, urlsplit

import pytest

from app.services.db_init import postgres_connection_dsn


@pytest.mark.unit
def test_default_planning_preserves_credentials_ssl_and_libpq_compatibility(
    monkeypatch,
):
    monkeypatch.delenv("INTEGRAL_POSTGRES_PLAN_CACHE_MODE", raising=False)
    monkeypatch.delenv("JVSPATIAL_POSTGRES_POOLER_MODE", raising=False)
    dsn = "postgresql://user:p%40ss%2Fword@db:5432/core?sslmode=require&application_name=Core%20QA"
    result = postgres_connection_dsn(dsn)
    before, after = urlsplit(dsn), urlsplit(result)
    assert (before.scheme, before.netloc, before.path) == (
        after.scheme,
        after.netloc,
        after.path,
    )
    query = parse_qs(after.query)
    assert "options=-c%20plan_cache_mode%3Dforce_custom_plan" in after.query
    assert query == {
        "sslmode": ["require"],
        "application_name": ["Core QA"],
        "options": ["-c plan_cache_mode=force_custom_plan"],
    }
    assert postgres_connection_dsn(result) == result


@pytest.mark.unit
@pytest.mark.parametrize(
    "query", ["options=-c%20work_mem%3D8MB", "options=", "plan_cache_mode=auto"]
)
def test_explicit_operator_startup_options_are_unchanged(monkeypatch, query):
    monkeypatch.delenv("JVSPATIAL_POSTGRES_POOLER_MODE", raising=False)
    dsn = "postgresql://user@db/core?" + query
    assert postgres_connection_dsn(dsn) == dsn


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["auto", "force_custom_plan", "force_generic_plan"])
def test_operator_can_select_a_planning_mode(monkeypatch, mode):
    monkeypatch.delenv("JVSPATIAL_POSTGRES_POOLER_MODE", raising=False)
    monkeypatch.setenv("INTEGRAL_POSTGRES_PLAN_CACHE_MODE", mode)
    query = parse_qs(
        urlsplit(postgres_connection_dsn("postgresql://user@db/core")).query
    )
    assert query["options"] == [f"-c plan_cache_mode={mode}"]


@pytest.mark.unit
def test_transaction_pooler_does_not_gain_session_startup_options(monkeypatch):
    monkeypatch.setenv("JVSPATIAL_POSTGRES_POOLER_MODE", "transaction")
    dsn = "postgresql://user@pooler/core?sslmode=require"
    assert postgres_connection_dsn(dsn) == dsn


@pytest.mark.unit
def test_invalid_mode_fails_without_echoing_a_connection_string(monkeypatch):
    monkeypatch.delenv("JVSPATIAL_POSTGRES_POOLER_MODE", raising=False)
    monkeypatch.setenv("INTEGRAL_POSTGRES_PLAN_CACHE_MODE", "unknown")
    with pytest.raises(ValueError, match="^Invalid INTEGRAL_POSTGRES_PLAN_CACHE_MODE$"):
        postgres_connection_dsn("postgresql://user:private@db/core")


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.skipif(
    os.getenv("INTEGRAL_TEST_DB") != "postgres", reason="PostgreSQL connection proof"
)
async def test_startup_planning_survives_actual_driver_pool_reset():
    from jvspatial.db import create_database

    dsn = os.environ["JVSPATIAL_POSTGRES_DSN"]
    database = create_database("postgres", dsn=postgres_connection_dsn(dsn))
    try:
        async with database._acquire_conn() as connection:
            mode = await connection.fetchval("SHOW plan_cache_mode")
            assert mode == "force_custom_plan"
        async with database._acquire_conn() as connection:
            assert await connection.fetchval("SHOW plan_cache_mode") == mode
    finally:
        await database.close()
