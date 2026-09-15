"""Hardening regressions for the MCP-as-connector subsystem (ADR-009/010).

One test per finding. Each fails against the pre-fix code:

- F-2  SSRF survived redirects: the URL was validated once, then handed to an
       MCP SDK httpx client built with ``follow_redirects=True``.
- F-3  OAuth discovery fetched, and POSTed credentials to, whatever host the
       remote MCP server named in ``WWW-Authenticate`` / its metadata.
- F-5  ``mcp_safe_auth_state`` was a denylist, so a catalog-install
       ``client_secret`` came back on every read.
- F-7  ``auth_state`` secrets and the stdio token-store file sat in plaintext,
       and nothing ever deleted the file.
- F-9  refresh / health / delete checked only ``connector.owner``.
- F-12 ``Workspace —HAS_CONNECTOR→ Connector`` was never wired (I-GRAPH-01).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

import httpx
import pytest
from httpx import AsyncClient

from app.exceptions import BadRequestError as UrlSafetyBadRequest

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mcp_echo_server.py"
_PUBLIC_IP = "93.184.216.34"
_METADATA_IP = "169.254.169.254"


@pytest.fixture
def public_dns(monkeypatch):
    """Resolve every test hostname to a public address, deterministically."""
    from app.services import url_safety

    monkeypatch.setattr(url_safety, "_resolve_host_ips", lambda host: [_PUBLIC_IP])
    return _PUBLIC_IP


@pytest.fixture
def enc_key(monkeypatch):
    """A deterministic credential-encryption key for at-rest assertions."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "A" * 43 + "=")
    return True


async def _workspace_id(client: AsyncClient) -> str:
    resp = await client.get("/api/workspaces")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    workspaces = body.get("workspaces") or body.get("items") or body
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("workspaces") or []
    return workspaces[0]["id"]


def _stdio_mount_body(env: Dict[str, str], name: str) -> Dict[str, Any]:
    return {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(_FIXTURE)],
        "env": env,
        "display_name": name,
    }


# ---------------------------------------------------------------------------
# F-2 — redirects, DNS pinning, port allowlist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guarded_client_refuses_redirect_to_link_local(public_dns):
    """A 302 towards cloud instance metadata aborts instead of being followed."""
    from app.services.url_safety import guarded_async_client

    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "mcp.example":
            return httpx.Response(
                302, headers={"Location": f"http://{_METADATA_IP}/latest/meta-data/"}
            )
        return httpx.Response(200, text="instance-credentials")

    transport = httpx.MockTransport(handler)
    client = guarded_async_client(timeout=5.0, transport=transport)
    async with client:
        with pytest.raises(UrlSafetyBadRequest):
            await client.get("https://mcp.example/mcp")

    assert not any(_METADATA_IP in url for url in seen), seen


@pytest.mark.asyncio
async def test_guarded_client_follows_public_redirect(public_dns):
    """The guard is a filter, not a ban: public hops still work."""
    from app.services.url_safety import guarded_async_client

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp":
            return httpx.Response(302, headers={"Location": "https://mcp.example/v2"})
        return httpx.Response(200, text="ok")

    client = guarded_async_client(timeout=5.0, transport=httpx.MockTransport(handler))
    async with client:
        resp = await client.get("https://mcp.example/mcp")
    assert resp.status_code == 200


def test_outbound_port_allowlist_refuses_nonstandard_port(public_dns):
    """Outbound fetches are limited to 80/443 unless explicitly configured."""
    from app.services.url_safety import (
        validate_outbound_http_url_sync,
        validate_public_http_url_sync,
    )

    # The un-restricted validator still accepts it — the port gate is the
    # outbound-specific control, not a change to attachment validation.
    validate_public_http_url_sync("https://mcp.example:6379/mcp")
    with pytest.raises(UrlSafetyBadRequest):
        validate_outbound_http_url_sync("https://mcp.example:6379/mcp")
    validate_outbound_http_url_sync("https://mcp.example/mcp")


def test_extra_outbound_ports_are_operator_configurable(public_dns, monkeypatch):
    """Self-hosted deployments can widen the allowlist deliberately."""
    from app.services.url_safety import validate_outbound_http_url_sync

    monkeypatch.setenv("INTEGRAL_OUTBOUND_EXTRA_PORTS", "8443")
    validate_outbound_http_url_sync("https://mcp.example:8443/mcp")


