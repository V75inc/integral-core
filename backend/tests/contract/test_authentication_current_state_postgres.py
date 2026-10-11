"""Independent auth workers and password writes obey current primary state."""

import asyncio
import json
import secrets
import sys
import textwrap

import pytest
from jvspatial.api.auth.models import User as AuthUser
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.services.app_operations.transaction_scope import postgres_graph_transaction
from app.services.authentication import (
    IntegralAuthenticationService,
    get_authentication_service,
)

pytestmark = [pytest.mark.postgres, pytest.mark.contract, pytest.mark.asyncio]


@pytest.mark.parametrize("held_disable", [False, True])
async def test_other_process_cannot_validate_or_reactivate_disabled_identity(
    postgres_raw_db, held_disable
):
    secret = secrets.token_urlsafe(48)
    service = IntegralAuthenticationService(
        GraphContext(database=postgres_raw_db), jwt_secret=secret
    )
    identity = AuthUser(
        email="worker-identity@example.com", password_hash="fixture", is_active=True
    )
    await service.context.save(identity)
    token, _ = service._generate_jwt_token(identity.id, identity.email)
    script = textwrap.dedent(
        """
        import asyncio,json,sys
        from jvspatial.db.factory import create_database
        from jvspatial.core.context import GraphContext
        from app.services.authentication import IntegralAuthenticationService
        async def run():
            message=json.loads(await asyncio.to_thread(sys.stdin.readline))
            database=create_database(db_type="postgres",min_size=1,max_size=1)
            service=IntegralAuthenticationService(
                GraphContext(database=database),jwt_secret=message["secret"])
            try:
                assert await service.validate_token(message["token"]) is not None
                print("READY",flush=True)
                assert await asyncio.to_thread(sys.stdin.readline)=="GO\\n"
                response=await service.validate_token(message["token"])
                row=await database.find_one("object",{"id":sys.argv[1]})
                print(json.dumps([response is not None,row["context"]["is_active"]]),flush=True)
            finally:
                await database.close()
        asyncio.run(run())
        """
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        identity.id,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        process.stdin.write(
            (json.dumps({"secret": secret, "token": token}) + "\n").encode()
        )
        await process.stdin.drain()
        assert (
            await asyncio.wait_for(process.stdout.readline(), timeout=20) == b"READY\n"
        )
        if held_disable:
            async with postgres_graph_transaction(postgres_raw_db) as transaction:
                await transaction.find_one_and_update(
                    "object",
                    {"id": identity.id},
                    {"$set": {"context.is_active": False}},
                )
                process.stdin.write(b"GO\n")
                await process.stdin.drain()
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(process.stdout.readline(), timeout=0.1)
        else:
            await postgres_raw_db.find_one_and_update(
                "object", {"id": identity.id}, {"$set": {"context.is_active": False}}
            )
            process.stdin.write(b"GO\n")
            await process.stdin.drain()
        result = await asyncio.wait_for(process.stdout.readline(), timeout=20)
        assert json.loads(result) == [False, False]
        assert await asyncio.wait_for(process.wait(), timeout=20) == 0
    finally:
        if process.returncode is None:
            process.kill()
        await process.communicate()


async def test_auth_factory_password_update_joins_and_rolls_back(postgres_raw_db):
    service = IntegralAuthenticationService(
        GraphContext(database=postgres_raw_db), jwt_secret=secrets.token_urlsafe(48)
    )
    identity = AuthUser(
        email="transaction-identity@example.com",
        password_hash="original",
        is_active=True,
    )
    await service.context.save(identity)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        with pytest.raises(RuntimeError, match="rollback"):
            async with postgres_graph_transaction(postgres_raw_db) as transaction:
                inside = get_authentication_service()
                assert inside.context.database is transaction
                current = await inside._get_user_by_id(identity.id)
                current.password_hash = "must-not-commit"
                await current.save()
                raise RuntimeError("rollback password change")
    row = await postgres_raw_db.find_one("object", {"id": identity.id})
    assert row["context"]["password_hash"] == "original"
    assert row["context"]["is_active"] is True
