"""Current external authority, not an old backup, governs recovered access."""

import copy
import uuid

import bcrypt
import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async
from jvspatial.db import get_database_manager
from jvspatial.db.postgres import PostgresDB

from app.main import _startup
from app.models.edges import COLLABORATES_ON
from app.services import access_recovery
from tests.contract.test_access_lifecycle_postgres import graph
from tests.test_access_lifecycle import verified_subject

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres, pytest.mark.contract]


@pytest.fixture
async def recovery_db(postgres_raw_db):
    """Use a genuinely independent schema for each installation recovery image."""
    import asyncpg

    schema = "recovery_" + uuid.uuid4().hex
    connection = await asyncpg.connect(dsn=postgres_raw_db.dsn)
    try:
        await connection.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        await connection.close()
    database = PostgresDB(dsn=postgres_raw_db.dsn, schema_name=schema)
    manager = get_database_manager()
    previous = manager.get_prime_database()
    manager.set_prime_database(database)
    try:
        yield database
    finally:
        await database.close()
        manager.set_prime_database(previous)
        connection = await asyncpg.connect(dsn=postgres_raw_db.dsn)
        try:
            await connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            await connection.close()


async def setup_recovery(database, tmp_path):
    """Retain current revocation independently, then simulate an old grant image."""
    owner, workspace, app, track, _ = await graph()
    from jvspatial.api.auth.models import User as AuthUser

    owner_auth = await AuthUser.create(
        email="recovery-owner@example.com",
        password_hash=bcrypt.hashpw(
            b"initial-valid-owner-password", bcrypt.gensalt()
        ).decode(),
    )
    owner.user_id = owner_auth.id
    owner.email_verified = True
    owner.verified_email = owner_auth.email
    await owner.save()
    member = await verified_subject(workspace)
    await member.connect(track, edge=COLLABORATES_ON, role="editor")
    old = await database.find(
        "edge", {"entity": "COLLABORATES_ON", "source": member.id, "target": track.id}
    )
    assert len(old) == 1
    await database.delete("edge", old[0]["id"])
    directory = tmp_path / "independent"
    await access_recovery.checkpoint(
        installation_id="restore-test", directory=directory, home=tmp_path / "home"
    )
    await database.save("edge", old[0])
    return owner, workspace, app, track, member, old[0], directory


async def test_restore_reconciles_current_revocation_and_persists_receipt(
    recovery_db, tmp_path, monkeypatch
):
    """Reviewed current checkpoint removes resurrected direct grant atomically."""
    async with scoped_default_context_async(GraphContext(database=recovery_db)):
        _, _, _, track, member, old, directory = await setup_recovery(
            recovery_db, tmp_path
        )
        report = await access_recovery.review(
            installation_id="restore-test", directory=directory
        )
        assert report["complete"] is True
        assert "password_hash" not in str(report)
        receipt = await access_recovery.apply(
            installation_id="restore-test",
            directory=directory,
            expected_digest=report["review_digest"],
            owner_ids=[],
        )
        assert not await recovery_db.find("edge", {"id": old["id"]}, limit=1)
        raw = await recovery_db.get("object", receipt["receipt_id"])
        assert raw["context"]["review_digest"] == report["review_digest"]
        from app import local_runtime

        home = tmp_path / "reviewed-installation"
        config = local_runtime.initialize(home)
        config["installation_id"] = "restore-test"
        local_runtime.private_json(home / "installation.json", config)
        marker = {**receipt, "installation_id": "restore-test"}
        local_runtime.private_json(home / "recovery.json", marker)
        monkeypatch.setenv("INTEGRAL_RECOVERY_MARKER", str(home / "recovery.json"))
        # Server import/bootstrap happened before fixture admission. The genuine
        # startup proof must now read this exact isolated primary store.
        from jvspatial.core.context import get_default_context

        assert get_default_context().database is recovery_db
        assert get_database_manager().get_prime_database() is recovery_db
        await _startup()
        marker["receipt_id"] = "o.AccessRecoveryReceipt." + "0" * 64
        local_runtime.private_json(home / "recovery.json", marker)
        from app.exceptions import AuthorizationUnavailableError

        with pytest.raises(AuthorizationUnavailableError):
            await _startup()
        from app.services.permissions import resolve_role

        assert await resolve_role(member.id, "track", track.id) != "editor"