def test_dns_pin_refuses_host_that_resolves_private(monkeypatch):
    """Pinning re-checks the exact records it is about to force."""
    import socket

    from app.services import url_safety

    def fake_getaddrinfo(host, port, *args: Any, **kwargs: Any):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (_METADATA_IP, port or 0)),
        ]

    monkeypatch.setattr(url_safety, "_real_getaddrinfo", fake_getaddrinfo)
    with (
        pytest.raises(UrlSafetyBadRequest),
        url_safety.pin_public_dns("https://rebind.example/mcp"),
    ):
        pass


def test_mcp_client_injects_the_guarded_http_factory():
    """The streamable-HTTP transport must not build a raw SDK client."""
    from app.agentive.connectors.mcp_client import guarded_mcp_http_client
    from app.services.url_safety import GuardedAsyncClient

    client = guarded_mcp_http_client(headers={"Authorization": "Bearer x"})
    assert isinstance(client, GuardedAsyncClient)


# ---------------------------------------------------------------------------
# F-3 — OAuth discovery / credential exfiltration
# ---------------------------------------------------------------------------


def _probe_response(www_authenticate: str) -> httpx.Response:
    request = httpx.Request("POST", "https://mcp.example/mcp")
    return httpx.Response(
        401, request=request, headers={"WWW-Authenticate": www_authenticate}
    )


@pytest.mark.asyncio
async def test_resource_metadata_url_must_be_same_origin(public_dns):
    """The ``resource_metadata`` URL is server-supplied — it is not trusted."""
    from app.agentive.connectors.mcp_oauth import start_mcp_oauth_session
    from app.api.errors import BadRequestError

    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BadRequestError):
            await start_mcp_oauth_session(
                "https://mcp.example/mcp",
                probe_response=_probe_response(
                    'Bearer resource_metadata="https://evil.example/.well-known/x"'
                ),
                redirect_uri="https://app.example/cb",
                client=client,
            )
    assert not any("evil.example" in url for url in seen), seen


@pytest.mark.asyncio
async def test_authorization_server_from_prm_must_be_same_origin(public_dns):
    """A PRM document cannot point discovery at an unrelated host."""
    from app.agentive.connectors.mcp_oauth import start_mcp_oauth_session
    from app.api.errors import BadRequestError

    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "mcp.example":
            return httpx.Response(
                200,
                json={
                    "resource": "https://mcp.example/mcp",
                    "authorization_servers": ["https://evil.example"],
                },
            )
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BadRequestError):
            await start_mcp_oauth_session(
                "https://mcp.example/mcp",
                probe_response=_probe_response("Bearer"),
                redirect_uri="https://app.example/cb",
                client=client,
            )
    assert not any("evil.example" in url for url in seen), seen


