"""Qualify jvspatial's public claim primitive for recoverable uploads."""

import asyncio
import sys
import time
import uuid

import pytest
from jvspatial import claim_record, release_claim

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def test_native_claim_serializes_reclaim_and_stale_release(postgres_raw_db):
    """One claimant wins; an expired owner cannot release its successor."""
    record_id = f"o.ChatUploadClaimProbe.{uuid.uuid4().hex}"
    await postgres_raw_db.save(
        "object",
        {
            "id": record_id,
            "entity": "ChatUploadClaimProbe",
            "context": {"state": "pending"},
        },
    )
    first, second = await asyncio.gather(
        claim_record(postgres_raw_db, "object", record_id, stale_seconds=60),
        claim_record(postgres_raw_db, "object", record_id, stale_seconds=60),
    )
    winners = [(doc, token) for doc, token in (first, second) if token]
    assert len(winners) == 1
    old_token = winners[0][1]
    await postgres_raw_db.find_one_and_update(
        "object", {"id": record_id}, {"$set": {"_jv_claim_until": time.time() - 1}}
    )
    expired_write = await postgres_raw_db.find_one_and_update(
        "object",
        {
            "id": record_id,
            "_jv_claim": old_token,
            "_jv_claim_until": {"$gt": time.time()},
        },
        {"$set": {"context.state": "completed"}},
    )
    assert expired_write is None
    recovered, new_token = await claim_record(
        postgres_raw_db, "object", record_id, stale_seconds=60
    )
    assert recovered is not None and new_token and new_token != old_token
    displaced_write = await postgres_raw_db.find_one_and_update(
        "object",
        {"id": record_id, "_jv_claim": old_token},
        {"$set": {"context.state": "completed"}},
    )
    assert displaced_write is None
    owned_write = await postgres_raw_db.find_one_and_update(
        "object",
        {
            "id": record_id,
            "_jv_claim": new_token,
            "_jv_claim_until": {"$gt": time.time()},
        },
        {"$set": {"context.state": "completed"}},
    )
    assert owned_write is not None and owned_write["context"]["state"] == "completed"
    await release_claim(postgres_raw_db, "object", record_id, old_token)
    _, third_token = await claim_record(
        postgres_raw_db, "object", record_id, stale_seconds=60
    )
    assert third_token is None
    await release_claim(postgres_raw_db, "object", record_id, new_token)
    _, fourth_token = await claim_record(
        postgres_raw_db, "object", record_id, stale_seconds=60
    )
    assert fourth_token is not None


async def test_native_claim_recovers_after_worker_process_exits(postgres_raw_db):
    """A new worker can reclaim a native claim after the original exits."""
    record_id = f"o.ChatUploadClaimProbe.{uuid.uuid4().hex}"
    await postgres_raw_db.save(
        "object", {"id": record_id, "entity": "ChatUploadClaimProbe", "context": {}}
    )
    worker = """
import asyncio, os, sys
from jvspatial import claim_record
from jvspatial.db.factory import create_database
async def run():
    db = create_database(db_type='postgres')
    _, token = await claim_record(db, 'object', sys.argv[1], stale_seconds=1)
    if not token:
        raise SystemExit(2)
    print(token, flush=True)
    os._exit(0)
asyncio.run(run())
"""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        worker,
        record_id,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
    assert proc.returncode == 0, stderr.decode()
    old_token = stdout.decode().strip().splitlines()[-1]
    assert old_token
    deadline = asyncio.get_running_loop().time() + 8
    new_token = None
    while asyncio.get_running_loop().time() < deadline:
        _, new_token = await claim_record(
            postgres_raw_db, "object", record_id, stale_seconds=60
        )
        if new_token:
            break
        await asyncio.sleep(0.1)
    assert new_token and new_token != old_token
    await release_claim(postgres_raw_db, "object", record_id, old_token)
    _, other = await claim_record(
        postgres_raw_db, "object", record_id, stale_seconds=60
    )
    assert other is None
