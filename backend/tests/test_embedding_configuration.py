"""Workspace embedding authority, isolation and bounded provider transport."""

import base64
import json
from dataclasses import asdict
from unittest.mock import AsyncMock

import httpx
import pytest
from jvspatial.core.context import get_default_context
from pydantic import ValidationError

from app.api.errors import InsufficientPermissionsError, ResourceConflictError
from app.models.credentials import UserModelConnection
from app.schemas.embedding_configuration import (
    EmbeddingModelChoice,
    EmbeddingModelWrite,
)
from app.services.retrieval import embedding_configuration as service
from app.services.retrieval.index_definition import digest, embedding_profile
from app.services.retrieval.profile_encoder import (
    WorkspaceEmbeddingUnavailable,
    encode_profile,
)
from app.services.workspace_model_configuration import configuration_record
from tests.test_resource_location import graph

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


def mock_embedding_http(monkeypatch, transport):
    """Inject native responses; guard routing is separately tested below."""
    from app.services import custom_model_transport

    original = httpx.AsyncClient
    factory = lambda **kwargs: original(transport=transport, **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", factory)

    monkeypatch.setattr(
        custom_model_transport,
        "CustomModelAsyncClient",
        lambda base, **kwargs: factory(
            timeout=kwargs["timeout"],
            trust_env=False,
            follow_redirects=False,
        ),
    )


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv(
        "INTEGRAL_CREDENTIAL_ENC_KEY", base64.urlsafe_b64encode(b"e" * 32).decode()
    )
    from app.config import settings

    monkeypatch.setattr(settings, "INTEGRAL_AGENT_KEY_MODE", "hybrid")
    monkeypatch.setattr(service, "overrides_supported", lambda: True)


async def fixture():
    user, workspace, app, track, entry = await graph()
    from uuid import uuid4

    from jvspatial.api.auth.models import User as AuthUser

    auth = await AuthUser.create(
        email=f"embedding-{uuid4().hex}@example.com", password_hash="fixture-only"
    )
    user.user_id = auth.id
    await user.save()
    connection = UserModelConnection(
        user_id=user.user_id,
        provider="custom_openai",
        base_url="http://localhost:8080/v1",
        name="Embeddings",
    )
    await connection.save()
    assert await service.owner_subject(workspace.id) == user.user_id
    body = EmbeddingModelWrite(
        mode="provider",
        connection_id=connection.id,
        model="test-embedding",
        dimensions=16,
        allow_workspace_text=True,
        revision=0,
    )
    return user, workspace, connection, body


async def test_local_profile_identity_is_unchanged():
    profile = embedding_profile()
    old = {
        k: v
        for k, v in asdict(profile).items()
        if k not in {"provider", "connection_id", "owner_id", "workspace_id"}
    }
    assert profile.id == digest(old)


async def test_owner_choice_is_private_revision_bound_and_durably_queued():
    user, workspace, connection, body = await fixture()
    with pytest.raises(InsufficientPermissionsError):
        await service.save("someone-else", workspace.id, body)
    saved = await service.save(user.user_id, workspace.id, body)
    assert saved["configuration"]["connection_id"] == connection.id
    assert (
        saved["effective_model"] == "test-embedding"
        and saved["rebuild_status"] == "pending"
    )
    assert saved["profile_id"] != embedding_profile().id
    with pytest.raises(ResourceConflictError):
        await service.save(user.user_id, workspace.id, body)
    profile = await service.resolve_workspace_embedding_profile(workspace.id)
    assert profile.workspace_id == workspace.id and profile.owner_id == user.user_id
    from dataclasses import replace

    assert replace(profile, dimensions=32).id != profile.id
    reset = await service.save(
        user.user_id, workspace.id, EmbeddingModelWrite(revision=1)
    )
    assert reset["profile_id"] == embedding_profile().id
    with pytest.raises(WorkspaceEmbeddingUnavailable):
        await encode_profile(["never sent"], profile)


async def test_consent_and_provider_ownership_are_required():
    user, workspace, connection, body = await fixture()
    with pytest.raises(ValidationError):
        EmbeddingModelChoice(mode="provider", connection_id=connection.id, model="test")
    connection.user_id = "another-owner"
    await connection.save()
    with pytest.raises(ValueError, match="owned provider"):
        await service.save(user.user_id, workspace.id, body)
    connection.user_id, connection.provider = user.user_id, "anthropic"
    await connection.save()
    with pytest.raises(ValueError, match="embedding API"):
        await service.save(user.user_id, workspace.id, body)
    assert (await configuration_record(workspace.id)).embedding_settings_revision == 0


async def test_member_sees_effective_model_without_private_connection():
    user, workspace, connection, body = await fixture()
    await service.save(user.user_id, workspace.id, body)
    from app.models.edges import IS_MEMBER_OF
    from app.models.nodes import User
    from app.services.app_graph import catalog_user

    member = await User.create(display_name="Member")
    await catalog_user(member)
    await member.connect(workspace, edge=IS_MEMBER_OF, role="member")
    view = await service.describe(member.id, workspace.id)
    assert not view["editable"] and view["effective_model"] == body.model
    assert connection.id not in str(view)


async def test_native_transport_never_uses_ambient_key_or_remote_workspace_in_test(
    monkeypatch,
):
    user, workspace, connection, body = await fixture()
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leave")
    calls = []

    def respond(request):
        import json

        payload = json.loads(request.content)
        calls.append((request, payload))
        assert "authorization" not in request.headers
        return httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [0.5] * 16}]}
        )

    mock_embedding_http(monkeypatch, httpx.MockTransport(respond))
    result = await service.test(
        user.user_id,
        workspace.id,
        EmbeddingModelChoice.model_validate(body.model_dump(exclude={"revision"})),
    )
    assert result["valid"]
    assert calls[0][1]["input"] == ["Integral embedding configuration test"]
    assert calls[0][0].url == "http://localhost:8080/v1/embeddings"
    assert (await configuration_record(workspace.id)).embedding_settings_revision == 0
    await service.save(user.user_id, workspace.id, body)
    profile = await service.resolve_workspace_embedding_profile(workspace.id)
    await encode_profile(["fixture only"], profile)
    await connection.delete()
    with pytest.raises(WorkspaceEmbeddingUnavailable):
        await encode_profile(["must not leave"], profile)
    assert len(calls) == 2