@pytest.mark.asyncio
async def test_token_endpoint_host_must_match_issuer(public_dns):
    """DCR never runs when the AS metadata points its endpoints elsewhere."""
    from app.agentive.connectors.mcp_oauth import start_mcp_oauth_session
    from app.api.errors import BadRequestError

    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if "oauth-protected-resource" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "resource": "https://mcp.example/mcp",
                    "authorization_servers": ["https://mcp.example"],
                },
            )
        if request.url.path == "/register":
            # A registration the pre-fix code accepted happily, leaving the
            # flow pointed at evil.example for the token exchange.
            return httpx.Response(
                201,
                json={
                    "client_id": "registered-client",
                    "client_secret": "registered-secret",
                    "redirect_uris": ["https://app.example/cb"],
                },
            )
        return httpx.Response(
            200,
            json={
                "issuer": "https://mcp.example",
                "authorization_endpoint": "https://mcp.example/authorize",
                "token_endpoint": "https://evil.example/token",
                "registration_endpoint": "https://mcp.example/register",
                "response_types_supported": ["code"],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BadRequestError):
            await start_mcp_oauth_session(
                "https://mcp.example/mcp",
                probe_response=_probe_response("Bearer"),
                redirect_uri="https://app.example/cb",
                client=client,
            )
    assert not any("evil.example" in url for url in seen), seen


@pytest.mark.asyncio
async def test_token_exchange_refuses_link_local_token_endpoint():
    """A persisted token_endpoint is revalidated before the code is sent."""
    from app.agentive.connectors.mcp_oauth import exchange_mcp_oauth_code

    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={})

    oauth = {
        # Same-origin with the endpoint, so this test still exercises the
        # private-address refusal rather than the (stricter, separate)
        # missing-issuer refusal added later.
        "issuer": f"http://{_METADATA_IP}/",
        "token_endpoint": f"http://{_METADATA_IP}/token",
        "client_id": "abc",
        "redirect_uri": "https://app.example/cb",
        "code_verifier": "verifier",
        "client_secret": "super-secret",
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(UrlSafetyBadRequest):
            await exchange_mcp_oauth_code(oauth, "the-code", client=client)
    assert seen == []


# ---------------------------------------------------------------------------
# F-5 — wire redaction is an allowlist
# ---------------------------------------------------------------------------


def test_mcp_safe_auth_state_drops_unknown_and_secret_keys():
    """Anything not explicitly allowed is dropped, not passed through."""
    from app.schemas.agentive.connectors import mcp_safe_auth_state

    safe = mcp_safe_auth_state(
        {
            "url": "https://mcp.example/mcp",
            "transport": "streamable_http",
            "client_secret": "cs-plaintext",
            "client_id": "not-a-secret-but-not-allowlisted",
            "refresh_token": "rt-plaintext",
            "some_future_field": "leak-me",
            "env": {"MCP_SECRET": "env-plaintext"},
        }
    )
    assert safe["url"] == "https://mcp.example/mcp"
    assert safe["env"] == {"MCP_SECRET": "[redacted]"}
    for dropped in ("client_secret", "refresh_token", "some_future_field", "client_id"):
        assert dropped not in safe, dropped
    blob = str(safe)
    for secret in ("cs-plaintext", "rt-plaintext", "leak-me", "env-plaintext"):
        assert secret not in blob, secret


def test_redact_auth_state_recurses_into_nested_dicts():
    """The generic redactor missed nested token bundles entirely."""
    from app.schemas.agentive.connectors import redact_auth_state

    out = redact_auth_state(
        {
            "oauth": {
                "status": "authorized",
                "tokens": {"access_token": "nested-access-value"},
            },
            "env": {"QUICKBOOKS_CLIENT_SECRET": "nested-secret-value"},
            "realm_id": "123",
        }
    )
    assert out["realm_id"] == "123"
    assert "nested-access-value" not in str(out)
    assert "nested-secret-value" not in str(out)


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_mcp_connector_wire_never_echoes_client_secret(
    authenticated_client: AsyncClient,
):
    """Create / GET / LIST / PATCH of an MCP connector echo no credentials."""
    secrets = {
        "client_secret": "qb-client-secret",
        "refresh_token": "qb-refresh-token",
        "api_key": "an-api-key",
    }
    r = await authenticated_client.post(
        "/api/agentive/connectors",
        json={
            "kind": "mcp",
            "auth_state": {
                "url": "https://mcp.example/mcp",
                "transport": "streamable_http",
                "catalog_slug": "quickbooks_mcp",
                **secrets,
            },
        },
    )
    assert r.status_code in (200, 201), r.text
    connector_id = r.json()["id"]

    async def _assert_clean(payload: dict) -> None:
        blob = str(payload)
        for key, value in secrets.items():
            assert key not in payload["auth_state"], key
            assert value not in blob, value
        assert payload["auth_state"]["catalog_slug"] == "quickbooks_mcp"

    await _assert_clean(r.json())

    got = await authenticated_client.get(f"/api/agentive/connectors/{connector_id}")
    assert got.status_code == 200, got.text
    await _assert_clean(got.json())

    listed = await authenticated_client.get("/api/agentive/connectors")
    assert listed.status_code == 200, listed.text
    rows = [c for c in listed.json()["connectors"] if c["id"] == connector_id]
    assert rows
    await _assert_clean(rows[0])

    # PATCH is a lifecycle mutation and now requires workspace authority; a
    # workspace-less row is refused outright. Mount this one in the caller's
    # own workspace so the assertion under test is redaction, not the gate.
    from app.agentive.nodes import Connector

    node = await Connector.get(connector_id)
    ws_resp = await authenticated_client.get("/api/workspaces")
    node.workspace_id = ws_resp.json()["workspaces"][0]["id"]
    await node.save()

    patched = await authenticated_client.patch(
        f"/api/agentive/connectors/{connector_id}",
        json={"sync_interval_seconds": 600},
    )
    assert patched.status_code == 200, patched.text
    await _assert_clean(patched.json())


# ---------------------------------------------------------------------------
# F-7 / #17 — encryption at rest + token-store cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_auth_state_secrets_encrypted_at_rest(
    authenticated_client: AsyncClient, test_user, enc_key
):
    """MCP ``auth_state`` credential values are AES-GCM ciphertext on the node."""
    from app.agentive.nodes import Connector
    from app.agentive.services.connector_registry_node import decrypt_auth_state

    r = await authenticated_client.post(
        "/api/agentive/connectors",
        json={
            "kind": "mcp",
            "auth_state": {
                "url": "https://mcp.example/mcp",
                "transport": "streamable_http",
                "client_secret": "plaintext-secret",
                "oauth": {
                    "status": "authorized",
                    "tokens": {"access_token": "plaintext-access"},
                },
            },
        },
    )
    assert r.status_code in (200, 201), r.text
    node = await Connector.get(r.json()["id"])
    assert node is not None

    stored = node.auth_state
    assert stored["client_secret"].startswith("v1:")
    assert stored["oauth"]["tokens"]["access_token"].startswith("v1:")
    # Non-secret fields stay readable — this is not blanket blob encryption.
    assert stored["url"] == "https://mcp.example/mcp"

    plain = decrypt_auth_state(stored)
    assert plain["client_secret"] == "plaintext-secret"
    assert plain["oauth"]["tokens"]["access_token"] == "plaintext-access"


