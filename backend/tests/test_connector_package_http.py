"""Drop a package folder, then add, probe, read, stage a write, and remove it."""

from pathlib import Path

import pytest

from app.agentive.services import connector_packages


@pytest.fixture(autouse=True)
def connector_storage_key(monkeypatch):
    """Exercise package secrets with an isolated storage key."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "ab" * 32)


_VALID = """
schema: integral.connector/v1
id: acme_crm
version: 1.0.0
publisher: acme
name: Acme CRM
description: Look up contacts.
trust: { tier: declarative }
binding:
  scopes: [workspace]
configuration:
  - { key: base_url, label: URL, type: url, required: true }
  - { key: api_key, label: Key, type: secret, required: true }
  - { key: credential, label: Private value, type: secret }
auth:
  type: api_key
  secret: api_key
  inject: { in: header, name: Authorization, prefix: "Bearer " }
http:
  base_url: "{{config.base_url}}"
capabilities:
  actions:
    - name: find_contact
      description: Find contacts.
      effect: read
      input:
        type: object
        properties: { query: { type: string } }
      request: { method: GET, path: /contacts, query: { q: "{{input.query}}" } }
      output: { select: "$.items[*]" }
    - name: create_contact
      description: Create a contact.
      effect: write
      input:
        type: object
        properties: { name: { type: string } }
      request: { method: POST, path: /contacts, body: { name: "{{input.name}}" } }
health:
  request: { method: GET, path: /ping }
"""


@pytest.mark.smoke
@pytest.mark.asyncio
@pytest.mark.parametrize("fresh_project", [True, False])
async def test_dropped_package_installs_disabled_then_runs_after_a_probe(
    authenticated_client, tmp_path: Path, monkeypatch, fresh_project: bool
):
    """A scaffolded connector gets the same managed lifecycle outside Core."""
    from app import local_runtime
    from app.cli import main

    project = tmp_path / "project"
    apps = project / "integral-apps"
    if fresh_project:
        assert main(["init", str(project)]) == 0
        assert (project / "integral-connectors" / ".gitkeep").is_file()
    else:
        apps.mkdir(parents=True)
        (project / "README.md").write_text("Existing project")
    monkeypatch.chdir(project)
    assert main(["connectors", "new", "--kind", "actions", "acme_crm"]) == 0
    root = project / "integral-connectors"
    package = root / "acme_crm"
    (package / "connector.yaml").write_text(_VALID)
    assert main(["connectors", "validate", str(package)]) == 0
    assert main(["connectors", "new", "--kind", "actions", "acme_crm"]) == 1
    assert (package / "connector.yaml").read_text() == _VALID
    if not fresh_project:
        assert (project / "README.md").read_text() == "Existing project"
    broken = root / "broken"
    broken.mkdir()
    (broken / "connector.yaml").write_text("id: broken\n")
    remote = root / "remote_acme"
    remote.mkdir()
    (remote / "connector.yaml").write_text(
        _VALID.replace("id: acme_crm", "id: remote_acme").replace(
            "trust: { tier: declarative }",
            "trust: { tier: remote, remote: {protocol: integral.connector.remote/v1, base_url: 'https://remote.example'} }",
        )
    )
    executable = root / "executable_acme"
    (executable / "adapter").mkdir(parents=True)
    (executable / "connector.yaml").write_text(
        _VALID.replace("id: acme_crm", "id: executable_acme")
    )
    marker = tmp_path / "executed"
    (executable / "adapter" / "runner.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    )
    home = tmp_path / "installation"
    config = local_runtime.initialize(home)
    config["apps_path"] = str(apps)
    runtime_env = local_runtime._environment(
        home,
        config,
        "postgresql://unused",
        4000,
        9006,
        str(apps) if fresh_project else None,
    )
    monkeypatch.chdir(home)
    monkeypatch.setenv(
        "INTEGRAL_CONNECTOR_PATHS", runtime_env["INTEGRAL_CONNECTOR_PATHS"]
    )

    calls = []
    probe_ok = False

    async def fake_request(
        manifest,
        request,
        config,
        secrets,
        inputs=None,
        client=None,
        *,
        ok_statuses=None,
    ):
        calls.append((request["path"], secrets.get("api_key"), inputs))
        if request["path"] == "/ping":
            if not probe_ok:
                raise ValueError("unhealthy")
            return {"ok": True}
        return {"items": [{"id": "1", "name": (inputs or {}).get("query")}]}

    monkeypatch.setattr(connector_packages, "perform_request", fake_request)

    created = await authenticated_client.post(
        "/api/workspaces", json={"name": "Drop-in connector workspace"}
    )
    assert created.status_code in {200, 201}, created.text
    workspace = created.json()["workspace"]["id"]
    headers = {"X-Integral-Scope": f"ws:{workspace}"}
    params = {"workspace_id": workspace}

    scanned = await authenticated_client.post(
        "/api/agentive/connector-packages/rescan",
        headers=headers,
        params=params,
        json={},
    )
    assert scanned.status_code == 200, scanned.text
    body = scanned.json()
    ids = {row["id"] for row in body["packages"]}
    assert "acme_crm" in ids and "gmail" in ids and "notion" in ids
    assert any(row["id"] == "broken" for row in body["rejected"])
    assert body["installations"] == []

    for rejected in ("remote_acme", "executable_acme"):
        assert rejected not in ids
        assert any(row["id"] == rejected and row["reason"] for row in body["rejected"])
        refused = await authenticated_client.post(
            f"/api/agentive/connector-packages/{rejected}/install",
            headers=headers,
            json={"workspace_id": workspace, "values": {}},
        )
        assert refused.status_code == 404, refused.text
    assert not marker.exists()
    assert calls == []

    added = await authenticated_client.post(
        "/api/agentive/connector-packages/acme_crm/install",
        headers=headers,
        json={
            "workspace_id": workspace,
            "values": {
                "base_url": "https://acme.example",
                "api_key": "secret-value",
                "credential": "custom-field-secret-value",
            },
        },
    )
    assert added.status_code == 200, added.text
    install = added.json()
    assert install["enabled"] is False
    assert "secret-value" not in added.text
    install_id = install["id"]
    stored = await connector_packages.Connector.get(install_id)
    assert "custom-field-secret-value" not in str(stored.auth_state)
    assert connector_packages.decrypt_auth_state(stored.auth_state)["credential"] == (
        "custom-field-secret-value"
    )

    failed = await authenticated_client.put(
        f"/api/agentive/connector-packages/installations/{install_id}/enabled",
        headers=headers,
        params=params,
        json={"enabled": True},
    )
    assert failed.status_code == 503, failed.text
    assert not (await connector_packages.Connector.get(install_id)).enabled
    probe_ok = True
    calls.clear()

    enabled = await authenticated_client.put(
        f"/api/agentive/connector-packages/installations/{install_id}/enabled",
        headers=headers,
        params=params,
        json={"enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["enabled"] is True
    assert calls == [("/ping", "secret-value", None)]

    read = await authenticated_client.post(
        f"/api/agentive/connector-packages/installations/{install_id}/actions/find_contact",
        headers=headers,
        params=params,
        json={"input": {"query": "Ada"}},
    )
    assert read.status_code == 200, read.text
    assert read.json()["output"] == [{"id": "1", "name": "Ada"}]

    staged = await authenticated_client.post(
        f"/api/agentive/connector-packages/installations/{install_id}/actions/create_contact",
        headers=headers,
        params=params,
        json={"input": {"name": "Ada"}},
    )
    assert staged.status_code == 200, staged.text
    assert staged.json()["status"] == "review"
    assert len(calls) == 2

    approved = await authenticated_client.post(
        f"/api/agentive/connector-packages/installations/{install_id}/reviews/{staged.json()['review_id']}",
        headers=headers,
        params=params,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "ok"
    assert calls[-1][0] == "/contacts"

    disabled = await authenticated_client.put(
        f"/api/agentive/connector-packages/installations/{install_id}/enabled",
        headers=headers,
        params=params,
        json={"enabled": False},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False

    calls.clear()
    reenabled = await authenticated_client.put(
        f"/api/agentive/connector-packages/installations/{install_id}/enabled",
        headers=headers,
        params=params,
        json={"enabled": True},
    )
    assert reenabled.status_code == 200 and reenabled.json()["enabled"] is True
    assert calls == [("/ping", "secret-value", None)]

    removed = await authenticated_client.delete(
        f"/api/agentive/connector-packages/installations/{install_id}",
        headers=headers,
        params=params,
    )
    assert removed.status_code == 200, removed.text


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_package_management_refuses_a_different_workspace(
    authenticated_client, tmp_path: Path, monkeypatch
):
    """An installation can be changed only from the workspace that owns it."""
    package = tmp_path / "acme_crm"
    package.mkdir()
    (package / "connector.yaml").write_text(_VALID)
    monkeypatch.setenv("INTEGRAL_CONNECTOR_PATHS", str(tmp_path))

    async def create_workspace(name: str) -> str:
        created = await authenticated_client.post(
            "/api/workspaces", json={"name": name}
        )
        assert created.status_code in {200, 201}, created.text
        return created.json()["workspace"]["id"]

    workspace = await create_workspace("Owning connector workspace")
    other = await create_workspace("Other connector workspace")
    headers = {"X-Integral-Scope": f"ws:{workspace}"}
    scanned = await authenticated_client.post(
        "/api/agentive/connector-packages/rescan",
        headers=headers,
        params={"workspace_id": workspace},
        json={},
    )
    assert scanned.status_code == 200, scanned.text
    added = await authenticated_client.post(
        "/api/agentive/connector-packages/acme_crm/install",
        headers=headers,
        json={
            "workspace_id": workspace,
            "values": {"base_url": "https://acme.example", "api_key": "secret-value"},
        },
    )
    assert added.status_code == 200, added.text
    refused = await authenticated_client.put(
        f"/api/agentive/connector-packages/installations/{added.json()['id']}/enabled",
        headers={"X-Integral-Scope": f"ws:{other}"},
        params={"workspace_id": other},
        json={"enabled": True},
    )
    assert refused.status_code == 404, refused.text


_MCP = """
schema: integral.connector/v1
id: desk_mcp
version: 1.0.0
publisher: acme
name: Desk
description: Desk tools.
trust: { tier: declarative }
binding:
  scopes: [workspace]