@pytest.mark.parametrize(
    "vectors", [[[]], [[float("nan")] * 16], [[0.0] * 15], [[False] * 16]]
)
async def test_malformed_provider_vectors_fail_closed(monkeypatch, vectors):
    user, workspace, connection, body = await fixture()
    profile = await service.selected_profile(workspace.id, body)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, content=json.dumps({"data": [{"index": 0, "embedding": vectors[0]}]})
        )
    )
    mock_embedding_http(monkeypatch, transport)
    with pytest.raises(WorkspaceEmbeddingUnavailable):
        await encode_profile(["fixture"], profile, require_active=False)


async def test_unsupported_store_and_platform_policy_reject_provider(monkeypatch):
    user, workspace, connection, body = await fixture()
    monkeypatch.setattr(service, "overrides_supported", lambda: False)
    with pytest.raises(ValueError, match="PostgreSQL"):
        await service.save(user.user_id, workspace.id, body)
    from app.config import settings

    monkeypatch.setattr(settings, "INTEGRAL_AGENT_KEY_MODE", "platform_only")
    with pytest.raises(ValueError, match="disabled"):
        await service.save(user.user_id, workspace.id, body)


@pytest.mark.parametrize("provider", ["openai", "openrouter", "ollama", "ollama_local"])
async def test_native_embedding_protocols_use_explicit_key_and_dimensions(
    monkeypatch, provider
):
    import json

    from app.services.credential_crypto import encrypt_secret_for_storage

    user, workspace, connection, body = await fixture()
    connection.provider = provider
    connection.base_url = ""
    connection.api_key_enc = (
        ""
        if provider == "ollama_local"
        else encrypt_secret_for_storage("fixture-key", aad=user.user_id)
    )
    await connection.save()
    body.model = (
        "text-embedding-3-small"
        if provider in {"openai", "openrouter"}
        else "embeddinggemma"
    )
    profile = await service.selected_profile(workspace.id, body)

    def respond(request):
        payload = json.loads(request.content)
        assert payload["dimensions"] == 16
        if provider.startswith("ollama"):
            assert payload["truncate"] is False
            assert request.url.path == "/api/embed"
            output = {"embeddings": [[0.5] * 16]}
        else:
            assert payload["encoding_format"] == "float"
            assert request.url.path.endswith("/embeddings")
            output = {"data": [{"index": 0, "embedding": [0.5] * 16}]}
        assert request.headers.get("authorization") == (
            None if provider == "ollama_local" else "Bearer fixture-key"
        )
        return httpx.Response(200, json=output)

    mock_embedding_http(monkeypatch, httpx.MockTransport(respond))
    assert await encode_profile(["synthetic"], profile, require_active=False) == [
        [0.5] * 16
    ]