def test_encrypt_auth_state_is_idempotent(enc_key):
    """Re-saving a dict that already holds ciphertext must not double-encrypt."""
    from app.agentive.services.connector_registry_node import (
        decrypt_auth_state,
        encrypt_auth_state,
    )

    once = encrypt_auth_state({"client_secret": "s"})
    twice = encrypt_auth_state(dict(once))
    assert decrypt_auth_state(twice)["client_secret"] == "s"


def test_token_store_delete_refuses_paths_outside_the_store(tmp_path):
    """The path comes from auth_state — an unguarded unlink would be a weapon."""
    from app.connectors.stdio_env import delete_token_store_file

    outside = tmp_path / "victim.env"
    outside.write_text("keep me", encoding="utf-8")
    assert delete_token_store_file(str(outside)) is False
    assert outside.exists()
    assert delete_token_store_file("") is False


@pytest.mark.asyncio
async def test_deleting_mcp_connector_removes_the_stdio_token_file(
    authenticated_client: AsyncClient,
):
    """#17 — the token store held a live client secret and was never deleted."""
    from app.connectors.stdio_env import TOKEN_STORE_ROOT, write_dotenv_file
    from app.services.hooks.registry import clear_workspace_registrations

    workspace_id = await _workspace_id(authenticated_client)
    clear_workspace_registrations(workspace_id)
    token_file = write_dotenv_file(
        TOKEN_STORE_ROOT / "pytest-purge" / "token.env",
        {"QUICKBOOKS_REFRESH_TOKEN": "live-refresh-token"},
    )
    assert token_file.exists()

    mounted = await authenticated_client.post(
        "/api/agentive/connectors/mcp/mount",
        json=_stdio_mount_body(
            {"QUICKBOOKS_TOKEN_STORE_PATH": str(token_file)}, "purge-test"
        ),
        headers={"X-Integral-Scope": f"ws:{workspace_id}"},
    )
    assert mounted.status_code in (200, 201), mounted.text
    connector_id = mounted.json()["connector"]["id"]

    deleted = await authenticated_client.delete(
        f"/api/agentive/connectors/{connector_id}"
    )
    assert deleted.status_code in (200, 204), deleted.text
    assert not token_file.exists()
    clear_workspace_registrations(workspace_id)