auth: { type: none }
capabilities:
  mcp:
    transport: streamable_http
    url: https://mcp.example.test/mcp
    read_only_tools: [search]
health:
  request: { method: GET, path: /ping }
http:
  base_url: https://mcp.example.test
"""


@pytest.mark.asyncio
async def test_uncertified_mcp_tool_waits_for_review(
    authenticated_client, tmp_path: Path, monkeypatch
):
    """A tool omitted from read_only_tools does not call the MCP server."""
    folder = tmp_path / "desk_mcp"
    folder.mkdir()
    (folder / "connector.yaml").write_text(_MCP)
    monkeypatch.setenv("INTEGRAL_CONNECTOR_PATHS", str(tmp_path))
    calls = []

    async def fake_request(
        manifest,
        request,
        config,
        secrets,
        inputs=None,
        client=None,
        *,
        ok_statuses=None,
    ):
        return {"ok": True}

    async def fake_probe(auth_state, config, manifest):
        assert manifest["capabilities"]["mcp"]["transport"] == "streamable_http"
        return {"ok": True}

    async def fake_mcp(auth_state, tool_name, arguments, config=None, manifest=None):
        calls.append(tool_name)
        return {"echo": tool_name}

    monkeypatch.setattr(connector_packages, "perform_request", fake_request)
    monkeypatch.setattr(connector_packages, "call_mcp_tool", fake_mcp)
    monkeypatch.setattr(
        "app.agentive.connectors.mcp_client.probe_mcp_health", fake_probe
    )
    created = await authenticated_client.post(
        "/api/workspaces", json={"name": "MCP package workspace"}
    )
    workspace = created.json()["workspace"]["id"]
    headers = {"X-Integral-Scope": f"ws:{workspace}"}
    params = {"workspace_id": workspace}
    await authenticated_client.post(
        "/api/agentive/connector-packages/rescan",
        headers=headers,
        params=params,
        json={},
    )
    added = await authenticated_client.post(
        "/api/agentive/connector-packages/desk_mcp/install",
        headers=headers,
        json={"workspace_id": workspace, "values": {}},
    )
    assert added.status_code == 200, added.text
    install_id = added.json()["id"]
    enabled = await authenticated_client.put(
        f"/api/agentive/connector-packages/installations/{install_id}/enabled",
        headers=headers,
        params=params,
        json={"enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    read = await authenticated_client.post(
        f"/api/agentive/connector-packages/installations/{install_id}/mcp/search",
        headers=headers,
        params=params,
        json={"input": {"q": "notes"}},
    )
    assert read.status_code == 200, read.text
    assert read.json()["status"] == "ok"
    assert calls == ["search"]
    staged = await authenticated_client.post(
        f"/api/agentive/connector-packages/installations/{install_id}/mcp/update_page",
        headers=headers,
        params=params,
        json={"input": {"id": "1"}},
    )
    assert staged.json()["status"] == "review"
    assert calls == ["search"]


_HOOK = """
schema: integral.connector/v1
id: desk_hook
version: 1.0.0
publisher: acme
name: Desk hook
description: Signed inbound messages.
trust: { tier: declarative }
binding:
  scopes: [workspace]