async def test_provider_failure_degrades_only_its_request(monkeypatch):
    from unittest.mock import Mock

    from app.api import retrieve
    from app.schemas.policy import Subject
    from app.schemas.retrieve import RetrieveRequest
    from app.services.retrieval import search_service
    from app.services.retrieval.pgvector_driver import PgVectorEmbeddingStore

    monkeypatch.setattr(
        retrieve,
        "get_embedding_store",
        lambda: PgVectorEmbeddingStore(
            dsn="postgresql://fixture:fixture@localhost/fixture"
        ),
    )
    monkeypatch.setattr(
        search_service,
        "retrieve_passages",
        AsyncMock(side_effect=WorkspaceEmbeddingUnavailable("fixture unavailable")),
    )
    monkeypatch.setattr(retrieve, "_retrieve_graph", AsyncMock(return_value=([], 0, 0)))
    disable = Mock()
    monkeypatch.setattr(retrieve, "disable_semantic_retrieval_runtime", disable)
    result = await retrieve._dispatch_retrieve_modes(
        Subject(kind="human", id="fixture"),
        RetrieveRequest(query="fixture", mode="semantic"),
        requested_mode="semantic",
        effective_mode="semantic",
        degraded=False,
        k=10,
        top_n=5,
    )
    assert result[-2:] == ("graph", True)
    disable.assert_not_called()


async def test_ordinary_workspace_saves_cannot_restore_old_provider_choices():
    user, workspace, connection, body = await fixture()
    # Keep an old ordinary Workspace object across configuration changes.
    await service.save(user.user_id, workspace.id, body)
    workspace.name = "A concurrent rename"
    workspace.storage_bytes_used = 42
    await workspace.save()
    assert (
        await service.resolve_workspace_embedding_profile(workspace.id)
    ).connection_id == connection.id
    await service.save(user.user_id, workspace.id, EmbeddingModelWrite(revision=1))
    await workspace.save()
    assert (
        await service.resolve_workspace_embedding_profile(workspace.id)
    ).id == embedding_profile().id
    exported = await workspace.export(flat=True)
    assert connection.id not in str(exported)
    from app.models.edges import HAS_MODEL_CONFIGURATION
    from app.models.nodes import WorkspaceModelConfiguration

    records = await workspace.nodes(
        edge=[HAS_MODEL_CONFIGURATION],
        node=[WorkspaceModelConfiguration._entity_name()],
        direction="out",
        limit=2,
    )
    assert len(records) == 1 and records[0].embedding_settings_revision == 2


async def test_detached_configuration_fails_closed():
    user, workspace, connection, body = await fixture()
    await service.save(user.user_id, workspace.id, body)
    record = await configuration_record(workspace.id)
    from app.models.edges import HAS_MODEL_CONFIGURATION

    context = get_default_context()
    edges = await context.find_edges_between(
        workspace.id, record.id, edge_class=HAS_MODEL_CONFIGURATION
    )
    await context.database.delete("edge", edges[0].id)
    with pytest.raises(WorkspaceEmbeddingUnavailable):
        await service.resolve_workspace_embedding_profile(workspace.id)


