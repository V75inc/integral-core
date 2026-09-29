"""Service-layer tests for UserModelCredential upsert/dedupe."""

import base64
import os
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app.models.credentials import UserModelCredential
from app.services.model_credentials import (
    dedupe_user_model_credentials,
    get_credential_for_user,
    revoke_user_credential,
    upsert_user_credential,
)


@asynccontextmanager
async def _legacy_credential_context():
    """Use a schema without the new unique index for a legacy duplicate seed."""
    from jvspatial.core.context import (
        GraphContext,
        get_default_context,
        scoped_default_context_async,
    )

    if os.getenv("INTEGRAL_TEST_DB", "").lower() != "postgres":
        yield get_default_context()
        return

    import asyncpg
    from jvspatial.db import get_prime_database
    from jvspatial.db.postgres import PostgresDB

    prime = get_prime_database()
    while not isinstance(prime, PostgresDB):
        prime = prime.inner
    schema = f"credential_migration_{uuid.uuid4().hex[:12]}"
    connection = await asyncpg.connect(dsn=prime.dsn)
    try:
        await connection.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        await connection.close()

    isolated = PostgresDB(dsn=prime.dsn, schema_name=schema)
    try:
        context = GraphContext(isolated)
        async with scoped_default_context_async(context):
            yield context
    finally:
        await isolated.close()
        connection = await asyncpg.connect(dsn=prime.dsn)
        try:
            await connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            await connection.close()


@pytest.fixture
def enc_key(monkeypatch):
    key = base64.urlsafe_b64encode(b"3" * 32).decode().rstrip("=")
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", key)
    monkeypatch.setenv("INTEGRAL_AGENT_KEY_MODE", "hybrid")
    return key


@pytest.mark.asyncio
async def test_upsert_after_revoke_reuses_same_row(enc_key, test_user):
    """Revoke then save again must not create a second row for the same user."""
    auth_user_id = getattr(test_user, "user_id", None) or test_user.id
    with patch(
        "app.services.model_credentials.validate_provider_api_key",
        new=AsyncMock(return_value=(True, "validated")),
    ):
        first = await upsert_user_credential(
            user_id=auth_user_id,
            provider="openai",
            model="gpt-4o-mini",
            api_key="sk-live-test-key-1234",
        )
        await revoke_user_credential(auth_user_id)
        second = await upsert_user_credential(
            user_id=auth_user_id,
            provider="openai",
            model="gpt-4.1",
            api_key="sk-live-test-key-5678",
        )

    assert second.id == first.id
    assert second.is_active is True
    assert second.model == "gpt-4.1"
    rows = await UserModelCredential.find({"context.user_id": auth_user_id})
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_local_ollama_credential_saves_without_secret(enc_key, test_user):
    """Local Ollama records the model choice without creating a fake key."""
    from app.services.model_credentials import (
        decrypt_credential_api_key,
    )

    auth_user_id = getattr(test_user, "user_id", None) or test_user.id
    with patch(
        "app.services.model_credentials.validate_provider_api_key",
        new=AsyncMock(return_value=(True, "local Ollama reachable")),
    ) as validate:
        record = await upsert_user_credential(
            user_id=auth_user_id,
            provider="ollama_local",
            model="gemma4:e2b",
        )

    assert record.provider == "ollama_local"
    assert record.api_key_enc == ""
    assert record.key_fingerprint == ""
    assert decrypt_credential_api_key(record) == ""
    validate.assert_awaited_once_with("ollama_local", "")


@pytest.mark.asyncio
async def test_local_ollama_does_not_require_encryption_key(test_user, monkeypatch):
    """No secret is stored, so local-only setup works without vault config."""
    monkeypatch.setattr(
        "app.services.model_credentials.settings.INTEGRAL_AGENT_KEY_MODE", "hybrid"
    )
    monkeypatch.setattr(
        "app.services.model_credentials.encryption_available", lambda: False
    )
    monkeypatch.setattr(
        "app.services.model_credentials.encryption_unavailable_reason",
        lambda: "INTEGRAL_CREDENTIAL_ENC_KEY is not set",
    )
    auth_user_id = getattr(test_user, "user_id", None) or test_user.id
    with patch(
        "app.services.model_credentials.validate_provider_api_key",
        new=AsyncMock(return_value=(True, "local Ollama reachable")),
    ):
        record = await upsert_user_credential(
            user_id=auth_user_id,
            provider="ollama_local",
            model="gemma4:e2b",
        )

    assert record.api_key_enc == ""
    assert record.key_fingerprint == ""


@pytest.mark.asyncio
async def test_dedupe_user_model_credentials_collapses_duplicates(enc_key, test_user):
    """Startup dedupe keeps one canonical row per user_id."""
    auth_user_id = getattr(test_user, "user_id", None) or test_user.id
    inactive = UserModelCredential(
        user_id=auth_user_id,
        provider="openai",
        model="old",
        is_active=False,
        created_at="2020-01-01T00:00:00Z",
    )
    active = UserModelCredential(
        user_id=auth_user_id,
        provider="anthropic",
        model="new",
        is_active=True,
        created_at="2021-01-01T00:00:00Z",
    )
    async with _legacy_credential_context() as context:
        # Raw legacy rows predate the unique index. Object.save() would create
        # that index before a duplicate could be seeded on PostgreSQL.
        await context.database.save("object", await inactive.export())
        await context.database.save("object", await active.export())

        removed = await dedupe_user_model_credentials()
        assert removed == 1

        remaining = await UserModelCredential.find({"context.user_id": auth_user_id})
        assert len(remaining) == 1
        canonical = await get_credential_for_user(auth_user_id)
        assert canonical is not None
        assert canonical.id == active.id
        assert canonical.is_active is True
        if os.getenv("INTEGRAL_TEST_DB", "").lower() == "postgres":
            import asyncpg

            duplicate = UserModelCredential(user_id=auth_user_id)
            with pytest.raises(asyncpg.UniqueViolationError):
                await context.database.save("object", await duplicate.export())