configuration:
  - { key: hook_secret, label: Secret, type: secret, required: true }
auth:
  type: api_key
  secret: hook_secret
  inject: { in: header, name: X-Unused }
http:
  base_url: https://hooks.example.test
capabilities:
  channel:
    mode: webhook
    ingress:
      verify: { scheme: hmac_sha256, header: X-Signature, secret: hook_secret }
      event_id: $.id
      sender: $.from
      text: $.text
    send:
      request: { method: POST, path: /send }
health:
  request: { method: GET, path: /ping }
"""


@pytest.mark.asyncio
async def test_webhook_rejects_a_bad_signature_and_a_replay(
    authenticated_client, tmp_path: Path, monkeypatch
):
    """A channel webhook is accepted only with a matching HMAC, and only once."""
    import hashlib
    import hmac

    folder = tmp_path / "desk_hook"
    folder.mkdir()
    (folder / "connector.yaml").write_text(_HOOK)
    monkeypatch.setenv("INTEGRAL_CONNECTOR_PATHS", str(tmp_path))

    async def fake_request(
        manifest,
        request,
        config,
        secrets,
        inputs=None,
        client=None,
        *,
        ok_statuses=None,
    ):
        return {"ok": True}

    monkeypatch.setattr(connector_packages, "perform_request", fake_request)
    created = await authenticated_client.post(
        "/api/workspaces", json={"name": "Webhook package workspace"}
    )
    workspace = created.json()["workspace"]["id"]
    headers = {"X-Integral-Scope": f"ws:{workspace}"}
    params = {"workspace_id": workspace}
    await authenticated_client.post(
        "/api/agentive/connector-packages/rescan",
        headers=headers,
        params=params,
        json={},
    )
    added = await authenticated_client.post(
        "/api/agentive/connector-packages/desk_hook/install",
        headers=headers,
        json={"workspace_id": workspace, "values": {"hook_secret": "hook-secret"}},
    )
    assert added.status_code == 200, added.text
    install_id = added.json()["id"]
    enabled = await authenticated_client.put(
        f"/api/agentive/connector-packages/installations/{install_id}/enabled",
        headers=headers,
        params=params,
        json={"enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    raw = b'{"id":"evt-1","from":"ada","text":"hello"}'
    digest = hmac.new(b"hook-secret", raw, hashlib.sha256).hexdigest()
    bad = await authenticated_client.post(
        f"/api/agentive/connector-packages/installations/{install_id}/webhook",
        content=raw,
        headers={"X-Signature": "sha256=00"},
    )
    assert bad.status_code == 400
    ok = await authenticated_client.post(
        f"/api/agentive/connector-packages/installations/{install_id}/webhook",
        content=raw,
        headers={"X-Signature": f"sha256={digest}"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["accepted"] is True
    assert ok.json()["linked"] is False
    replay = await authenticated_client.post(
        f"/api/agentive/connector-packages/installations/{install_id}/webhook",
        content=raw,
        headers={"X-Signature": f"sha256={digest}"},
    )
    assert replay.status_code == 409


@pytest.mark.asyncio
async def test_configure_keeps_a_blank_secret_and_bumps_revision(
    authenticated_client, tmp_path: Path, monkeypatch
):
    """An omitted secret stays stored. JSON null removes it from the next call."""
    package = tmp_path / "acme_crm"
    package.mkdir()
    (package / "connector.yaml").write_text(_VALID)
    monkeypatch.setenv("INTEGRAL_CONNECTOR_PATHS", str(tmp_path))
    seen = []

    async def fake_request(
        manifest,
        request,
        config,
        secrets,
        inputs=None,
        client=None,
        *,
        ok_statuses=None,
    ):
        seen.append((config.get("base_url"), secrets.get("api_key")))
        return {"ok": True, "items": []}

    monkeypatch.setattr(connector_packages, "perform_request", fake_request)
    created = await authenticated_client.post(
        "/api/workspaces", json={"name": "Configure package workspace"}
    )
    workspace = created.json()["workspace"]["id"]
    headers = {"X-Integral-Scope": f"ws:{workspace}"}
    params = {"workspace_id": workspace}
    await authenticated_client.post(
        "/api/agentive/connector-packages/rescan",
        headers=headers,
        params=params,
        json={},
    )
    added = await authenticated_client.post(
        "/api/agentive/connector-packages/acme_crm/install",
        headers=headers,
        json={
            "workspace_id": workspace,
            "values": {"base_url": "https://crm.example.test", "api_key": "stored-key"},
        },
    )
    assert added.status_code == 200, added.text
    install_id = added.json()["id"]
    saved = await authenticated_client.put(
        f"/api/agentive/connector-packages/installations/{install_id}",
        headers=headers,
        json={
            "workspace_id": workspace,
            "values": {"base_url": "https://crm.example.test/v2"},
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["revision"] == 1
    assert body["enabled"] is False
    assert body["config"] == {"base_url": "https://crm.example.test/v2"}
    assert "api_key" not in body["config"]
    enabled = await authenticated_client.put(
        f"/api/agentive/connector-packages/installations/{install_id}/enabled",
        headers=headers,
        params=params,
        json={"enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    assert seen[-1] == ("https://crm.example.test/v2", "stored-key")
    revoked = await authenticated_client.put(
        f"/api/agentive/connector-packages/installations/{install_id}",
        headers=headers,
        json={
            "workspace_id": workspace,
            "values": {"base_url": "https://crm.example.test/v2", "api_key": None},
        },
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["revision"] == 2
    assert "stored-key" not in revoked.text
    enabled_again = await authenticated_client.put(
        f"/api/agentive/connector-packages/installations/{install_id}/enabled",
        headers=headers,
        params=params,
        json={"enabled": True},
    )
    assert enabled_again.status_code == 200, enabled_again.text
    assert seen[-1] == ("https://crm.example.test/v2", None)


@pytest.mark.asyncio
async def test_package_oauth_start_uses_the_gmail_package(
    authenticated_client, monkeypatch
):
    """Settings OAuth start uses the package route and the Gmail manifest."""
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GMAIL_OAUTH_REDIRECT_URI", "https://app.example/cb")
    workspace = (
        await authenticated_client.post("/api/workspaces", json={"name": "OAuth Scope"})
    ).json()["workspace"]["id"]
    headers = {"X-Integral-Scope": f"ws:{workspace}"}
    unbound = await authenticated_client.post(
        "/api/agentive/connector-packages/gmail/oauth/start", json={}
    )
    assert unbound.status_code == 400, unbound.text
    added = await authenticated_client.post(
        "/api/agentive/connector-packages/gmail/install",
        headers=headers,
        json={"workspace_id": workspace, "binding_scope": "workspace", "values": {}},
    )
    assert added.status_code == 200, added.text
    response = await authenticated_client.post(
        "/api/agentive/connector-packages/gmail/oauth/start",
        json={"connector_id": added.json()["id"]},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["consent_url"].startswith("https://accounts.google.com/")
    assert "access_type=offline" in body["consent_url"]
    assert body["state"]
    missing = await authenticated_client.post(
        "/api/agentive/connector-packages/not_a_package/oauth/start",
        json={},
    )
    assert missing.status_code == 400, missing.text
    invalid = await authenticated_client.post(
        "/api/agentive/connector-packages/Bad_Id/oauth/start",
        json={},
    )
    assert invalid.status_code == 400, invalid.text
    created = await authenticated_client.post(
        "/api/agentive/connectors",
        json={"kind": "custom"},
    )
    assert created.status_code in (200, 201), created.text
    mismatch = await authenticated_client.post(
        "/api/agentive/connector-packages/gmail/oauth/start",
        json={"connector_id": created.json()["id"]},
    )
    assert mismatch.status_code == 400, mismatch.text


@pytest.mark.asyncio
async def test_reauth_refuses_when_the_package_is_not_admitted(
    authenticated_client, monkeypatch
):
    """Dedicated re-authorize routes do not fall back to a hardcoded provider URL."""
    monkeypatch.setattr(
        "app.connectors.package_runtime.consent_url_for_package",
        lambda *_args, **_kwargs: None,
    )
    gmail = await authenticated_client.post(
        "/api/agentive/connectors/gmail/oauth/start",
        json={},
    )
    quickbooks = await authenticated_client.post(
        "/api/agentive/connectors/quickbooks/authorize",
        json={},
    )
    assert gmail.status_code == 400, gmail.text
    assert quickbooks.status_code == 400, quickbooks.text


@pytest.mark.asyncio
async def test_gmail_labels_refuse_a_connector_from_another_package(
    authenticated_client,
):
    """A non-Gmail token is not sent to the Gmail label URL."""
    created = await authenticated_client.post(
        "/api/agentive/connectors",
        json={"kind": "custom"},
    )
    assert created.status_code in (200, 201), created.text
    connector_id = created.json()["id"]
    listed = await authenticated_client.get(
        f"/api/agentive/connectors/{connector_id}/gmail/labels"
    )
    assert listed.status_code == 400, listed.text
    saved = await authenticated_client.post(
        f"/api/agentive/connectors/{connector_id}/gmail/labels",
        json={"label_ids": ["INBOX"], "consent_acknowledged": True},
    )
    assert saved.status_code == 400, saved.text


@pytest.mark.asyncio
async def test_gmail_callback_does_not_replace_another_package(
    authenticated_client, monkeypatch
):
    """The Gmail callback does not exchange a code onto a different connector."""
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GMAIL_OAUTH_REDIRECT_URI", "https://app.example/cb")

    async def explode(*_args, **_kwargs):
        raise AssertionError("token exchange must not run")

    monkeypatch.setattr("app.services.connectors.gmail_oauth.exchange_code", explode)
    workspace = (
        await authenticated_client.post("/api/workspaces", json={"name": "OAuth Scope"})
    ).json()["workspace"]["id"]
    headers = {"X-Integral-Scope": f"ws:{workspace}"}
    added = await authenticated_client.post(
        "/api/agentive/connector-packages/gmail/install",
        headers=headers,
        json={"workspace_id": workspace, "binding_scope": "workspace"},
    )
    assert added.status_code == 200, added.text
    started = await authenticated_client.post(
        "/api/agentive/connector-packages/gmail/oauth/start",
        json={"connector_id": added.json()["id"]},
        headers=headers,
    )
    assert started.status_code == 200, started.text
    created = await authenticated_client.post(
        "/api/agentive/connectors",
        json={"kind": "custom"},
    )
    assert created.status_code in (200, 201), created.text
    response = await authenticated_client.post(
        "/api/agentive/connectors/gmail/oauth/callback",
        json={
            "code": "auth-code",
            "state": started.json()["state"],
            "connector_id": created.json()["id"],
        },
    )
    assert response.status_code == 400, response.text


@pytest.mark.asyncio
async def test_quickbooks_callback_does_not_replace_another_package(
    authenticated_client, monkeypatch
):
    """The QuickBooks callback does not exchange a code onto a different connector."""
    monkeypatch.setenv("QUICKBOOKS_CLIENT_ID", "cid")
    monkeypatch.setenv("QUICKBOOKS_REDIRECT_URI", "https://app.example/cb")

    async def explode(*_args, **_kwargs):
        raise AssertionError("token exchange must not run")

    monkeypatch.setattr(
        "app.services.connectors.quickbooks_oauth.exchange_code", explode
    )
    workspace = (
        await authenticated_client.post("/api/workspaces", json={"name": "OAuth Scope"})
    ).json()["workspace"]["id"]
    headers = {"X-Integral-Scope": f"ws:{workspace}"}
    added = await authenticated_client.post(
        "/api/agentive/connector-packages/quickbooks/install",
        headers=headers,
        json={"workspace_id": workspace, "binding_scope": "workspace"},
    )
    assert added.status_code == 200, added.text
    started = await authenticated_client.post(
        "/api/agentive/connector-packages/quickbooks/oauth/start",
        json={"connector_id": added.json()["id"]},
        headers=headers,
    )
    assert started.status_code == 200, started.text
    created = await authenticated_client.post(
        "/api/agentive/connectors",
        json={"kind": "custom"},
    )
    assert created.status_code in (200, 201), created.text
    response = await authenticated_client.post(
        "/api/agentive/connectors/quickbooks/callback",
        json={
            "code": "auth-code",
            "state": started.json()["state"],
            "realmId": "realm-1",
            "connector_id": created.json()["id"],
        },
    )
    assert response.status_code == 400, response.text


@pytest.mark.asyncio
async def test_legacy_connector_credential_migration_is_reversible(
    authenticated_client,
):
    """Migrate plaintext on an existing node, retaining its cursor and identity."""
    from app.agentive.services.connector_registry_node import create_connector
    from app.services.connectors.credential_migration import migrate

    created = await authenticated_client.post(
        "/api/workspaces", json={"name": "Connector credential migration"}
    )
    workspace = created.json()["workspace"]["id"]
    from app.services.workspace_permissions import get_workspace_owner_user_id

    owner_id = await get_workspace_owner_user_id(workspace)
    assert owner_id is not None
    connector = await create_connector(
        owner=owner_id,
        kind="custom",
        workspace_id=workspace,
        sync_cursor="keep-this-cursor",
    )
    legacy = {"access_token": "legacy-access", "refresh_token": "legacy-refresh"}
    connector.auth_state = legacy
    await connector.save()

    await migrate(reverse=False, dry_run=True)
    assert (await connector_packages.Connector.get(connector.id)).auth_state == legacy
    await migrate(reverse=False, dry_run=False)
    migrated = await connector_packages.Connector.get(connector.id)
    assert all(value.startswith("v1:") for value in migrated.auth_state.values())
    assert connector_packages.decrypt_auth_state(migrated.auth_state) == legacy
    assert migrated.sync_cursor == "keep-this-cursor"
    await migrate(reverse=True, dry_run=False)
    restored = await connector_packages.Connector.get(connector.id)
    assert restored.auth_state == legacy
    assert restored.sync_cursor == "keep-this-cursor"


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_drop_in_sync_materializes_records_before_advancing_cursor(
    authenticated_client, test_user, tmp_path, monkeypatch
):
    """Use a dropped manifest, real Track binding, and the manual sync endpoint."""
    from app.connectors import package_runtime
    from app.models.edges import CONTAINS, OWNS
    from app.models.nodes import Entry, Track
    from app.services.app_graph import (
        catalog_track,
        ensure_track_attached_operational_model,
    )
    from app.services.personal_workspace import ensure_personal_workspace

    folder = tmp_path / "acme_sync"
    folder.mkdir()
    (folder / "connector.yaml").write_text(
        """