async def test_restore_stale_review_and_receipt_failure_have_no_effects(
    recovery_db, tmp_path, monkeypatch
):
    """Review drift denies; late receipt error rolls back actual grant deletion."""
    async with scoped_default_context_async(GraphContext(database=recovery_db)):
        _, _, _, _, _, old, directory = await setup_recovery(recovery_db, tmp_path)
        report = await access_recovery.review(
            installation_id="restore-test", directory=directory
        )
        changed = copy.deepcopy(old)
        changed["context"]["role"] = "viewer"
        await recovery_db.save("edge", changed)
        with pytest.raises(ValueError, match="changed"):
            await access_recovery.apply(
                installation_id="restore-test",
                directory=directory,
                expected_digest=report["review_digest"],
                owner_ids=[],
            )
        assert (await recovery_db.get("edge", old["id"]))["context"]["role"] == "viewer"
        report = await access_recovery.review(
            installation_id="restore-test", directory=directory
        )
        original = access_recovery._record_receipt

        async def fail_receipt(database, value):
            await original(database, value)
            raise RuntimeError("late recovery receipt")

        monkeypatch.setattr(access_recovery, "_record_receipt", fail_receipt)
        with pytest.raises(RuntimeError, match="receipt"):
            await access_recovery.apply(
                installation_id="restore-test",
                directory=directory,
                expected_digest=report["review_digest"],
                owner_ids=[],
            )
        assert (await recovery_db.get("edge", old["id"]))["context"]["role"] == "viewer"


async def test_recovery_fallback_requires_owner_review_and_removes_old_member_rights(
    recovery_db, tmp_path
):
    """No checkpoint means explicit owner-only recovery with credential reset."""
    async with scoped_default_context_async(GraphContext(database=recovery_db)):
        owner, workspace, _, track, member, old, _ = await setup_recovery(
            recovery_db, tmp_path
        )
        report = await access_recovery.review(
            installation_id="restore-test", directory=None
        )
        with pytest.raises(ValueError, match="explicit"):
            await access_recovery.apply(
                installation_id="restore-test",
                directory=None,
                expected_digest=report["review_digest"],
                owner_ids=[],
            )
        rows = await recovery_db.find("node", {"entity": "Workspace"})
        owners = set()
        for row in rows:
            edges = await recovery_db.find(
                "edge", {"target": row["id"], "entity": "OWNS"}
            )
            memberships = await recovery_db.find(
                "edge",
                {
                    "target": row["id"],
                    "entity": "IS_MEMBER_OF",
                    "context.role": "owner",
                },
            )
            owners.update(e["source"] for e in edges + memberships)
        receipt = await access_recovery.apply(
            installation_id="restore-test",
            directory=None,
            expected_digest=report["review_digest"],
            owner_ids=sorted(owners),
            password_hash=bcrypt.hashpw(
                b"new-reviewed-owner-password", bcrypt.gensalt()
            ).decode(),
        )
        assert receipt["mode"] == "credentials_disabled"
        assert not await recovery_db.find("edge", {"id": old["id"]})
        assert not await recovery_db.find(
            "edge",
            {"entity": "IS_MEMBER_OF", "source": member.id, "target": workspace.id},
        )
        assert (await recovery_db.get("node", track.id))["context"][
            "visibility"
        ] == "private"
        assert (await recovery_db.get("node", owner.id))["context"][
            "email_verified"
        ] is False
        owner_auth = await recovery_db.get("object", owner.user_id)
        member_auth = await recovery_db.get("object", member.user_id)
        assert owner_auth["context"]["is_active"] is True
        assert owner_auth["context"]["roles"] == ["user"]
        assert owner_auth["context"]["permissions"] == []
        assert bcrypt.checkpw(
            b"new-reviewed-owner-password",
            owner_auth["context"]["password_hash"].encode(),
        )
        assert not bcrypt.checkpw(
            b"initial-valid-owner-password",
            owner_auth["context"]["password_hash"].encode(),
        )
        assert member_auth["context"]["is_active"] is False