async def test_former_workspace_owner_can_remove_their_private_provider():
    from uuid import uuid4

    from jvspatial.api.auth.models import User as AuthUser

    from app.models.edges import IS_MEMBER_OF
    from app.models.nodes import User
    from app.services.ai_configuration import remove_item
    from app.services.app_graph import catalog_user

    user, workspace, connection, body = await fixture()
    await service.save(user.user_id, workspace.id, body)
    context = get_default_context()
    membership = (
        await context.find_edges_between(user.id, workspace.id, edge_class=IS_MEMBER_OF)
    )[0]
    membership.role = "member"
    await membership.save()
    auth = await AuthUser.create(
        email=f"new-owner-{uuid4().hex}@example.com", password_hash="fixture-only"
    )
    replacement = await User.create(display_name="Replacement", user_id=auth.id)
    await catalog_user(replacement)
    await replacement.connect(workspace, edge=IS_MEMBER_OF, role="owner")
    with pytest.raises(WorkspaceEmbeddingUnavailable):
        await service.resolve_workspace_embedding_profile(workspace.id)
    await remove_item(user.user_id, connection.id, connection=True)
    assert await context.database.get("object", connection.id) is None


async def test_platform_policy_uses_local_default_without_erasing_saved_override(
    monkeypatch,
):
    user, workspace, connection, body = await fixture()
    await service.save(user.user_id, workspace.id, body)
    selected = await service.resolve_workspace_embedding_profile(workspace.id)
    from app.config import settings

    monkeypatch.setattr(settings, "INTEGRAL_AGENT_KEY_MODE", "platform_only")
    assert (
        await service.resolve_workspace_embedding_profile(workspace.id)
    ).id == embedding_profile().id
    view = await service.describe(user.user_id, workspace.id)
    assert (
        not view["editable"]
        and view["effective_provider"] == "local"
        and not view["error"]
    )
    with pytest.raises(WorkspaceEmbeddingUnavailable):
        await encode_profile(["never sent"], selected)
    monkeypatch.setattr(settings, "INTEGRAL_AGENT_KEY_MODE", "hybrid")
    assert (
        await service.resolve_workspace_embedding_profile(workspace.id)
    ).id == selected.id


async def test_failed_workspace_rebuild_is_backed_off_without_losing_cursor():
    import time

    from app.services.retrieval.workspace_reindex import process_workspace_rebuilds

    user, workspace, connection, body = await fixture()
    await service.save(user.user_id, workspace.id, body)
    await connection.delete()
    database = get_default_context().database
    await process_workspace_rebuilds()
    rows = await database.find(
        "object",
        {"entity": service.REBUILD, "context.workspace_id": workspace.id},
        limit=1,
    )
    first = rows[0]["context"]
    assert first["status"] == "pending" and first["error"]
    assert first["attempt"] == 1 and first["due"] > time.time() and first["after"] == ""
    await process_workspace_rebuilds()
    second = await database.get("object", rows[0]["id"])
    assert second["context"]["attempt"] == 1


async def test_custom_embeddings_cannot_bypass_the_harness_outbound_guard(monkeypatch):
    from unittest.mock import Mock

    from app.services import custom_model_transport

    user, workspace, connection, body = await fixture()
    profile = await service.selected_profile(workspace.id, body)
    guarded = Mock(side_effect=ValueError("synthetic outbound guard rejection"))
    monkeypatch.setattr(custom_model_transport, "CustomModelAsyncClient", guarded)
    with pytest.raises(WorkspaceEmbeddingUnavailable, match="check the model"):
        await encode_profile(["synthetic"], profile, require_active=False)
    assert guarded.call_args.args[0] == connection.base_url
    assert guarded.call_args.kwargs["omit_authorization"] is True


async def test_embedding_response_byte_budget_is_enforced(monkeypatch):
    user, workspace, connection, body = await fixture()
    profile = await service.selected_profile(workspace.id, body)
    mock_embedding_http(
        monkeypatch,
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "data": [{"index": 0, "embedding": [0.5] * 16}],
                    "padding": "x" * (8 * 1024 * 1024),
                },
            )
        ),
    )
    with pytest.raises(WorkspaceEmbeddingUnavailable):
        await encode_profile(["synthetic"], profile, require_active=False)
