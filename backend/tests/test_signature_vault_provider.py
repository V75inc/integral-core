"""A host may adapt the user vault without overriding scope admission."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api import user_signatures
from app.api.errors import InsufficientPermissionsError
from app.exceptions import AuthorizationUnavailableError
from app.services import host_hooks


@pytest.fixture
def host_vault(monkeypatch):
    monkeypatch.setattr(host_hooks, "_signature_vault_provider", None)
    provider = AsyncMock(return_value=[{"id": "owned"}])
    host_hooks.register_signature_vault_provider(provider)
    monkeypatch.setattr(user_signatures, "resolve_principal_id", lambda _: "principal")
    personal = AsyncMock(return_value=[{"id": "personal"}])
    monkeypatch.setattr(user_signatures, "list_user_signatures", personal)
    request = SimpleNamespace(headers={"x-integral-scope": "ws:workspace"})
    return provider, personal, request


@pytest.mark.asyncio
async def test_provider_receives_resolved_workspace_and_principal(
    monkeypatch, host_vault
):
    provider, personal, request = host_vault
    resolver = AsyncMock(return_value="verified-workspace")
    monkeypatch.setattr(user_signatures, "resolve_workspace_id_from_request", resolver)

    assert await user_signatures.api_list_my_signatures(request) == {
        "signatures": [{"id": "owned"}],
        "total": 1,
    }
    resolver.assert_awaited_once_with(request, "principal")
    provider.assert_awaited_once_with("list", "verified-workspace", "principal", {})
    personal.assert_not_awaited()


@pytest.mark.asyncio
async def test_denied_workspace_never_reads_or_writes_personal_vault(
    monkeypatch, host_vault
):
    provider, personal, request = host_vault
    monkeypatch.setattr(
        user_signatures,
        "resolve_workspace_id_from_request",
        AsyncMock(side_effect=InsufficientPermissionsError()),
    )
    with pytest.raises(InsufficientPermissionsError):
        await user_signatures.api_list_my_signatures(request)
    provider.assert_not_awaited()
    personal.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["provider_error", "malformed", "not_installed"])
async def test_only_explicit_provider_absence_allows_personal_fallback(
    monkeypatch, host_vault, failure
):
    provider, personal, request = host_vault
    monkeypatch.setattr(
        user_signatures,
        "resolve_workspace_id_from_request",
        AsyncMock(return_value="workspace"),
    )
    if failure == "provider_error":
        provider.side_effect = AuthorizationUnavailableError()
    else:
        provider.return_value = (
            None if failure == "not_installed" else {"unexpected": True}
        )
    if failure == "not_installed":
        assert (await user_signatures.api_list_my_signatures(request))[
            "signatures"
        ] == [{"id": "personal"}]
        personal.assert_awaited_once_with("principal")
    else:
        with pytest.raises(AuthorizationUnavailableError):
            await user_signatures.api_list_my_signatures(request)
        personal.assert_not_awaited()
