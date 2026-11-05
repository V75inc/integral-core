"""Admission holds participant rows with a shared lock so a user's parallel writes don't queue."""

import asyncio

import pytest

from app.services import identity_admission as ia


class FakeConnection:
    def __init__(self, found=True):
        self.found = found
        self.sql = []

    async def fetchrow(self, sql, *params):
        self.sql.append((sql, params))
        return {"id": params[0]} if self.found else None


class FakeDB:
    schema_name = "public"


class FakeTransaction:
    def __init__(self, found=True):
        self._db = FakeDB()
        self._connection = FakeConnection(found)
        self._operation_lock = asyncio.Lock()

    def _ensure_active(self):
        pass


@pytest.mark.asyncio
async def test_the_row_is_locked_shared_not_exclusive():
    tx = FakeTransaction()
    assert await ia._hold_row_shared(tx, "object", "o.User.abc", "User") is True
    sql, params = tx._connection.sql[0]
    assert sql.endswith("FOR SHARE") and "FOR UPDATE" not in sql
    assert params == ("o.User.abc", "User")
    assert "public.object" in sql


@pytest.mark.asyncio
async def test_a_missing_row_is_reported_so_admission_refuses():
    assert await ia._hold_row_shared(FakeTransaction(found=False), "node", "n.User.x", "User") is False


@pytest.mark.asyncio
async def test_a_wrapped_transaction_is_unwrapped():
    class Wrapper:
        def __init__(self, inner):
            self.inner = inner

    tx = FakeTransaction()
    assert await ia._hold_row_shared(Wrapper(Wrapper(tx)), "object", "o.User.abc", "User") is True
    assert tx._connection.sql


@pytest.mark.asyncio
async def test_unsafe_collection_names_are_refused():
    with pytest.raises(Exception):
        await ia._hold_row_shared(FakeTransaction(), "object; drop table x", "o.User.abc", "User")


def test_admission_no_longer_takes_the_exclusive_lock():
    import inspect

    source = inspect.getsource(ia.identity_admission)
    assert 'find_one_and_update' not in source
    assert "_hold_row_shared" in source