# ---------------------------------------------------------------------------
# F-9 — lifecycle routes re-check workspace authority
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_lifecycle_routes_require_workspace_authority(
    authenticated_client: AsyncClient, second_user_client: AsyncClient
):
    """Owning the connector is not enough once you leave its workspace."""
    from app.agentive.nodes import Connector
    from app.services.hooks.registry import clear_workspace_registrations

    workspace_id = await _workspace_id(authenticated_client)
    other_workspace_id = await _workspace_id(second_user_client)
    assert other_workspace_id != workspace_id
    clear_workspace_registrations(workspace_id)

    mounted = await authenticated_client.post(
        "/api/agentive/connectors/mcp/mount",
        json=_stdio_mount_body({"MCP_SECRET": "s"}, "authority-test"),
        headers={"X-Integral-Scope": f"ws:{workspace_id}"},
    )
    assert mounted.status_code in (200, 201), mounted.text
    connector_id = mounted.json()["connector"]["id"]

    # Owner-still-owner, but the connector now lives in a workspace they are
    # not a member of — exactly the shape of "removed from the org".
    node = await Connector.get(connector_id)
    assert node is not None
    node.workspace_id = other_workspace_id
    await node.save()

    refreshed = await authenticated_client.post(
        f"/api/agentive/connectors/{connector_id}/mcp/refresh"
    )
    assert refreshed.status_code == 403, refreshed.text

    health = await authenticated_client.get(
        f"/api/agentive/connectors/{connector_id}/health"
    )
    assert health.status_code == 403, health.text

    removed = await authenticated_client.delete(
        f"/api/agentive/connectors/{connector_id}"
    )
    assert removed.status_code == 403, removed.text

    # Restore authority and clean up.
    node = await Connector.get(connector_id)
    node.workspace_id = workspace_id
    await node.save()
    await authenticated_client.delete(f"/api/agentive/connectors/{connector_id}")
    clear_workspace_registrations(workspace_id)


# ---------------------------------------------------------------------------
# F-12 — Workspace —HAS_CONNECTOR→ Connector (I-GRAPH-01)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mounted_connector_is_reachable_from_its_workspace(
    authenticated_client: AsyncClient,
):
    """ADR-009 §4 requires the structural edge, not just the scalar cache."""
    from app.models.nodes import Workspace
    from app.services.hooks.registry import clear_workspace_registrations

    workspace_id = await _workspace_id(authenticated_client)
    clear_workspace_registrations(workspace_id)
    mounted = await authenticated_client.post(
        "/api/agentive/connectors/mcp/mount",
        json=_stdio_mount_body({"MCP_SECRET": "s"}, "graph-test"),
        headers={"X-Integral-Scope": f"ws:{workspace_id}"},
    )
    assert mounted.status_code in (200, 201), mounted.text
    connector_id = mounted.json()["connector"]["id"]

    workspace = await Workspace.get(workspace_id)
    assert workspace is not None
    reachable = await workspace.nodes(
        edge=["HasConnector"], direction="out", node=["Connector"]
    )
    assert connector_id in [c.id for c in (reachable or [])]

    await authenticated_client.delete(f"/api/agentive/connectors/{connector_id}")
    clear_workspace_registrations(workspace_id)


@pytest.mark.asyncio
async def test_create_connector_refuses_to_persist_a_detached_node():
    """An unresolvable owner used to log a warning and return a floating node."""
    from app.agentive.nodes import Connector
    from app.agentive.services.connector_registry_node import create_connector

    before = len(await Connector.find({"owner": "n.User.does-not-exist"}))
    with pytest.raises(ValueError, match="owner User node not found"):
        await create_connector(owner="n.User.does-not-exist", kind="mcp")
    after = await Connector.find({"owner": "n.User.does-not-exist"})
    assert len(after) == before


@pytest.mark.asyncio
async def test_owns_edge_is_wired_for_a_created_connector(
    authenticated_client: AsyncClient, test_user
):
    """The OWNS edge resolved the wrong id shape and silently never existed."""
    from app.services.permissions import get_user_node

    r = await authenticated_client.post(
        "/api/agentive/connectors",
        json={"kind": "jvagent", "auth_state": {}},
    )
    assert r.status_code in (200, 201), r.text
    connector_id = r.json()["id"]
    owner_id = r.json()["owner"]

    user = await get_user_node(owner_id)
    assert user is not None
    owned = await user.nodes(edge=["Owns"], direction="out", node=["Connector"])
    assert connector_id in [c.id for c in (owned or [])]