schema: integral.connector/v1
id: acme_sync
version: 1.0.0
publisher: acme
name: Acme sync
description: Import public rows.
trust: {tier: declarative}
binding: {scopes: [personal]}
http: {base_url: https://sync.example}
health: {request: {method: GET, path: /ping}}
capabilities:
  sync:
    entry_type: post
    source:
      request: {method: GET, path: /rows}
      items: "$.items"
      paginate: {next_cursor: "$.next", max_pages: 1}
    map: {external_id: "$.id", title: "$.name"}
"""
    )
    monkeypatch.setenv("INTEGRAL_CONNECTOR_PATHS", str(tmp_path))

    async def upstream(_manifest, request, *_args, **_kwargs):
        if request["path"] == "/ping":
            return {"ok": True}
        return {"items": [{"id": "upstream-a", "name": "Synced row"}], "next": "page-b"}

    monkeypatch.setattr(connector_packages, "perform_request", upstream)
    monkeypatch.setattr(package_runtime, "perform_request", upstream)

    workspace = await ensure_personal_workspace(test_user)
    track = await Track.create(
        name="Drop-in sync target",
        owner_id=test_user.id,
        workspace_id=workspace.id,
    )
    await catalog_track(track)
    await workspace.connect(track, edge=CONTAINS)
    await test_user.connect(track, edge=OWNS)
    await ensure_track_attached_operational_model(track)

    base = "/api/agentive/connector-packages"
    headers = {"X-Integral-Scope": f"ws:{workspace.id}"}
    params = {"workspace_id": workspace.id}
    scanned = await authenticated_client.post(
        f"{base}/rescan", headers=headers, params=params
    )
    assert scanned.status_code == 200, scanned.text
    added = await authenticated_client.post(
        f"{base}/acme_sync/install", headers=headers, json={**params, "values": {}}
    )
    assert added.status_code == 200, added.text
    connector_id = added.json()["id"]
    bound = await authenticated_client.post(
        f"/api/agentive/connectors/{connector_id}/bindings",
        headers=headers,
        json={"track_id": track.id},
    )
    assert bound.status_code in {200, 201}, bound.text
    url = f"{base}/installations/{connector_id}"
    enabled = await authenticated_client.put(
        f"{url}/enabled", headers=headers, params=params, json={"enabled": True}
    )
    assert enabled.status_code == 200, enabled.text
    result = await authenticated_client.post(
        f"{url}/sync", headers=headers, params=params
    )
    assert result.status_code == 200, result.text
    assert result.json()["stats"]["created"] == 1
    row = await Entry.find_one(
        {"context.track_id": track.id, "context.title": "Synced row"}
    )
    assert row is not None
    assert row.provenance.source_id == f"{connector_id}:upstream-a"
    assert (
        await connector_packages.Connector.get(connector_id)
    ).sync_cursor == "page-b"

    disabled = await authenticated_client.put(
        f"{url}/enabled", headers=headers, params=params, json={"enabled": False}
    )
    assert disabled.status_code == 200, disabled.text
    blocked = await authenticated_client.post(
        f"{url}/sync", headers=headers, params=params
    )
    assert blocked.status_code == 400, blocked.text
    removed = await authenticated_client.delete(url, headers=headers, params=params)
    assert removed.status_code == 200, removed.text


@pytest.mark.parametrize(
    ("package", "module", "callback", "extra"),
    [
        ("gmail", "gmail_oauth", "gmail/oauth/callback", {}),
        (
            "quickbooks",
            "quickbooks_oauth",
            "quickbooks/callback",
            {"realmId": "realm-1"},
        ),
    ],
)
@pytest.mark.asyncio
async def test_package_oauth_reauthorization_keeps_installation(
    authenticated_client, monkeypatch, package, module, callback, extra
):
    """Add and reauthorize the same disabled package through its callback."""
    from urllib.parse import parse_qs, urlsplit

    from app.models.edges import CONTAINS, IS_CONNECTED_TO
    from app.models.nodes import Track, Workspace
    from app.services.app_graph import catalog_track

    async def exchange(code, *, redirect_uri, client_id, client_secret):
        assert (client_id, client_secret) == ("fixture-id", "fixture-secret")
        tokens = {"access_token": f"token-{code}", "expires_in": 3600}
        if code == "first":
            tokens["refresh_token"] = "fixture-refresh"
        return tokens

    async def probe(manifest, request, config, secrets, *, ok_statuses):
        assert request["method"] == "GET"
        assert secrets["access_token"] == "token-second"
        if package == "quickbooks":
            assert config["realm_id"] == "realm-1"
            assert (
                manifest["http"]["base_url"]
                == "https://sandbox-quickbooks.api.intuit.com"
            )
        return {}

    monkeypatch.setattr(connector_packages, "perform_request", probe)
    monkeypatch.setenv("GMAIL_OAUTH_REDIRECT_URI", "https://app.example/gmail")
    monkeypatch.setenv("QUICKBOOKS_REDIRECT_URI", "https://app.example/quickbooks")
    monkeypatch.setattr(f"app.services.connectors.{module}.exchange_code", exchange)
    created = await authenticated_client.post(
        "/api/workspaces", json={"name": f"{package} OAuth acceptance"}
    )
    workspace = created.json()["workspace"]["id"]
    headers = {"X-Integral-Scope": f"ws:{workspace}"}
    base = f"/api/agentive/connector-packages/{package}"
    values = {"client_id": "fixture-id", "client_secret": "fixture-secret"}
    if package == "quickbooks":
        added = await authenticated_client.post(
            "/api/agentive/connectors/catalog/quickbooks/install",
            headers=headers,
            json={
                "connection_mode": "shared",
                "secrets": {**values, "environment": "sandbox"},
            },
        )
        assert added.status_code == 200, added.text
        installation = added.json()["connector"]
    else:
        added = await authenticated_client.post(
            f"{base}/install",
            headers=headers,
            json={
                "workspace_id": workspace,
                "values": values,
                "binding_scope": "workspace",
            },
        )
        assert added.status_code == 200, added.text
        installation = added.json()
    connector_id = installation["id"]
    assert not installation["enabled"]
    assert "fixture-secret" not in added.text
    row = await connector_packages.Connector.get(connector_id)
    assert row.mapping_profile == "package"
    row.sync_cursor = "keep-cursor"
    await row.save()
    track = await Track.create(name="OAuth Track", workspace_id=workspace)
    await catalog_track(track)
    await (await Workspace.get(workspace)).connect(track, edge=CONTAINS)
    await row.connect(track, edge=IS_CONNECTED_TO, mapping_profile_yaml="")

    for code in ("first", "second"):
        started = await authenticated_client.post(
            f"{base}/oauth/start",
            headers=headers,
            json={"connector_id": connector_id},
        )
        assert started.status_code == 200, started.text
        query = parse_qs(urlsplit(started.json()["consent_url"]).query)
        assert query["client_id"] == ["fixture-id"]
        completed = await authenticated_client.post(
            f"/api/agentive/connectors/{callback}",
            headers=headers,
            json={"code": code, "state": started.json()["state"], **extra},
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["connector_id"] == connector_id
        assert f"token-{code}" not in completed.text

    row = await connector_packages.Connector.get(connector_id)
    stored = connector_packages.decrypt_auth_state(row.auth_state)
    assert row.sync_cursor == "keep-cursor"
    assert row.workspace_id == workspace
    graph = await row.get_context()
    assert await graph.find_edges_between(row.id, track.id, edge_class=IS_CONNECTED_TO)
    assert not row.enabled
    assert stored["access_token"] == "token-second"
    assert stored["refresh_token"] == "fixture-refresh"

    enabled = await authenticated_client.put(
        f"/api/agentive/connector-packages/installations/{connector_id}/enabled",
        headers=headers,
        params={"workspace_id": workspace},
        json={"enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["enabled"]
    assert (
        await connector_packages.Connector.get(connector_id)
    ).sync_cursor == "keep-cursor"


@pytest.mark.asyncio
async def test_catalog_migration_adopts_and_reverses_existing_rows(
    authenticated_client, test_user, monkeypatch
):
    """Adopt legacy catalog rows in place and reverse metadata with no credential writes."""
    from app.agentive.services.connector_registry_node import create_connector
    from app.models.edges import CONTAINS, IS_CONNECTED_TO
    from app.models.nodes import Track, Workspace
    from app.services.app_graph import catalog_track
    from app.services.connectors.catalog_migration import migrate

    response = await authenticated_client.post(
        "/api/workspaces", json={"name": "Legacy catalog migration"}
    )
    workspace = response.json()["workspace"]["id"]
    track = await Track.create(name="Existing binding", workspace_id=workspace)
    await catalog_track(track)
    await (await Workspace.get(workspace)).connect(track, edge=CONTAINS)
    gmail = await create_connector(
        owner=test_user.id,
        kind="custom",
        workspace_id=workspace,
        auth_state={
            "catalog_slug": "gmail",
            "access_token": "legacy-token",
            "refresh_token": "legacy-refresh",
            "labels": ["INBOX"],
        },
        capabilities=["connector.sync"],
        sync_cursor="before-migration",
        mapping_profile="legacy-mapping",
    )
    gmail.subclass_slug = "gmail"
    gmail.sync_interval_seconds = 777
    await gmail.save()
    await gmail.connect(track, edge=IS_CONNECTED_TO, mapping_profile_yaml="version: 1")
    mcp = await create_connector(
        owner=test_user.id,
        kind="mcp",
        workspace_id=workspace,
        auth_state={"catalog_slug": "notion", "oauth": {"access_token": "mcp-token"}},
        skip_default_policy=True,
    )
    mcp.subclass_slug = "mcp"
    mcp.health_status = "ok"
    await mcp.save()
    credentials = {row.id: dict(row.auth_state) for row in (gmail, mcp)}

    from app.services.connectors.sync_runtime import sync_one_connector

    legacy_options = {"headers": {"X-Integral-Scope": f"ws:{workspace}"}}
    legacy_endpoint = f"/api/agentive/connectors/{gmail.id}/legacy-sync-enabled"
    wrong = await authenticated_client.put(legacy_endpoint, json={"enabled": False})
    assert wrong.status_code == 400, wrong.text
    paused_legacy = await authenticated_client.put(
        legacy_endpoint, **legacy_options, json={"enabled": False}
    )
    assert paused_legacy.status_code == 200, paused_legacy.text
    legacy = await connector_packages.Connector.get(gmail.id)
    assert legacy.legacy_sync_paused
    assert (await sync_one_connector(legacy))["skipped"] == 1

    async def legacy_probe(*args, **kwargs):
        return {}

    monkeypatch.setattr(connector_packages, "perform_request", legacy_probe)
    resumed_legacy = await authenticated_client.put(
        legacy_endpoint, **legacy_options, json={"enabled": True}
    )
    assert resumed_legacy.status_code == 200, resumed_legacy.text
    assert not (await connector_packages.Connector.get(gmail.id)).legacy_sync_paused

    from app.services.personal_workspace import ensure_personal_workspace

    own_personal = await ensure_personal_workspace(test_user)
    personal_headers = {"headers": {"X-Integral-Scope": f"ws:{own_personal.id}"}}
    catalogue_row = await authenticated_client.post(
        "/api/agentive/connectors/catalog/github_issues/install",
        **personal_headers,
        json={
            "connection_mode": "per_user",
            "secrets": {"owner": "example", "repo": "synthetic"},
        },
    )
    assert catalogue_row.status_code in (200, 201), catalogue_row.text
    created = catalogue_row.json()["connector"]
    assert created["legacy_sync_enabled"] is False
    github_enabled = await authenticated_client.put(
        f"/api/agentive/connectors/{created['id']}/legacy-sync-enabled",
        **personal_headers,
        json={"enabled": True},
    )
    assert github_enabled.status_code == 200, github_enabled.text
    assert (await connector_packages.Connector.get(created["id"])).sync_cursor is None
    await (await connector_packages.Connector.get(created["id"])).delete(cascade=False)

    assert await migrate(dry_run=True) == 2
    assert (
        await connector_packages.Connector.get(gmail.id)
    ).mapping_profile == "legacy-mapping"
    assert await migrate() == 2
    assert await migrate() == 0
    headers = {"X-Integral-Scope": f"ws:{workspace}"}
    listed = await authenticated_client.get(
        "/api/agentive/connector-packages",
        headers=headers,
        params={"workspace_id": workspace},
    )
    assert listed.status_code == 200, listed.text
    assert {
        item["id"]: item["package_id"] for item in listed.json()["installations"]
    } == {
        gmail.id: "gmail",
        mcp.id: "notion",
    }
    assert "legacy-token" not in listed.text and "mcp-token" not in listed.text

    async def inspect_rows(mapping):
        for original in (gmail, mcp):
            row = await connector_packages.Connector.get(original.id)
            assert row.auth_state == credentials[original.id]
            assert row.owner == original.owner and row.workspace_id == workspace
            assert row.subclass_slug == original.subclass_slug
            if row.id == gmail.id:
                assert row.mapping_profile == mapping
                assert (
                    row.sync_cursor == "before-migration"
                    and row.sync_interval_seconds == 777
                )
                graph = await row.get_context()
                edges = await graph.find_edges_between(
                    row.id, track.id, edge_class=IS_CONNECTED_TO
                )
                assert len(edges) == 1 and edges[0].mapping_profile_yaml == "version: 1"

    import httpx

    from app.services.connectors.sync_runtime import sync_one_connector

    endpoint = f"/api/agentive/connector-packages/installations/{gmail.id}/enabled"
    options = {"headers": headers, "params": {"workspace_id": workspace}}
    paused = await authenticated_client.put(
        endpoint, **options, json={"enabled": False}
    )
    assert paused.status_code == 200 and paused.json()["enabled"] is False
    assert paused.json()["connection_mode"] == "per_user"
    disabled = await connector_packages.Connector.get(gmail.id)
    assert not disabled.enabled
    assert (await sync_one_connector(disabled))["skipped"] == 1

    async def failed_probe(*args, **kwargs):
        raise httpx.ConnectError("Synthetic health failure")

    async def healthy_probe(*args, **kwargs):
        return {}

    monkeypatch.setattr(connector_packages, "perform_request", failed_probe)
    blocked = await authenticated_client.put(
        endpoint, **options, json={"enabled": True}
    )
    assert blocked.status_code == 503, blocked.text
    assert not (await connector_packages.Connector.get(gmail.id)).enabled
    monkeypatch.setattr(connector_packages, "perform_request", healthy_probe)
    resumed = await authenticated_client.put(
        endpoint, **options, json={"enabled": True}
    )
    assert resumed.status_code == 200 and resumed.json()["enabled"] is True
    assert resumed.json()["id"] == gmail.id

    await inspect_rows("package")
    assert await migrate(reverse=True, dry_run=True) == 2
    assert await migrate(reverse=True) == 2
    assert await migrate(reverse=True) == 0
    await inspect_rows("legacy-mapping")


@pytest.mark.asyncio
async def test_manifest_installation_scope_targets_the_real_personal_workspace(
    authenticated_client,
    second_user_client,
    test_user,
    second_user,
    tmp_path,
    monkeypatch,
):
    from app.services.personal_workspace import ensure_personal_workspace

    shared = (
        await authenticated_client.post("/api/workspaces", json={"name": "Scope QA"})
    ).json()["workspace"]["id"]
    personal = (await ensure_personal_workspace(test_user)).id
    other_personal = (await ensure_personal_workspace(second_user)).id
    for slug, scopes in (
        ("acme_workspace", "workspace"),
        ("acme_personal", "personal"),
        ("acme_both", "workspace, personal"),
    ):
        folder = tmp_path / slug
        folder.mkdir()
        manifest = _VALID.replace("acme_crm", slug).replace(
            "[workspace]", f"[{scopes}]"
        )
        (folder / "connector.yaml").write_text(manifest)
    monkeypatch.setenv("INTEGRAL_CONNECTOR_PATHS", str(tmp_path))
    base = "/api/agentive/connector-packages"

    def headers(workspace):
        return {"X-Integral-Scope": f"ws:{workspace}"}

    async def add(
        slug, workspace, binding=None, client=authenticated_client, header_scope=None
    ):
        body = {
            "workspace_id": workspace,
            "values": {"base_url": "https://scope.example", "api_key": "synthetic"},
        }
        if binding:
            body["binding_scope"] = binding
        return await client.post(
            f"{base}/{slug}/install",
            headers=headers(header_scope or workspace),
            json=body,
        )

    scan = await authenticated_client.post(
        f"{base}/rescan", headers=headers(shared), params={"workspace_id": shared}
    )
    assert scan.status_code == 200, scan.text
    assert {
        row["id"]: row["binding_scopes"]
        for row in scan.json()["packages"]
        if row["id"].startswith("acme_")
    } == {
        "acme_workspace": ["workspace"],
        "acme_personal": ["personal"],
        "acme_both": ["workspace", "personal"],
    }
    assert (await add("acme_both", shared)).status_code == 400
    assert (await add("acme_workspace", personal, "personal")).status_code == 400
    assert (await add("acme_personal", shared, "workspace")).status_code == 400
    assert (await add("acme_both", shared, "personal")).status_code == 400
    assert (await add("acme_both", personal, "workspace")).status_code == 400
    assert (
        await add("acme_both", personal, "personal", client=second_user_client)
    ).status_code == 403
    assert (
        await add("acme_both", personal, "personal", header_scope=shared)
    ).status_code == 400
    for slug, workspace, binding, mode in (
        ("acme_workspace", shared, None, "shared"),
        ("acme_personal", personal, None, "per_user"),
        ("acme_both", shared, "workspace", "shared"),
        ("acme_both", personal, "personal", "per_user"),
    ):
        result = await add(slug, workspace, binding)
        assert result.status_code == 200, result.text
        row = await connector_packages.Connector.get(result.json()["id"])
        assert (
            row.workspace_id == workspace
            and row.connection_mode == mode
            and not row.enabled
        )
    other_list = await second_user_client.get(
        base, headers=headers(other_personal), params={"workspace_id": other_personal}
    )
    assert other_list.status_code == 200 and not other_list.json()["installations"]


@pytest.mark.asyncio
async def test_mcp_oauth_preserves_the_explicit_personal_installation(
    authenticated_client, test_user, monkeypatch
):
    from types import SimpleNamespace

    from app.agentive.connectors import mcp_oauth
    from app.services.personal_workspace import ensure_personal_workspace

    async def probe(url):
        assert url == "https://mcp.notion.com/mcp"
        return SimpleNamespace(needs_oauth=True, response=None)

    async def start(url, **kwargs):
        return {"authorization_endpoint": "https://idp.example.test/authorize"}

    exchanged = []

    async def exchange(oauth, code):
        exchanged.append(code)
        return {"access_token": "synthetic-private-test-token"}

    monkeypatch.setattr(mcp_oauth, "probe_mcp_authorization", probe)
    monkeypatch.setattr(mcp_oauth, "start_mcp_oauth_session", start)
    monkeypatch.setattr(
        mcp_oauth,
        "authorization_url_for_oauth",
        lambda oauth, state: f"{oauth['authorization_endpoint']}?state={state}",
    )
    monkeypatch.setattr(mcp_oauth, "exchange_mcp_oauth_code", exchange)

    personal = (await ensure_personal_workspace(test_user)).id
    headers = {"X-Integral-Scope": f"ws:{personal}"}
    base = "/api/agentive/connector-packages/notion"
    install = await authenticated_client.post(
        f"{base}/install",
        headers=headers,
        json={"workspace_id": personal, "binding_scope": "personal"},
    )
    assert install.status_code == 200, install.text
    connector_id = install.json()["id"]

    request = {"connector_id": connector_id}
    wrong_scope = await authenticated_client.post(f"{base}/oauth/start", json=request)
    assert wrong_scope.status_code == 400, wrong_scope.text
    consent = await authenticated_client.post(
        f"{base}/oauth/start", headers=headers, json=request
    )
    assert consent.status_code == 200, consent.text
    state = consent.json()["consent_url"].split("?state=", 1)[1]
    path = "/api/agentive/connectors/mcp/oauth/callback"
    callback = {"state": state, "code": "synthetic-code"}
    result = await authenticated_client.post(path, headers=headers, json=callback)
    assert result.status_code == 200, result.text
    assert "synthetic-private-test-token" not in result.text
    assert result.json()["id"] == connector_id
    row = await connector_packages.Connector.get(connector_id)
    assert (row.workspace_id, row.connection_mode, row.enabled) == (
        personal,
        "per_user",
        False,
    )
    duplicate = await authenticated_client.post(path, headers=headers, json=callback)
    assert duplicate.status_code == 401, duplicate.text
    assert exchanged == ["synthetic-code"]
