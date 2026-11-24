"""Drop-in connector admission. Invalid packages are recorded and not offered."""

from pathlib import Path

import pytest

from app.connectors.package_discovery import load_package, scan_packages
from app.connectors.package_runtime import pull_declared_sync, render, select_output

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
      request: { method: GET, path: /contacts }
health:
  request: { method: GET, path: /ping }
"""


def test_valid_folder_is_admitted_and_invalid_folder_is_not_offered(tmp_path: Path):
    """A valid declarative package is offered. Broken and trusted packages are not."""
    good = tmp_path / "acme_crm"
    good.mkdir()
    (good / "connector.yaml").write_text(_VALID)
    bad = tmp_path / "broken"
    bad.mkdir()
    (bad / "connector.yaml").write_text("schema: integral.connector/v1\nid: nope\n")
    trusted = tmp_path / "signed_one"
    trusted.mkdir()
    (trusted / "connector.yaml").write_text(
        _VALID.replace("id: acme_crm", "id: signed_one").replace(
            "tier: declarative", "tier: trusted"
        )
    )

    admission = scan_packages([tmp_path])

    assert list(admission.packages) == ["acme_crm"]
    reasons = {row["id"]: row["reason"] for row in admission.rejected}
    assert "broken" in reasons
    assert "signed_one" in reasons
    assert "acme_crm" not in reasons


def test_http_mcp_cannot_declare_generated_env(tmp_path: Path):
    """generated_env belongs to a local process, not an HTTP MCP server."""
    folder = tmp_path / "http_mcp"
    folder.mkdir()
    (folder / "connector.yaml").write_text(
        """
schema: integral.connector/v1
id: http_mcp
version: 1.0.0
publisher: acme
name: HTTP MCP
description: An HTTP server that must not declare process env.
trust:
  tier: declarative
binding:
  scopes: [workspace]
capabilities:
  mcp:
    transport: streamable_http
    url: https://example.com/mcp
    generated_env: [ACME_TOKEN]
""",
        encoding="utf-8",
    )
    _manifest, reason = load_package(folder)
    assert "generated_env" in reason


def test_remote_package_is_rejected_without_importing_code(tmp_path: Path):
    """Marketplace manifests are unavailable and cannot load adapter files."""

    folder = tmp_path / "remote_echo"
    folder.mkdir()
    (folder / "connector.yaml").write_text(
        """
schema: integral.connector/v1
id: remote_echo
version: 1.0.0
publisher: acme
name: Remote echo
description: An out-of-process adapter.
trust:
  tier: remote
  remote:
    protocol: integral.connector.remote/v1
    base_url: https://remote.example
binding:
  scopes: [workspace]
capabilities:
  mcp:
    transport: streamable_http
    url: https://remote.example/mcp
    read_only_tools: []
""",
        encoding="utf-8",
    )
    marker = folder / "loaded.txt"
    (folder / "do_not_import.py").write_text(
        f"open({str(marker)!r}, 'w').write('loaded')\n",
        encoding="utf-8",
    )
    admission = scan_packages([tmp_path])
    assert "remote_echo" not in admission.packages
    assert "trust" in admission.rejected[0]["reason"]
    assert not marker.exists()


@pytest.mark.smoke
def test_user_folder_trusted_python_is_not_imported(tmp_path: Path):
    """A trusted package outside the first-party folder is rejected and not imported."""
    folder = tmp_path / "evil_adapter"
    folder.mkdir()
    (folder / "connector.yaml").write_text(
        "schema: integral.connector/v1\n"
        "id: evil_adapter\n"
        "version: 1.0.0\n"
        "publisher: acme\n"
        "name: Evil\n"
        "description: A trusted package that must not be imported from a user folder.\n"
        "trust: {tier: trusted}\n"
        "binding: {scopes: [workspace]}\n"
        "capabilities:\n"
        "  channel: {mode: managed-text-v1}\n",
        encoding="utf-8",
    )
    marker = folder / "loaded.txt"
    adapter = folder / "adapter"
    adapter.mkdir()
    (adapter / "channel.py").write_text(
        f"open({str(marker)!r}, 'w').write('loaded')\n",
        encoding="utf-8",
    )
    admission = scan_packages([tmp_path])
    manifest, reason = load_package(folder, allow_trusted=True)
    reasons = {row["id"]: row["reason"] for row in admission.rejected}
    assert "declarative" in reasons["evil_adapter"]
    assert "declarative" in reason
    assert manifest is None
    assert "evil_adapter" not in admission.packages
    assert not marker.exists()


@pytest.mark.asyncio
async def test_previous_remote_installation_cannot_run_an_action(monkeypatch):
    """A retired installation snapshot has no effect path."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.services import connector_packages as service
    from app.connectors.package_discovery import PackageAdmission

    manifest = {
        "trust": {
            "tier": "remote",
            "remote": {
                "protocol": "integral.connector.remote/v1",
                "base_url": "https://remote.example",
            },
        },
        "capabilities": {
            "actions": [
                {
                    "name": "ping",
                    "effect": "read",
                    "request": {"method": "GET", "path": "/ping"},
                    "input": {
                        "type": "object",
                        "properties": {"q": {"type": "string"}},
                        "additionalProperties": False,
                    },
                }
            ]
        },
    }
    connector = SimpleNamespace(
        id="c1",
        enabled=True,
        package_revision=2,
        subclass_slug="remote_echo",
        package_manifest=manifest,
        package_config={"api_key": "super-secret"},
        auth_state={},
    )

    async def get(_connector_id):
        return connector

    monkeypatch.setattr(service, "_call_receipt", AsyncMock())

    async def local(*_args, **_kwargs):
        raise AssertionError("remote action ran in Core")

    monkeypatch.setattr(service.Connector, "get", get)
    monkeypatch.setattr(service, "perform_request", local)
    monkeypatch.setattr(
        service,
        "current_admission",
        lambda: PackageAdmission(packages={"remote_echo": manifest}),
    )
    from app.api.errors import ResourceNotFoundError

    with pytest.raises(ResourceNotFoundError, match="tier"):
        await service._execute(
            connector, manifest, {"name": "ping", "effect": "read"}, {"q": "a"}
        )
    service._call_receipt.assert_not_awaited()


def test_rescan_keeps_the_startup_trusted_package(monkeypatch):
    """A trusted package added after startup is not admitted until restart."""
    from app.connectors import package_discovery as discovery

    discovery._current = discovery.PackageAdmission()
    first = discovery.rescan()
    original = discovery.scan_packages

    def scan(roots=None):
        admission = original(roots)
        admission.packages["late_trusted"] = {
            "id": "late_trusted",
            "trust": {"tier": "trusted"},
            "name": "Late",
        }
        return admission

    monkeypatch.setattr(discovery, "scan_packages", scan)
    second = discovery.rescan()
    assert second.packages["gmail"] is first.packages["gmail"]
    assert "late_trusted" not in second.packages
    assert any(
        row["id"] == "late_trusted" and "startup" in row["reason"]
        for row in second.rejected
    )


def test_quickbooks_reauth_consent_uses_the_package():
    """Re-authorize uses the QuickBooks package authorize URL and scope."""
    from urllib.parse import parse_qs, urlparse

    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import admitted_oauth, oauth_consent_url

    rescan()
    auth = admitted_oauth("quickbooks")
    assert auth is not None
    url = oauth_consent_url(
        auth,
        client_id="cid",
        redirect_uri="https://app.example/cb",
        state="signed",
    )
    parsed = urlparse(url)
    assert parsed.netloc == "appcenter.intuit.com"
    assert parse_qs(parsed.query)["scope"] == ["com.intuit.quickbooks.accounting"]
    assert auth["client"]["id_env"] == ["QUICKBOOKS_CLIENT_ID"]
    assert auth["redirect_env"] == ["QUICKBOOKS_REDIRECT_URI"]

    assert auth["redirect_env"] == ["QUICKBOOKS_REDIRECT_URI"]


@pytest.mark.asyncio
async def test_quickbooks_reads_use_the_package_request(monkeypatch):
    """Entity query and change-data go through the shared package request."""
    from app.agentive.connectors.quickbooks import QuickBooksConnector
    from app.connectors.package_discovery import rescan

    rescan()
    seen: dict = {}

    async def fake_perform(
        manifest, request, config, secrets, inputs=None, client=None
    ):
        seen["path"] = request["path"]
        seen["inputs"] = inputs
        seen["config"] = config
        seen["authorization"] = (request.get("headers") or {}).get("Authorization")
        seen["accept"] = (request.get("headers") or {}).get("Accept")
        if request["path"] in {"/books/changes"} or str(request["path"]).endswith(
            "/cdc"
        ):
            return {"CDCResponse": {"QueryResponse": [{"Customer": [{"Id": "1"}]}]}}
        return {"QueryResponse": {"Customer": [{"Id": "9"}]}}

    monkeypatch.setattr("app.connectors.package_runtime.perform_request", fake_perform)
    connector = QuickBooksConnector()
    client = object()
    page = await connector._query_entity(
        client=client,
        host="https://quickbooks.api.intuit.com",
        realm_id="123",
        access_token="tok",
        entity="Customer",
    )
    assert page == [{"Id": "9"}]
    assert seen["path"] == "/v3/company/{{config.realm_id}}/query"
    assert seen["config"]["realm_id"] == "123"
    assert seen["inputs"]["entity"] == "Customer"
    assert seen["inputs"]["start"] == "1"
    assert seen["authorization"] == "Bearer tok"
    assert seen["accept"] == "application/json"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {
                        "method": "GET",
                        "path": "/custom/query",
                        "query": {"q": "{{input.entity}}"},
                        "headers": {"Accept": "application/json"},
                    },
                    "output": {"select": "$.QueryResponse"},
                }
            ]
        }
    }
    await connector._query_entity(
        client=client,
        host="https://sandbox-quickbooks.api.intuit.com",
        realm_id="123",
        access_token="tok",
        entity="Customer",
        manifest=stored,
    )
    assert seen["path"] == "/custom/query"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "change_data",
                    "request": {"method": "GET", "path": "/old"},
                }
            ]
        }
    }
    await connector._query_entity(
        client=client,
        host="https://quickbooks.api.intuit.com",
        realm_id="123",
        access_token="tok",
        entity="Customer",
        manifest=old,
    )
    assert seen["path"] == "/v3/company/{{config.realm_id}}/query"
    foreign = {
        "id": "acme_books",
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/books/query"},
                    "output": {"select": "$.QueryResponse"},
                },
                {
                    "name": "change_data",
                    "request": {"method": "GET", "path": "/books/changes"},
                    "output": {"select": "$.CDCResponse.QueryResponse"},
                },
            ]
        },
    }
    await connector._query_entity(
        client=client,
        host="https://books.example",
        realm_id="123",
        access_token="tok",
        entity="Customer",
        manifest=foreign,
    )
    assert seen["path"] == "/books/query"
    changes = await connector._cdc_changes(
        client=client,
        host="https://books.example",
        realm_id="123",
        access_token="tok",
        changed_since="2024-01-01",
        manifest=foreign,
        entities=["Customer"],
    )
    assert changes["Customer"] == [{"Id": "1"}]
    assert seen["path"] == "/books/changes"
    assert seen["inputs"]["cursor"] == "2024-01-01"
    missing = {"id": "acme_books", "capabilities": {"actions": []}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="does not declare"):
        await connector._query_entity(
            client=client,
            host="https://books.example",
            realm_id="123",
            access_token="tok",
            entity="Customer",
            manifest=missing,
        )


def test_quickbooks_query_path_comes_from_the_package(monkeypatch):
    """Entity query uses the package path and query, snapshot first."""
    from urllib.parse import parse_qs, urlparse

    from app.agentive.connectors.quickbooks import _qbo_query_url
    from app.connectors.package_discovery import rescan

    rescan()
    url = _qbo_query_url(
        "https://quickbooks.api.intuit.com",
        "123",
        "Invoice",
        1,
        100,
        None,
    )
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "quickbooks.api.intuit.com"
    assert parsed.path == "/v3/company/123/query"
    assert parse_qs(parsed.query)["minorversion"] == ["65"]
    old = {"capabilities": {"sync": {"entry_type": "quickbooks"}}}
    old_url = _qbo_query_url("https://stored.example", "9", "Invoice", 1, 100, old)
    assert urlparse(old_url).path == "/v3/company/9/query"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {
                        "method": "GET",
                        "path": "/custom/{{config.realm_id}}/query",
                        "query": {"minorversion": "70"},
                    },
                }
            ]
        }
    }
    stored_url = _qbo_query_url(
        "https://stored.example", "9", "Invoice", 1, 100, stored
    )
    stored_parsed = urlparse(stored_url)
    assert stored_parsed.path == "/custom/9/query"
    assert parse_qs(stored_parsed.query)["minorversion"] == ["70"]
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="does not declare"):
        _qbo_query_url(
            "https://quickbooks.api.intuit.com", "123", "Invoice", 1, 100, None
        )


def test_quickbooks_query_body_comes_from_the_package(monkeypatch):
    """Entity query statement comes from the package template."""
    from urllib.parse import parse_qs, urlparse

    from app.agentive.connectors.quickbooks import _qbo_query_url
    from app.connectors.package_discovery import rescan

    rescan()
    admitted = parse_qs(
        urlparse(
            _qbo_query_url(
                "https://quickbooks.api.intuit.com", "1", "Invoice", 1, 100, None
            )
        ).query
    )
    assert admitted["query"] == ["SELECT * FROM Invoice STARTPOSITION 1 MAXRESULTS 100"]
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {
                        "method": "GET",
                        "path": "/v3/company/{{config.realm_id}}/query",
                        "query": {"minorversion": "65"},
                    },
                }
            ]
        }
    }
    old_query = parse_qs(
        urlparse(
            _qbo_query_url("https://stored.example", "9", "Bill", 2, 25, old)
        ).query
    )
    assert old_query["query"] == ["SELECT * FROM Bill STARTPOSITION 2 MAXRESULTS 25"]
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {
                        "method": "GET",
                        "path": "/custom/{{config.realm_id}}/query",
                        "query": {
                            "statement": (
                                "FIND {{input.entity}} AT {{input.start}} "
                                "SIZE {{input.page_size}}"
                            )
                        },
                    },
                }
            ]
        }
    }
    stored_query = parse_qs(
        urlparse(
            _qbo_query_url("https://stored.example", "9", "Vendor", 3, 10, stored)
        ).query
    )
    assert stored_query["statement"] == ["FIND Vendor AT 3 SIZE 10"]
    assert "query" not in stored_query
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="query body"):
        _qbo_query_url("https://stored.example", "9", "Invoice", 1, 100, old)


def test_quickbooks_query_selector_comes_from_the_package(monkeypatch):
    """Entity rows are selected by the package output path."""
    from app.agentive.connectors.quickbooks import _qbo_query_select
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output

    rescan()
    body = {"QueryResponse": {"Invoice": [{"Id": "1"}]}, "other": {"Invoice": []}}
    selected = select_output(body, _qbo_query_select(None))
    assert selected["Invoice"] == [{"Id": "1"}]
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/v3/company/1/query"},
                }
            ]
        }
    }
    assert _qbo_query_select(old) == "$.QueryResponse"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/custom/1/query"},
                    "output": {"select": "$.data"},
                }
            ]
        }
    }
    assert _qbo_query_select(stored) == "$.data"
    assert select_output({"data": {"Bill": [{"Id": "9"}]}}, "$.data")["Bill"] == [
        {"Id": "9"}
    ]
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="query selector"):
        _qbo_query_select(old)


def test_quickbooks_query_accept_comes_from_the_package(monkeypatch):
    """Entity query uses the package Accept header. A stored action wins."""
    from app.agentive.connectors.quickbooks import _qbo_query_headers
    from app.connectors.package_discovery import rescan

    rescan()
    headers = _qbo_query_headers("tok", None)
    assert headers["Accept"] == "application/json"
    assert headers["Authorization"] == "Bearer tok"
    old = {"capabilities": {"sync": {"entry_type": "quickbooks"}}}
    assert _qbo_query_headers("tok", old)["Accept"] == "application/json"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {
                        "method": "GET",
                        "path": "/custom/query",
                        "headers": {"Accept": "application/xml"},
                    },
                }
            ]
        }
    }
    assert _qbo_query_headers("tok", stored)["Accept"] == "application/xml"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="does not declare"):
        _qbo_query_headers("tok", None)


def test_quickbooks_change_data_path_comes_from_the_package(monkeypatch):
    """Change-data uses the package path and query, snapshot first."""
    from urllib.parse import parse_qs, urlparse

    from app.agentive.connectors.quickbooks import _qbo_change_url
    from app.connectors.package_discovery import rescan

    rescan()
    url = _qbo_change_url(
        "https://quickbooks.api.intuit.com",
        "123",
        "Invoice,Customer",
        "2026-01-01T00:00:00+00:00",
        None,
    )
    parsed = urlparse(url)
    assert parsed.path == "/v3/company/123/cdc"
    query = parse_qs(parsed.query)
    assert query["minorversion"] == ["65"]
    assert query["entities"] == ["Invoice,Customer"]
    assert query["changedSince"] == ["2026-01-01T00:00:00+00:00"]
    old = {"capabilities": {"sync": {"entry_type": "quickbooks"}}}
    old_url = _qbo_change_url("https://stored.example", "9", "Invoice", "t", old)
    assert urlparse(old_url).path == "/v3/company/9/cdc"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "change_data",
                    "request": {
                        "method": "GET",
                        "path": "/custom/{{config.realm_id}}/cdc",
                        "query": {"minorversion": "70"},
                    },
                }
            ]
        }
    }
    stored_url = _qbo_change_url("https://stored.example", "9", "Invoice", "t", stored)
    stored_parsed = urlparse(stored_url)
    assert stored_parsed.path == "/custom/9/cdc"
    assert parse_qs(stored_parsed.query)["minorversion"] == ["70"]
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="does not declare"):
        _qbo_change_url(
            "https://quickbooks.api.intuit.com", "123", "Invoice", "t", None
        )


def test_quickbooks_cursor_field_comes_from_the_package(monkeypatch):
    """Change-data sends the cursor under the package's cursor query key."""
    from urllib.parse import parse_qs, urlparse

    from app.agentive.connectors.quickbooks import _qbo_change_url
    from app.connectors.package_discovery import rescan

    rescan()
    url = _qbo_change_url(
        "https://quickbooks.api.intuit.com", "123", "Invoice", "t1", None
    )
    assert parse_qs(urlparse(url).query)["changedSince"] == ["t1"]
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "change_data",
                    "request": {
                        "method": "GET",
                        "path": "/v3/company/{{config.realm_id}}/cdc",
                        "query": {"minorversion": "65"},
                    },
                }
            ]
        }
    }
    old_query = parse_qs(
        urlparse(
            _qbo_change_url("https://stored.example", "9", "Invoice", "t2", old)
        ).query
    )
    assert old_query["changedSince"] == ["t2"]
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "change_data",
                    "request": {
                        "method": "GET",
                        "path": "/custom/{{config.realm_id}}/cdc",
                        "query": {"updatedAfter": "{{cursor}}"},
                    },
                }
            ]
        }
    }
    stored_query = parse_qs(
        urlparse(
            _qbo_change_url("https://stored.example", "9", "Invoice", "t3", stored)
        ).query
    )
    assert stored_query["updatedAfter"] == ["t3"]
    assert "changedSince" not in stored_query
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="cursor field"):
        _qbo_change_url("https://stored.example", "9", "Invoice", "t4", old)


def test_quickbooks_change_accept_comes_from_the_package(monkeypatch):
    """Change-data uses the package Accept header. A stored action wins."""
    from app.agentive.connectors.quickbooks import _qbo_change_headers
    from app.connectors.package_discovery import rescan

    rescan()
    headers = _qbo_change_headers("tok", None)
    assert headers["Accept"] == "application/json"
    assert headers["Authorization"] == "Bearer tok"
    old = {"capabilities": {"sync": {"entry_type": "quickbooks"}}}
    assert _qbo_change_headers("tok", old)["Accept"] == "application/json"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "change_data",
                    "request": {
                        "method": "GET",
                        "path": "/custom/cdc",
                        "headers": {"Accept": "application/xml"},
                    },
                }
            ]
        }
    }
    assert _qbo_change_headers("tok", stored)["Accept"] == "application/xml"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="does not declare"):
        _qbo_change_headers("tok", None)


def test_quickbooks_change_selector_comes_from_the_package(monkeypatch):
    """Change rows are selected by the package output path."""
    from app.agentive.connectors.quickbooks import (
        _qbo_change_rows,
        _qbo_change_select,
    )
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output

    rescan()
    body = {
        "CDCResponse": [{"QueryResponse": [{"Invoice": [{"Id": "1"}], "Bill": []}]}]
    }
    rows = _qbo_change_rows(
        select_output(body, _qbo_change_select(None)), ["Invoice", "Bill"]
    )
    assert rows["Invoice"] == [{"Id": "1"}]
    assert rows["Bill"] == []
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "change_data",
                    "request": {"method": "GET", "path": "/v3/company/1/cdc"},
                }
            ]
        }
    }
    assert _qbo_change_select(old) == "$.CDCResponse.QueryResponse"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "change_data",
                    "request": {"method": "GET", "path": "/custom/cdc"},
                    "output": {"select": "$.changes"},
                }
            ]
        }
    }
    assert _qbo_change_select(stored) == "$.changes"
    stored_rows = _qbo_change_rows(
        select_output({"changes": [{"Vendor": [{"Id": "9"}]}]}, "$.changes"),
        ["Vendor"],
    )
    assert stored_rows["Vendor"] == [{"Id": "9"}]
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="change selector"):
        _qbo_change_select(old)


def test_quickbooks_record_id_comes_from_the_package(monkeypatch):
    """Each QuickBooks record id is read from the package output path."""
    from app.agentive.connectors.quickbooks import _qbo_record_id
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output

    rescan()
    assert select_output({"Id": "1"}, _qbo_record_id(None, "query_entity")) == "1"
    assert _qbo_record_id(None, "change_data") == "$.Id"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/v3/company/1/query"},
                    "output": {"select": "$.QueryResponse"},
                },
                {
                    "name": "change_data",
                    "request": {"method": "GET", "path": "/v3/company/1/cdc"},
                    "output": {"select": "$.CDCResponse.QueryResponse"},
                },
            ]
        }
    }
    assert _qbo_record_id(old, "query_entity") == "$.Id"
    assert _qbo_record_id(old, "change_data") == "$.Id"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/custom/query"},
                    "output": {"select": "$.data", "id": "$.DocNumber"},
                },
                {
                    "name": "change_data",
                    "request": {"method": "GET", "path": "/custom/cdc"},
                    "output": {"select": "$.changes", "id": "$.SyncToken"},
                },
            ]
        }
    }
    assert _qbo_record_id(stored, "query_entity") == "$.DocNumber"
    assert select_output(
        {"DocNumber": "9"}, _qbo_record_id(stored, "query_entity")
    ) == ("9")
    assert _qbo_record_id(stored, "change_data") == "$.SyncToken"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="record id"):
        _qbo_record_id(old, "query_entity")
    with pytest.raises(ValueError, match="record id"):
        _qbo_record_id(old, "change_data")


def test_quickbooks_record_timestamp_comes_from_the_package(monkeypatch):
    """Each QuickBooks record timestamp is read from the package output path."""
    from app.agentive.connectors.quickbooks import _qbo_record_updated_at
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output

    rescan()
    body = {"MetaData": {"LastUpdatedTime": "2020-01-01T00:00:00Z"}}
    assert (
        select_output(body, _qbo_record_updated_at(None, "query_entity"))
        == "2020-01-01T00:00:00Z"
    )
    assert _qbo_record_updated_at(None, "change_data") == "$.MetaData.LastUpdatedTime"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/v3/company/1/query"},
                    "output": {"select": "$.QueryResponse", "id": "$.Id"},
                },
                {
                    "name": "change_data",
                    "request": {"method": "GET", "path": "/v3/company/1/cdc"},
                    "output": {"select": "$.CDCResponse.QueryResponse", "id": "$.Id"},
                },
            ]
        }
    }
    assert _qbo_record_updated_at(old, "query_entity") == "$.MetaData.LastUpdatedTime"
    assert _qbo_record_updated_at(old, "change_data") == "$.MetaData.LastUpdatedTime"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/custom/query"},
                    "output": {"select": "$.data", "updated_at": "$.TxnDate"},
                },
                {
                    "name": "change_data",
                    "request": {"method": "GET", "path": "/custom/cdc"},
                    "output": {"select": "$.changes", "updated_at": "$.When"},
                },
            ]
        }
    }
    assert _qbo_record_updated_at(stored, "query_entity") == "$.TxnDate"
    assert select_output({"TxnDate": "2024-02-02"}, "$.TxnDate") == "2024-02-02"
    assert _qbo_record_updated_at(stored, "change_data") == "$.When"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="record timestamp"):
        _qbo_record_updated_at(old, "query_entity")
    with pytest.raises(ValueError, match="record timestamp"):
        _qbo_record_updated_at(old, "change_data")


def test_quickbooks_display_name_comes_from_the_package(monkeypatch):
    """Customer and vendor display names come from the package title path."""
    from app.agentive.connectors.quickbooks import (
        QuickBooksConnector,
        _qbo_display_name,
    )
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output
    from app.services.connectors import ExternalRecord

    rescan()
    assert select_output({"DisplayName": "Ada"}, _qbo_display_name(None)) == "Ada"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/v3/company/1/query"},
                    "output": {"select": "$.QueryResponse", "id": "$.Id"},
                }
            ]
        }
    }
    assert _qbo_display_name(old) == "$.DisplayName"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/custom/query"},
                    "output": {"select": "$.data", "title": "$.PrintName"},
                }
            ]
        }
    }
    assert _qbo_display_name(stored) == "$.PrintName"
    record = ExternalRecord(
        external_id="1",
        payload={"_qb_entity": "Customer", "PrintName": "Ada"},
    )
    entry = QuickBooksConnector().to_entry(record, stored)
    assert entry.title == "Ada"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="display name"):
        _qbo_display_name(old)


def test_quickbooks_company_name_comes_from_the_package(monkeypatch):
    """Customer and vendor company names come from the package name path."""
    from app.agentive.connectors.quickbooks import (
        QuickBooksConnector,
        _qbo_company_name,
    )
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output
    from app.services.connectors import ExternalRecord

    rescan()
    assert select_output({"CompanyName": "Acme"}, _qbo_company_name(None)) == "Acme"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/v3/company/1/query"},
                    "output": {"select": "$.QueryResponse", "title": "$.DisplayName"},
                }
            ]
        }
    }
    assert _qbo_company_name(old) == "$.CompanyName"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/custom/query"},
                    "output": {"select": "$.data", "name": "$.LegalName"},
                }
            ]
        }
    }
    assert _qbo_company_name(stored) == "$.LegalName"
    record = ExternalRecord(
        external_id="1",
        payload={
            "_qb_entity": "Customer",
            "LegalName": "Acme",
            "DisplayName": "Ada",
        },
    )
    entry = QuickBooksConnector().to_entry(record, stored)
    assert entry.title == "Acme"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="company name"):
        _qbo_company_name(old)


def test_quickbooks_document_number_comes_from_the_package(monkeypatch):
    """Invoice and bill numbers come from the package number path."""
    from app.agentive.connectors.quickbooks import (
        QuickBooksConnector,
        _qbo_document_number,
    )
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output
    from app.services.connectors import ExternalRecord

    rescan()
    assert select_output({"DocNumber": "1001"}, _qbo_document_number(None)) == "1001"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/v3/company/1/query"},
                    "output": {"select": "$.QueryResponse", "name": "$.CompanyName"},
                }
            ]
        }
    }
    assert _qbo_document_number(old) == "$.DocNumber"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/custom/query"},
                    "output": {"select": "$.data", "number": "$.Ref"},
                }
            ]
        }
    }
    assert _qbo_document_number(stored) == "$.Ref"
    record = ExternalRecord(
        external_id="1",
        payload={"_qb_entity": "Invoice", "Ref": "1001"},
    )
    entry = QuickBooksConnector().to_entry(record, stored)
    assert entry.custom_fields["invoice_number"] == "1001"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="document number"):
        _qbo_document_number(old)


def test_quickbooks_party_name_comes_from_the_package(monkeypatch):
    """Invoice party names come from the package party path."""
    from app.agentive.connectors.quickbooks import (
        QuickBooksConnector,
        _qbo_party_name,
    )
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output
    from app.services.connectors import ExternalRecord

    rescan()
    assert (
        select_output({"CustomerRef": {"name": "Ada"}}, _qbo_party_name(None)) == "Ada"
    )
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/v3/company/1/query"},
                    "output": {"select": "$.QueryResponse", "number": "$.DocNumber"},
                }
            ]
        }
    }
    assert _qbo_party_name(old) == "$.CustomerRef.name"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/custom/query"},
                    "output": {"select": "$.data", "party": "$.Buyer.label"},
                }
            ]
        }
    }
    assert _qbo_party_name(stored) == "$.Buyer.label"
    record = ExternalRecord(
        external_id="1",
        payload={"_qb_entity": "Invoice", "Buyer": {"label": "Ada"}},
    )
    entry = QuickBooksConnector().to_entry(record, stored)
    assert entry.custom_fields["qb_customer_name"] == "Ada"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="party name"):
        _qbo_party_name(old)


def test_quickbooks_reference_key_comes_from_the_package(monkeypatch):
    """Bill and expense vendor names use the package reference key."""
    from app.agentive.connectors.quickbooks import QuickBooksConnector, _qbo_ref_key
    from app.connectors.package_discovery import rescan
    from app.services.connectors import ExternalRecord

    rescan()
    assert _qbo_ref_key(None) == "name"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/v3/company/1/query"},
                    "output": {
                        "select": "$.QueryResponse",
                        "party": "$.CustomerRef.name",
                    },
                }
            ]
        }
    }
    assert _qbo_ref_key(old) == "name"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/custom/query"},
                    "output": {"select": "$.data", "ref_key": "label"},
                }
            ]
        }
    }
    assert _qbo_ref_key(stored) == "label"
    record = ExternalRecord(
        external_id="1",
        payload={"_qb_entity": "Bill", "VendorRef": {"label": "Ada"}},
    )
    entry = QuickBooksConnector().to_entry(record, stored)
    assert entry.custom_fields["qb_vendor_name"] == "Ada"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="reference key"):
        _qbo_ref_key(old)


def test_quickbooks_transaction_date_comes_from_the_package(monkeypatch):
    """Transaction dates come from the package occurred path."""
    from app.agentive.connectors.quickbooks import (
        QuickBooksConnector,
        _qbo_occurred,
    )
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output
    from app.services.connectors import ExternalRecord

    rescan()
    assert select_output({"TxnDate": "2024-01-01"}, _qbo_occurred(None)) == (
        "2024-01-01"
    )
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/v3/company/1/query"},
                    "output": {"select": "$.QueryResponse", "ref_key": "name"},
                }
            ]
        }
    }
    assert _qbo_occurred(old) == "$.TxnDate"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/custom/query"},
                    "output": {"select": "$.data", "occurred": "$.When"},
                }
            ]
        }
    }
    assert _qbo_occurred(stored) == "$.When"
    record = ExternalRecord(
        external_id="1",
        payload={"_qb_entity": "Invoice", "When": "2024-01-01"},
    )
    entry = QuickBooksConnector().to_entry(record, stored)
    assert entry.custom_fields["txn_date"] == "2024-01-01"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="transaction date"):
        _qbo_occurred(old)


def test_quickbooks_due_date_comes_from_the_package(monkeypatch):
    """Due dates come from the package due path."""
    from app.agentive.connectors.quickbooks import QuickBooksConnector, _qbo_due
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output
    from app.services.connectors import ExternalRecord

    rescan()
    assert select_output({"DueDate": "2024-02-01"}, _qbo_due(None)) == "2024-02-01"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/v3/company/1/query"},
                    "output": {"select": "$.QueryResponse", "occurred": "$.TxnDate"},
                }
            ]
        }
    }
    assert _qbo_due(old) == "$.DueDate"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/custom/query"},
                    "output": {"select": "$.data", "due": "$.PayBy"},
                }
            ]
        }
    }
    assert _qbo_due(stored) == "$.PayBy"
    record = ExternalRecord(
        external_id="1",
        payload={"_qb_entity": "Invoice", "PayBy": "2024-02-01"},
    )
    entry = QuickBooksConnector().to_entry(record, stored)
    assert entry.custom_fields["due_date"] == "2024-02-01"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="due date"):
        _qbo_due(old)


def test_quickbooks_total_amount_comes_from_the_package(monkeypatch):
    """Totals come from the package amount path."""
    from app.agentive.connectors.quickbooks import QuickBooksConnector, _qbo_amount
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output
    from app.services.connectors import ExternalRecord

    rescan()
    assert select_output({"TotalAmt": 10}, _qbo_amount(None)) == 10
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/v3/company/1/query"},
                    "output": {"select": "$.QueryResponse", "due": "$.DueDate"},
                }
            ]
        }
    }
    assert _qbo_amount(old) == "$.TotalAmt"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/custom/query"},
                    "output": {"select": "$.data", "amount": "$.Sum"},
                }
            ]
        }
    }
    assert _qbo_amount(stored) == "$.Sum"
    record = ExternalRecord(
        external_id="1",
        payload={"_qb_entity": "Invoice", "Sum": 10},
    )
    entry = QuickBooksConnector().to_entry(record, stored)
    assert entry.custom_fields["total_amount"] == 10
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="total amount"):
        _qbo_amount(old)


def test_quickbooks_balance_comes_from_the_package(monkeypatch):
    """Balances come from the package balance path."""
    from app.agentive.connectors.quickbooks import QuickBooksConnector, _qbo_balance
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output
    from app.services.connectors import ExternalRecord

    rescan()
    assert select_output({"Balance": 4}, _qbo_balance(None)) == 4
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/v3/company/1/query"},
                    "output": {"select": "$.QueryResponse", "amount": "$.TotalAmt"},
                }
            ]
        }
    }
    assert _qbo_balance(old) == "$.Balance"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/custom/query"},
                    "output": {"select": "$.data", "balance": "$.Open"},
                }
            ]
        }
    }
    assert _qbo_balance(stored) == "$.Open"
    record = ExternalRecord(
        external_id="1",
        payload={"_qb_entity": "Invoice", "Open": 4},
    )
    entry = QuickBooksConnector().to_entry(record, stored)
    assert entry.custom_fields["balance"] == 4
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="balance"):
        _qbo_balance(old)


def test_quickbooks_email_comes_from_the_package(monkeypatch):
    """Email addresses come from the package email path."""
    from app.agentive.connectors.quickbooks import QuickBooksConnector, _qbo_email
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output
    from app.services.connectors import ExternalRecord

    rescan()
    assert select_output(
        {"PrimaryEmailAddr": {"Address": "ada@example.com"}}, _qbo_email(None)
    ) == {"Address": "ada@example.com"}
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/v3/company/1/query"},
                    "output": {"select": "$.QueryResponse", "balance": "$.Balance"},
                }
            ]
        }
    }
    assert _qbo_email(old) == "$.PrimaryEmailAddr"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/custom/query"},
                    "output": {"select": "$.data", "email": "$.Mail"},
                }
            ]
        }
    }
    assert _qbo_email(stored) == "$.Mail"
    record = ExternalRecord(
        external_id="1",
        payload={"_qb_entity": "Customer", "Mail": "Ada@Example.com"},
    )
    entry = QuickBooksConnector().to_entry(record, stored)
    assert entry.custom_fields["email"] == "ada@example.com"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="email"):
        _qbo_email(old)


def test_quickbooks_phone_comes_from_the_package(monkeypatch):
    """Phone numbers come from the package phone path."""
    from app.agentive.connectors.quickbooks import QuickBooksConnector, _qbo_phone
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output
    from app.services.connectors import ExternalRecord

    rescan()
    assert (
        select_output(
            {"PrimaryPhone": {"FreeFormNumber": "555-0100"}}, _qbo_phone(None)
        )
        == "555-0100"
    )
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/v3/company/1/query"},
                    "output": {
                        "select": "$.QueryResponse",
                        "email": "$.PrimaryEmailAddr",
                    },
                }
            ]
        }
    }
    assert _qbo_phone(old) == "$.PrimaryPhone.FreeFormNumber"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/custom/query"},
                    "output": {"select": "$.data", "phone": "$.Tel"},
                }
            ]
        }
    }
    assert _qbo_phone(stored) == "$.Tel"
    record = ExternalRecord(
        external_id="1",
        payload={"_qb_entity": "Customer", "Tel": "555-0100"},
    )
    entry = QuickBooksConnector().to_entry(record, stored)
    assert entry.custom_fields["phone"] == "555-0100"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="phone number"):
        _qbo_phone(old)


def test_quickbooks_account_number_comes_from_the_package(monkeypatch):
    """Account numbers come from the package account-number path."""
    from app.agentive.connectors.quickbooks import (
        QuickBooksConnector,
        _qbo_account_number,
    )
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output
    from app.services.connectors import ExternalRecord

    rescan()
    assert select_output({"AcctNum": "A-1"}, _qbo_account_number(None)) == "A-1"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/v3/company/1/query"},
                    "output": {"select": "$.QueryResponse", "phone": "$.Tel"},
                }
            ]
        }
    }
    assert _qbo_account_number(old) == "$.AcctNum"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/custom/query"},
                    "output": {"select": "$.data", "account_number": "$.Code"},
                }
            ]
        }
    }
    assert _qbo_account_number(stored) == "$.Code"
    record = ExternalRecord(
        external_id="1",
        payload={"_qb_entity": "Vendor", "Code": "A-1"},
    )
    entry = QuickBooksConnector().to_entry(record, stored)
    assert entry.custom_fields["account_number"] == "A-1"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="account number"):
        _qbo_account_number(old)


def test_quickbooks_source_version_comes_from_the_package(monkeypatch):
    """Source versions come from the package version path."""
    from app.agentive.connectors.quickbooks import (
        QuickBooksConnector,
        _qbo_source_version,
    )
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output
    from app.services.connectors import ExternalRecord

    rescan()
    assert select_output({"SyncToken": "3"}, _qbo_source_version(None)) == "3"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/v3/company/1/query"},
                    "output": {
                        "select": "$.QueryResponse",
                        "account_number": "$.AcctNum",
                    },
                }
            ]
        }
    }
    assert _qbo_source_version(old) == "$.SyncToken"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/custom/query"},
                    "output": {"select": "$.data", "version": "$.Rev"},
                }
            ]
        }
    }
    assert _qbo_source_version(stored) == "$.Rev"
    record = ExternalRecord(
        external_id="1",
        payload={"_qb_entity": "Customer", "Rev": "9"},
    )
    entry = QuickBooksConnector().to_entry(record, stored)
    assert entry.custom_fields["_source_version"] == "9"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="source version"):
        _qbo_source_version(old)


def test_quickbooks_payment_type_comes_from_the_package(monkeypatch):
    """Expense payment types come from the package payment path."""
    from app.agentive.connectors.quickbooks import QuickBooksConnector, _qbo_payment
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output
    from app.services.connectors import ExternalRecord

    rescan()
    assert select_output({"PaymentType": "Cash"}, _qbo_payment(None)) == "Cash"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/v3/company/1/query"},
                    "output": {"select": "$.QueryResponse", "version": "$.SyncToken"},
                }
            ]
        }
    }
    assert _qbo_payment(old) == "$.PaymentType"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/custom/query"},
                    "output": {"select": "$.data", "payment": "$.Method"},
                }
            ]
        }
    }
    assert _qbo_payment(stored) == "$.Method"
    record = ExternalRecord(
        external_id="1",
        payload={"_qb_entity": "Purchase", "Method": "Cash"},
    )
    entry = QuickBooksConnector().to_entry(record, stored)
    assert entry.custom_fields["payment_type"] == "cash"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="payment type"):
        _qbo_payment(old)


def test_quickbooks_ledger_account_comes_from_the_package(monkeypatch):
    """Expense ledger accounts come from the package ledger path."""
    from app.agentive.connectors.quickbooks import QuickBooksConnector, _qbo_ledger
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output
    from app.services.connectors import ExternalRecord

    rescan()
    assert select_output({"AccountRef": {"name": "Cash"}}, _qbo_ledger(None)) == "Cash"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/v3/company/1/query"},
                    "output": {"select": "$.QueryResponse", "payment": "$.PaymentType"},
                }
            ]
        }
    }
    assert _qbo_ledger(old) == "$.AccountRef.name"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/custom/query"},
                    "output": {"select": "$.data", "ledger": "$.Books.label"},
                }
            ]
        }
    }
    assert _qbo_ledger(stored) == "$.Books.label"
    record = ExternalRecord(
        external_id="1",
        payload={"_qb_entity": "Purchase", "Books": {"label": "Cash"}},
    )
    entry = QuickBooksConnector().to_entry(record, stored)
    assert entry.custom_fields["account"] == "Cash"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="ledger account"):
        _qbo_ledger(old)


def test_quickbooks_expense_category_comes_from_the_package(monkeypatch):
    """Expense categories come from the package category path."""
    from app.agentive.connectors.quickbooks import QuickBooksConnector, _qbo_category
    from app.connectors.package_discovery import rescan
    from app.services.connectors import ExternalRecord

    rescan()
    admitted_path = "$.Line.AccountBasedExpenseLineDetail.AccountRef.name"
    assert _qbo_category(None) == admitted_path
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/v3/company/1/query"},
                    "output": {
                        "select": "$.QueryResponse",
                        "ledger": "$.AccountRef.name",
                    },
                }
            ]
        }
    }
    assert _qbo_category(old) == admitted_path
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/custom/query"},
                    "output": {"select": "$.data", "category": "$.Groups.label"},
                }
            ]
        }
    }
    assert _qbo_category(stored) == "$.Groups.label"
    record = ExternalRecord(
        external_id="1",
        payload={
            "_qb_entity": "Purchase",
            "Groups": [{"label": ""}, {"label": "Travel"}],
        },
    )
    entry = QuickBooksConnector().to_entry(record, stored)
    assert entry.custom_fields["category"] == "Travel"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="expense category"):
        _qbo_category(old)


def test_quickbooks_reference_id_comes_from_the_package(monkeypatch):
    """Related reference ids come from the package reference id key."""
    from app.agentive.connectors.quickbooks import QuickBooksConnector, _qbo_ref_id
    from app.connectors.package_discovery import rescan
    from app.services.connectors import ExternalRecord

    rescan()
    assert _qbo_ref_id(None) == "value"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/v3/company/1/query"},
                    "output": {"select": "$.QueryResponse", "ref_key": "name"},
                }
            ]
        }
    }
    assert _qbo_ref_id(old) == "value"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/custom/query"},
                    "output": {"select": "$.data", "ref_id": "Id"},
                }
            ]
        }
    }
    assert _qbo_ref_id(stored) == "Id"
    record = ExternalRecord(
        external_id="1",
        payload={
            "_qb_entity": "Invoice",
            "CustomerRef": {"Id": "42", "name": "Ada"},
        },
    )
    entry = QuickBooksConnector().to_entry(record, stored)
    assert entry.custom_fields["qb_customer_id"] == "42"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="reference id"):
        _qbo_ref_id(old)


def test_quickbooks_email_key_comes_from_the_package(monkeypatch):
    """Email objects use the package address key."""
    from app.agentive.connectors.quickbooks import QuickBooksConnector, _qbo_email_key
    from app.connectors.package_discovery import rescan
    from app.services.connectors import ExternalRecord

    rescan()
    assert _qbo_email_key(None) == "Address"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/v3/company/1/query"},
                    "output": {
                        "select": "$.QueryResponse",
                        "email": "$.PrimaryEmailAddr",
                    },
                }
            ]
        }
    }
    assert _qbo_email_key(old) == "Address"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/custom/query"},
                    "output": {"select": "$.data", "email_key": "Mail"},
                }
            ]
        }
    }
    assert _qbo_email_key(stored) == "Mail"
    record = ExternalRecord(
        external_id="1",
        payload={
            "_qb_entity": "Customer",
            "PrimaryEmailAddr": {"Mail": "Ada@Example.com"},
        },
    )
    entry = QuickBooksConnector().to_entry(record, stored)
    assert entry.custom_fields["email"] == "ada@example.com"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="email key"):
        _qbo_email_key(old)


def test_quickbooks_customer_reference_comes_from_the_package(monkeypatch):
    """Invoice customer references come from the package customer key."""
    from app.agentive.connectors.quickbooks import (
        QuickBooksConnector,
        _qbo_customer_ref,
    )
    from app.connectors.package_discovery import rescan
    from app.services.connectors import ExternalRecord

    rescan()
    assert _qbo_customer_ref(None) == "CustomerRef"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/v3/company/1/query"},
                    "output": {"select": "$.QueryResponse", "ref_id": "value"},
                }
            ]
        }
    }
    assert _qbo_customer_ref(old) == "CustomerRef"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/custom/query"},
                    "output": {"select": "$.data", "customer_ref": "Buyer"},
                }
            ]
        }
    }
    assert _qbo_customer_ref(stored) == "Buyer"
    record = ExternalRecord(
        external_id="1",
        payload={
            "_qb_entity": "Invoice",
            "CustomerRef": {"value": "1"},
            "Buyer": {"value": "42"},
        },
    )
    entry = QuickBooksConnector().to_entry(record, stored)
    assert entry.custom_fields["qb_customer_id"] == "42"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="customer reference"):
        _qbo_customer_ref(old)


def test_quickbooks_expense_reference_comes_from_the_package(monkeypatch):
    """Expense payee references come from the package expense key."""
    from app.agentive.connectors.quickbooks import (
        QuickBooksConnector,
        _qbo_expense_ref,
    )
    from app.connectors.package_discovery import rescan
    from app.services.connectors import ExternalRecord

    rescan()
    assert _qbo_expense_ref(None) == "EntityRef"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/v3/company/1/query"},
                    "output": {
                        "select": "$.QueryResponse",
                        "customer_ref": "CustomerRef",
                    },
                }
            ]
        }
    }
    assert _qbo_expense_ref(old) == "EntityRef"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/custom/query"},
                    "output": {"select": "$.data", "expense_ref": "Payee"},
                }
            ]
        }
    }
    assert _qbo_expense_ref(stored) == "Payee"
    record = ExternalRecord(
        external_id="1",
        payload={
            "_qb_entity": "Purchase",
            "EntityRef": {"value": "1", "name": "Ada"},
            "Payee": {"value": "42", "name": "Bea"},
        },
    )
    entry = QuickBooksConnector().to_entry(record, stored)
    assert entry.custom_fields["qb_vendor_id"] == "42"
    assert entry.custom_fields["qb_vendor_name"] == "Bea"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="expense reference"):
        _qbo_expense_ref(old)


def test_quickbooks_vendor_reference_comes_from_the_package(monkeypatch):
    """Bill vendor references come from the package vendor key."""
    from app.agentive.connectors.quickbooks import (
        QuickBooksConnector,
        _qbo_vendor_ref,
    )
    from app.connectors.package_discovery import rescan
    from app.services.connectors import ExternalRecord

    rescan()
    assert _qbo_vendor_ref(None) == "VendorRef"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/v3/company/1/query"},
                    "output": {"select": "$.QueryResponse", "expense_ref": "EntityRef"},
                }
            ]
        }
    }
    assert _qbo_vendor_ref(old) == "VendorRef"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "query_entity",
                    "request": {"method": "GET", "path": "/custom/query"},
                    "output": {"select": "$.data", "vendor_ref": "Seller"},
                }
            ]
        }
    }
    assert _qbo_vendor_ref(stored) == "Seller"
    record = ExternalRecord(
        external_id="1",
        payload={
            "_qb_entity": "Bill",
            "VendorRef": {"value": "1", "name": "Ada"},
            "Seller": {"value": "42", "name": "Bea"},
        },
    )
    entry = QuickBooksConnector().to_entry(record, stored)
    assert entry.custom_fields["qb_vendor_id"] == "42"
    assert entry.custom_fields["qb_vendor_name"] == "Bea"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="vendor reference"):
        _qbo_vendor_ref(old)


def test_quickbooks_entities_field_comes_from_the_package(monkeypatch):
    """Change-data sends the entity list under the package query key."""
    from urllib.parse import parse_qs, urlparse

    from app.agentive.connectors.quickbooks import _qbo_change_url
    from app.connectors.package_discovery import rescan

    rescan()
    url = _qbo_change_url(
        "https://quickbooks.api.intuit.com", "123", "Invoice", "t1", None
    )
    assert parse_qs(urlparse(url).query)["entities"] == ["Invoice"]
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "change_data",
                    "request": {
                        "method": "GET",
                        "path": "/v3/company/{{config.realm_id}}/cdc",
                        "query": {"changedSince": "{{cursor}}"},
                    },
                }
            ]
        }
    }
    old_query = parse_qs(
        urlparse(
            _qbo_change_url("https://stored.example", "9", "Invoice,Bill", "t2", old)
        ).query
    )
    assert old_query["entities"] == ["Invoice,Bill"]
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "change_data",
                    "request": {
                        "method": "GET",
                        "path": "/custom/{{config.realm_id}}/cdc",
                        "query": {
                            "changedSince": "{{cursor}}",
                            "companyEntities": "{{input.entities}}",
                        },
                    },
                }
            ]
        }
    }
    stored_query = parse_qs(
        urlparse(
            _qbo_change_url(
                "https://stored.example", "9", "Customer,Vendor", "t3", stored
            )
        ).query
    )
    assert stored_query["companyEntities"] == ["Customer,Vendor"]
    assert "entities" not in stored_query
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="entities field"):
        _qbo_change_url("https://stored.example", "9", "Invoice", "t4", old)


def test_quickbooks_entities_come_from_the_package(monkeypatch):
    """Sync entities come from the snapshot, then the admitted package."""
    from app.agentive.connectors.quickbooks import _qbo_entities
    from app.connectors.package_discovery import rescan

    rescan()
    assert _qbo_entities(None) == [
        "Invoice",
        "Purchase",
        "Customer",
        "Vendor",
        "Bill",
    ]
    old = {"capabilities": {"sync": {"entry_type": "quickbooks"}}}
    assert _qbo_entities(old)[0] == "Invoice"
    stored = {
        "capabilities": {"sync": {"entry_type": "quickbooks", "entities": ["Invoice"]}}
    }
    assert _qbo_entities(stored) == ["Invoice"]
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="does not declare"):
        _qbo_entities(None)


def test_quickbooks_page_size_comes_from_the_package(monkeypatch):
    """Entity query page size comes from the snapshot, then the admitted package."""
    from app.agentive.connectors.quickbooks import _qbo_page_size
    from app.connectors.package_discovery import rescan

    rescan()
    assert _qbo_page_size(None) == 100
    old = {
        "capabilities": {"sync": {"entry_type": "quickbooks", "entities": ["Invoice"]}}
    }
    assert _qbo_page_size(old) == 100
    stored = {
        "capabilities": {
            "sync": {
                "entry_type": "quickbooks",
                "entities": ["Invoice"],
                "page_size": 25,
            }
        }
    }
    assert _qbo_page_size(stored) == 25
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="does not declare"):
        _qbo_page_size(None)


def test_quickbooks_max_pages_comes_from_the_package(monkeypatch):
    """Entity queries follow the package page cap, then the admitted package."""
    from app.agentive.connectors.quickbooks import _qbo_max_pages
    from app.connectors.package_discovery import rescan

    rescan()
    assert _qbo_max_pages(None) == 100
    old = {
        "capabilities": {
            "sync": {
                "entry_type": "quickbooks",
                "entities": ["Invoice"],
                "page_size": 100,
            }
        }
    }
    assert _qbo_max_pages(old) == 100
    stored = {
        "capabilities": {
            "sync": {
                "entry_type": "quickbooks",
                "entities": ["Invoice"],
                "max_pages": 3,
            }
        }
    }
    assert _qbo_max_pages(stored) == 3
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="declare a page cap"):
        _qbo_max_pages(old)


def test_quickbooks_invoice_type_comes_from_the_package(monkeypatch):
    """Invoices use the package entity type, for any package id."""
    from app.agentive.connectors.quickbooks import QuickBooksConnector
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_entity_type
    from app.services.connectors import ExternalRecord

    rescan()
    assert package_entity_type(None, "quickbooks", "Invoice") == "qb_invoice"
    old = {
        "capabilities": {"sync": {"entry_type": "quickbooks", "entities": ["Invoice"]}}
    }
    assert package_entity_type(old, "quickbooks", "Invoice") == "qb_invoice"
    stored = {
        "capabilities": {
            "sync": {
                "entry_type": "quickbooks",
                "entity_types": {"Invoice": "sales_doc"},
            }
        }
    }
    entry = QuickBooksConnector().to_entry(
        ExternalRecord(external_id="1", payload={"_qb_entity": "Invoice"}),
        stored,
    )
    assert entry.entry_type_key == "sales_doc"
    foreign = {
        "id": "acme_books",
        "capabilities": {
            "sync": {"entry_type": "ledger", "entity_types": {"Invoice": "sales_doc"}}
        },
    }
    assert package_entity_type(foreign, "acme_books", "Invoice") == "sales_doc"
    missing = {"id": "acme_books", "capabilities": {"sync": {"entry_type": "ledger"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="entity type"):
        package_entity_type(missing, "acme_books", "Invoice")


def test_quickbooks_expense_type_comes_from_the_package(monkeypatch):
    """Expenses use the package entity type, for any package id."""
    from app.agentive.connectors.quickbooks import QuickBooksConnector
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_entity_type
    from app.services.connectors import ExternalRecord

    rescan()
    assert package_entity_type(None, "quickbooks", "Purchase") == "qb_expense"
    old = {
        "capabilities": {
            "sync": {
                "entry_type": "quickbooks",
                "entity_types": {"Invoice": "qb_invoice"},
            }
        }
    }
    assert package_entity_type(old, "quickbooks", "Purchase") == "qb_expense"
    stored = {
        "capabilities": {
            "sync": {
                "entry_type": "quickbooks",
                "entity_types": {"Purchase": "spend_item"},
            }
        }
    }
    entry = QuickBooksConnector().to_entry(
        ExternalRecord(external_id="1", payload={"_qb_entity": "Purchase"}),
        stored,
    )
    assert entry.entry_type_key == "spend_item"
    foreign = {
        "id": "acme_books",
        "capabilities": {
            "sync": {
                "entry_type": "ledger",
                "entity_types": {"Purchase": "spend_item"},
            }
        },
    }
    assert package_entity_type(foreign, "acme_books", "Purchase") == "spend_item"
    missing = {"id": "acme_books", "capabilities": {"sync": {"entry_type": "ledger"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="entity type"):
        package_entity_type(missing, "acme_books", "Purchase")


def test_quickbooks_customer_type_comes_from_the_package(monkeypatch):
    """Customers use the package entity type, for any package id."""
    from app.agentive.connectors.quickbooks import QuickBooksConnector
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_entity_type
    from app.services.connectors import ExternalRecord

    rescan()
    assert package_entity_type(None, "quickbooks", "Customer") == "qb_customer"
    old = {
        "capabilities": {
            "sync": {
                "entry_type": "quickbooks",
                "entity_types": {"Invoice": "qb_invoice", "Purchase": "qb_expense"},
            }
        }
    }
    assert package_entity_type(old, "quickbooks", "Customer") == "qb_customer"
    stored = {
        "capabilities": {
            "sync": {
                "entry_type": "quickbooks",
                "entity_types": {"Customer": "buyer"},
            }
        }
    }
    entry = QuickBooksConnector().to_entry(
        ExternalRecord(external_id="1", payload={"_qb_entity": "Customer"}),
        stored,
    )
    assert entry.entry_type_key == "buyer"
    foreign = {
        "id": "acme_books",
        "capabilities": {
            "sync": {
                "entry_type": "ledger",
                "entity_types": {"Customer": "buyer"},
            }
        },
    }
    assert package_entity_type(foreign, "acme_books", "Customer") == "buyer"
    missing = {"id": "acme_books", "capabilities": {"sync": {"entry_type": "ledger"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="entity type"):
        package_entity_type(missing, "acme_books", "Customer")


def test_quickbooks_vendor_type_comes_from_the_package(monkeypatch):
    """Vendors use the package entity type, for any package id."""
    from app.agentive.connectors.quickbooks import QuickBooksConnector
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_entity_type
    from app.services.connectors import ExternalRecord

    rescan()
    assert package_entity_type(None, "quickbooks", "Vendor") == "qb_vendor"
    old = {
        "capabilities": {
            "sync": {
                "entry_type": "quickbooks",
                "entity_types": {"Customer": "qb_customer"},
            }
        }
    }
    assert package_entity_type(old, "quickbooks", "Vendor") == "qb_vendor"
    stored = {
        "capabilities": {
            "sync": {
                "entry_type": "quickbooks",
                "entity_types": {"Vendor": "supplier"},
            }
        }
    }
    entry = QuickBooksConnector().to_entry(
        ExternalRecord(external_id="1", payload={"_qb_entity": "Vendor"}),
        stored,
    )
    assert entry.entry_type_key == "supplier"
    foreign = {
        "id": "acme_books",
        "capabilities": {
            "sync": {
                "entry_type": "ledger",
                "entity_types": {"Vendor": "supplier"},
            }
        },
    }
    assert package_entity_type(foreign, "acme_books", "Vendor") == "supplier"
    missing = {"id": "acme_books", "capabilities": {"sync": {"entry_type": "ledger"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="entity type"):
        package_entity_type(missing, "acme_books", "Vendor")


def test_quickbooks_bill_type_comes_from_the_package(monkeypatch):
    """Bills use the package entity type, for any package id."""
    from app.agentive.connectors.quickbooks import QuickBooksConnector
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_entity_type
    from app.services.connectors import ExternalRecord

    rescan()
    assert package_entity_type(None, "quickbooks", "Bill") == "qb_bill"
    old = {
        "capabilities": {
            "sync": {
                "entry_type": "quickbooks",
                "entity_types": {"Vendor": "qb_vendor"},
            }
        }
    }
    assert package_entity_type(old, "quickbooks", "Bill") == "qb_bill"
    stored = {
        "capabilities": {
            "sync": {
                "entry_type": "quickbooks",
                "entity_types": {"Bill": "payable"},
            }
        }
    }
    entry = QuickBooksConnector().to_entry(
        ExternalRecord(external_id="1", payload={"_qb_entity": "Bill"}),
        stored,
    )
    assert entry.entry_type_key == "payable"
    foreign = {
        "id": "acme_books",
        "capabilities": {
            "sync": {
                "entry_type": "ledger",
                "entity_types": {"Bill": "payable"},
            }
        },
    }
    assert package_entity_type(foreign, "acme_books", "Bill") == "payable"
    missing = {"id": "acme_books", "capabilities": {"sync": {"entry_type": "ledger"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="entity type"):
        package_entity_type(missing, "acme_books", "Bill")


def test_quickbooks_conflict_policy_comes_from_the_package(monkeypatch):
    """QuickBooks conflict policy comes from the package, for any package id."""
    from app.agentive.connectors.quickbooks import QuickBooksConnector
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_conflict_policy

    rescan()
    assert package_conflict_policy(None, "quickbooks") == "last_write_wins"
    assert QuickBooksConnector().conflict_policy == "last_write_wins"
    old = {"capabilities": {"sync": {"entry_type": "quickbooks"}}}
    assert package_conflict_policy(old, "quickbooks") == "last_write_wins"
    stored = {
        "capabilities": {
            "sync": {"entry_type": "quickbooks", "conflict_policy": "manual_resolve"}
        }
    }
    assert package_conflict_policy(stored, "quickbooks") == "manual_resolve"
    foreign = {
        "id": "acme_books",
        "capabilities": {
            "sync": {"entry_type": "ledger", "conflict_policy": "mirror_only"}
        },
    }
    assert package_conflict_policy(foreign, "acme_books") == "mirror_only"
    missing = {"id": "acme_books", "capabilities": {"sync": {"entry_type": "ledger"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="conflict policy"):
        package_conflict_policy(missing, "acme_books")


def test_quickbooks_title_prefix_comes_from_the_package(monkeypatch):
    """QuickBooks titles use the package prefix for the listed entity."""
    from app.agentive.connectors.quickbooks import QuickBooksConnector
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_title_prefix_for
    from app.services.connectors import ExternalRecord

    rescan()
    assert package_title_prefix_for(None, "quickbooks", "Customer") == "Customer"
    old = {
        "capabilities": {
            "sync": {
                "entry_type": "quickbooks",
                "title_prefixes": {"Invoice": "Invoice"},
            }
        }
    }
    assert package_title_prefix_for(old, "quickbooks", "Customer") == "Customer"
    stored = {
        "capabilities": {
            "sync": {
                "entry_type": "quickbooks",
                "title_prefixes": {"Customer": "Buyer"},
            }
        }
    }
    entry = QuickBooksConnector().to_entry(
        ExternalRecord(external_id="1", payload={"_qb_entity": "Customer"}),
        stored,
    )
    assert entry.title == "Buyer 1"
    foreign = {
        "id": "acme_books",
        "capabilities": {
            "sync": {
                "entry_type": "ledger",
                "title_prefixes": {"Customer": "Buyer"},
            }
        },
    }
    assert package_title_prefix_for(foreign, "acme_books", "Customer") == "Buyer"
    missing = {"id": "acme_books", "capabilities": {"sync": {"entry_type": "ledger"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="title prefix"):
        package_title_prefix_for(missing, "acme_books", "Customer")


def test_quickbooks_destination_fields_come_from_the_package(monkeypatch):
    """QuickBooks destination fields are written under the package field names."""
    from app.agentive.connectors.quickbooks import QuickBooksConnector
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_field_name
    from app.services.connectors import ExternalRecord

    remaining = {
        "_source_system": "origin",
        "_source_version": "revision",
        "_last_synced_at": "synced",
        "_qb_entity": "kind",
        "_realm_id": "realm",
        "invoice_number": "doc",
        "qb_customer_id": "customer",
        "qb_customer_name": "customer_name",
        "total_amount": "amount",
        "balance": "open_balance",
        "txn_date": "occurred_on",
        "due_date": "due_on",
        "status": "invoice_status",
        "payment_type": "pay_type",
        "qb_vendor_id": "vendor",
        "qb_vendor_name": "vendor_name",
        "account": "ledger",
        "category": "expense_category",
        "company_name": "company",
        "email": "mail",
        "phone": "tel",
        "crm_link_status": "link_status",
        "account_number": "acct",
        "bill_number": "bill",
    }
    rescan()
    for key in remaining:
        assert package_field_name(None, "quickbooks", key) == key
    old = {"capabilities": {"sync": {"entry_type": "quickbooks", "field_names": {}}}}
    for key in remaining:
        assert package_field_name(old, "quickbooks", key) == key
    stored = {
        "capabilities": {
            "sync": {"entry_type": "quickbooks", "field_names": dict(remaining)}
        }
    }
    connector = QuickBooksConnector()

    def written(payload):
        entry = connector.to_entry(
            ExternalRecord(external_id="1", payload=payload), stored
        )
        return entry.custom_fields

    invoice = written(
        {
            "_qb_entity": "Invoice",
            "_realm_id": "realm-1",
            "SyncToken": "3",
            "DocNumber": "1001",
            "CustomerRef": {"value": "c1", "name": "Ada"},
            "TotalAmt": 10,
            "Balance": 4,
            "TxnDate": "2024-01-01",
            "DueDate": "2024-02-01",
        }
    )
    assert invoice["origin"] == "quickbooks"
    assert invoice["revision"] == "3"
    assert invoice["synced"]
    assert invoice["kind"] == "Invoice"
    assert invoice["realm"] == "realm-1"
    assert invoice["doc"] == "1001"
    assert invoice["customer"] == "c1"
    assert invoice["customer_name"] == "Ada"
    assert invoice["amount"] == 10
    assert invoice["open_balance"] == 4
    assert invoice["occurred_on"] == "2024-01-01"
    assert invoice["due_on"] == "2024-02-01"
    assert invoice["invoice_status"]
    purchase = written(
        {
            "_qb_entity": "Purchase",
            "_realm_id": "realm-1",
            "PaymentType": "Cash",
            "EntityRef": {"value": "v1", "name": "Supply"},
            "TotalAmt": 8,
            "TxnDate": "2024-03-01",
            "AccountRef": {"name": "Checking"},
            "Line": [
                {"AccountBasedExpenseLineDetail": {"AccountRef": {"name": "Meals"}}}
            ],
        }
    )
    assert purchase["pay_type"] == "cash"
    assert purchase["vendor"] == "v1"
    assert purchase["vendor_name"] == "Supply"
    assert purchase["amount"] == 8
    assert purchase["occurred_on"] == "2024-03-01"
    assert purchase["ledger"] == "Checking"
    assert purchase["expense_category"] == "Meals"
    customer = written(
        {
            "_qb_entity": "Customer",
            "_realm_id": "realm-1",
            "CompanyName": "Ada Co",
            "PrimaryEmailAddr": {"Address": "ada@example.com"},
            "PrimaryPhone": {"FreeFormNumber": "555"},
            "Balance": 1,
        }
    )
    assert customer["company"] == "Ada Co"
    assert customer["mail"] == "ada@example.com"
    assert customer["tel"] == "555"
    assert customer["open_balance"] == 1
    assert customer["link_status"] == "unmatched"
    vendor = written(
        {
            "_qb_entity": "Vendor",
            "_realm_id": "realm-1",
            "CompanyName": "Supply",
            "PrimaryEmailAddr": {"Address": "v@example.com"},
            "Balance": 2,
            "AcctNum": "99",
        }
    )
    assert vendor["company"] == "Supply"
    assert vendor["mail"] == "v@example.com"
    assert vendor["open_balance"] == 2
    assert vendor["acct"] == "99"
    bill = written(
        {
            "_qb_entity": "Bill",
            "_realm_id": "realm-1",
            "DocNumber": "B1",
            "VendorRef": {"value": "v1", "name": "Supply"},
            "TotalAmt": 5,
            "Balance": 5,
            "TxnDate": "2024-04-01",
            "DueDate": "2024-05-01",
        }
    )
    assert bill["bill"] == "B1"
    assert bill["vendor"] == "v1"
    assert bill["vendor_name"] == "Supply"
    assert bill["amount"] == 5
    assert bill["open_balance"] == 5
    assert bill["occurred_on"] == "2024-04-01"
    assert bill["due_on"] == "2024-05-01"
    for fields in (invoice, purchase, customer, vendor, bill):
        for key in remaining:
            assert key not in fields
    foreign = {
        "id": "acme_books",
        "capabilities": {
            "sync": {"entry_type": "ledger", "field_names": dict(remaining)}
        },
    }
    for key, name in remaining.items():
        assert package_field_name(foreign, "acme_books", key) == name
    missing = {"id": "acme_books", "capabilities": {"sync": {"entry_type": "ledger"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    for key in remaining:
        with pytest.raises(ValueError, match="field name"):
            package_field_name(missing, "acme_books", key)


def test_stdio_oauth_rehydrate_waits_for_the_package_token_store(monkeypatch):
    """Boot skips a stdio OAuth package until its declared token store exists."""
    from app.agentive.connectors.mcp_mount import _awaiting_stdio_oauth
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import admitted_manifest

    rescan()
    assert _awaiting_stdio_oauth({"catalog_slug": "quickbooks_mcp", "env": {}}) is True
    assert (
        _awaiting_stdio_oauth(
            {
                "catalog_slug": "quickbooks_mcp",
                "env": {"QUICKBOOKS_TOKEN_STORE_PATH": "/tmp/tokens"},
            }
        )
        is False
    )
    assert (
        _awaiting_stdio_oauth(
            {
                "catalog_slug": "quickbooks_mcp",
                "discovered_tools": [{"name": "query_customers"}],
                "env": {},
            }
        )
        is False
    )
    assert _awaiting_stdio_oauth({"catalog_slug": "gmail", "env": {}}) is False

    real = admitted_manifest

    def fake(package_id: str):
        if package_id == "acme_stdio":
            return {
                "auth": {"type": "oauth2", "token_url": "https://auth.example/token"},
                "capabilities": {
                    "mcp": {
                        "transport": "stdio",
                        "generated_env": ["ACME_TOKEN_STORE_PATH"],
                    }
                },
            }
        return real(package_id)

    monkeypatch.setattr("app.connectors.package_runtime.admitted_manifest", fake)
    assert _awaiting_stdio_oauth({"catalog_slug": "acme_stdio", "env": {}}) is True
    assert (
        _awaiting_stdio_oauth(
            {
                "catalog_slug": "acme_stdio",
                "env": {"ACME_TOKEN_STORE_PATH": "/tmp/acme"},
            }
        )
        is False
    )


def test_label_action_follows_the_package_not_the_gmail_slug():
    """Label reads follow a declared list_labels action."""
    from types import SimpleNamespace

    from app.agentive.api.connectors import _declared_action_url
    from app.api.errors import BadRequestError
    from app.connectors.package_discovery import rescan

    rescan()
    gmail = SimpleNamespace(subclass_slug="gmail", auth_state={}, package_manifest={})
    assert "/gmail/v1/users/me/labels" in _declared_action_url(gmail, "list_labels")
    other = SimpleNamespace(
        subclass_slug="acme_mail",
        auth_state={},
        package_manifest={
            "http": {"base_url": "https://mail.example"},
            "capabilities": {
                "actions": [
                    {
                        "name": "list_labels",
                        "request": {"method": "GET", "path": "/boxes"},
                    }
                ]
            },
        },
    )
    assert _declared_action_url(other, "list_labels") == "https://mail.example/boxes"
    quickbooks = SimpleNamespace(
        subclass_slug="quickbooks", auth_state={}, package_manifest={}
    )
    with pytest.raises(BadRequestError, match="does not declare"):
        _declared_action_url(quickbooks, "list_labels")


def test_gmail_start_uses_the_package_token_url():
    """OAuth start follows a package with Gmail's token URL and refuses another."""
    from types import SimpleNamespace

    from app.agentive.api.connectors import _oauth_start_package
    from app.api.errors import BadRequestError
    from app.connectors.package_discovery import rescan

    rescan()
    gmail = SimpleNamespace(subclass_slug="gmail", auth_state={})
    assert _oauth_start_package(gmail, "gmail") == "gmail"
    same = SimpleNamespace(
        subclass_slug="acme_mail",
        auth_state={"catalog_slug": "google_gmail"},
    )
    assert _oauth_start_package(same, "gmail") == "google_gmail"
    other = SimpleNamespace(subclass_slug="quickbooks", auth_state={})
    with pytest.raises(BadRequestError, match="not this package"):
        _oauth_start_package(other, "gmail")


def test_quickbooks_start_uses_the_package_token_url():
    """Authorize follows a package with QuickBooks' token URL and refuses Gmail."""
    from types import SimpleNamespace

    from app.agentive.api.connectors import _oauth_start_package
    from app.api.errors import BadRequestError
    from app.connectors.package_discovery import rescan

    rescan()
    quickbooks = SimpleNamespace(subclass_slug="quickbooks", auth_state={})
    assert _oauth_start_package(quickbooks, "quickbooks") == "quickbooks"
    same = SimpleNamespace(
        subclass_slug="acme_books",
        auth_state={"catalog_slug": "quickbooks_mcp"},
    )
    assert _oauth_start_package(same, "quickbooks") == "quickbooks_mcp"
    other = SimpleNamespace(subclass_slug="gmail", auth_state={})
    with pytest.raises(BadRequestError, match="not this package"):
        _oauth_start_package(other, "quickbooks")


def test_callback_accepts_a_package_with_the_same_token_url(monkeypatch):
    """The QuickBooks callback matches the token URL, not only the slug."""
    from app.agentive.api.connectors import _shares_oauth_token_url
    from app.connectors.package_discovery import rescan

    rescan()
    assert _shares_oauth_token_url("quickbooks", "quickbooks") is True
    assert _shares_oauth_token_url("quickbooks_mcp", "quickbooks") is True
    assert _shares_oauth_token_url("gmail", "quickbooks") is False

    def fake(package_id: str):
        if package_id in {"quickbooks", "acme_books"}:
            return {
                "type": "oauth2",
                "token_url": "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
            }
        return None

    monkeypatch.setattr("app.connectors.package_runtime.admitted_oauth", fake)
    assert _shares_oauth_token_url("acme_books", "quickbooks") is True
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_oauth", lambda package_id: None
    )
    assert _shares_oauth_token_url("quickbooks", "quickbooks") is False


def test_gmail_callback_accepts_a_package_with_the_same_token_url():
    """The Gmail callback matches the token URL and refuses a different one."""
    from app.agentive.api.connectors import _shares_oauth_token_url
    from app.connectors.package_discovery import rescan

    rescan()
    assert _shares_oauth_token_url("gmail", "gmail") is True
    assert _shares_oauth_token_url("google_gmail", "gmail") is True
    assert _shares_oauth_token_url("quickbooks", "gmail") is False


def test_quickbooks_production_host_comes_from_the_package(monkeypatch):
    """QuickBooks uses the package hosts for production and sandbox."""
    from app.agentive.connectors.quickbooks import _qbo_host
    from app.connectors.package_discovery import rescan

    rescan()
    assert _qbo_host("production") == "https://quickbooks.api.intuit.com"
    assert _qbo_host("sandbox") == "https://sandbox-quickbooks.api.intuit.com"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {
            "http": {
                "hosts": {
                    "production": "https://prod.example",
                    "sandbox": "https://sandbox.example",
                }
            }
        },
    )
    assert _qbo_host("production") == "https://prod.example"
    assert _qbo_host("sandbox") == "https://sandbox.example"
    stored = {
        "http": {
            "hosts": {
                "production": "https://stored-prod.example",
                "sandbox": "https://stored-sandbox.example",
            }
        }
    }
    assert _qbo_host("production", stored) == "https://stored-prod.example"
    assert _qbo_host("sandbox", stored) == "https://stored-sandbox.example"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="does not declare"):
        _qbo_host("production")


def test_package_hosts_come_from_the_package(monkeypatch):
    """A stored host map wins. An older snapshot uses the admitted hosts."""
    from app.connectors.package_runtime import package_http

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {
                "http": {
                    "base_url": "https://admitted.example",
                    "hosts": {
                        "production": "https://prod.example",
                        "sandbox": "https://sandbox.example",
                    },
                }
            }
            if package_id == "acme"
            else None
        ),
    )
    stored = {
        "id": "acme_books",
        "http": {"hosts": {"production": "https://stored.example"}},
    }
    assert (
        package_http("acme", stored)["hosts"]["production"] == "https://stored.example"
    )
    older = {"id": "acme_books", "http": {"base_url": "https://books.example"}}
    assert package_http("acme", older)["hosts"]["sandbox"] == "https://sandbox.example"
    assert package_http("acme", older)["base_url"] == "https://books.example"
    assert "hosts" not in package_http(
        "missing", {"http": {"base_url": "https://x.example"}}
    )
    with pytest.raises(ValueError, match="does not declare named hosts"):
        package_http(
            "acme_books",
            {"http": {"hosts": {"production": "http://stored.example"}}},
        )


def test_github_issue_request_url_comes_from_the_package():
    """Issue sync uses the repository URL declared by the package."""
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_action_url

    rescan()
    assert (
        package_action_url(
            "github_issues", "list_issues", {"owner": "acme", "repo": "widgets"}
        )
        == "https://api.github.com/repos/acme/widgets/issues"
    )


@pytest.mark.asyncio
async def test_github_reads_use_the_package_request(monkeypatch):
    """Issue list goes through the shared package request."""
    from app.agentive.connectors.github_issues import GitHubIssuesConnector
    from app.connectors.package_discovery import rescan

    rescan()
    seen: dict = {}

    async def fake_perform(
        manifest, request, config, secrets, inputs=None, client=None, with_headers=False
    ):
        seen["path"] = request["path"]
        seen["config"] = config
        seen["inputs"] = inputs
        seen["authorization"] = (request.get("headers") or {}).get("Authorization")
        seen["accept"] = (request.get("headers") or {}).get("Accept")
        body = [{"id": 7, "updated_at": "2024-01-01T00:00:00Z"}]
        if with_headers:
            return body, {}
        return body

    monkeypatch.setattr("app.connectors.package_runtime.perform_request", fake_perform)
    connector = GitHubIssuesConnector()
    node = type("Row", (), {})()
    node.auth_state = {"owner": "acme", "repo": "widgets"}
    node.sync_cursor = "2020-01-01T00:00:00Z"
    node.package_config = {}
    node.package_manifest = None
    rows = [row async for row in connector.sync_pull(connector=node)]
    assert rows[0].external_id == "7"
    assert seen["path"] == "/repos/{{config.owner}}/{{config.repo}}/issues"
    assert seen["config"] == {"owner": "acme", "repo": "widgets"}
    assert seen["inputs"]["cursor"] == "2020-01-01T00:00:00Z"
    assert seen["accept"] == "application/vnd.github+json"
    stored = {
        "id": "acme_tasks",
        "http": {"base_url": "https://tasks.example"},
        "auth": {"type": "none"},
        "capabilities": {
            "sync": {"max_pages": 1},
            "actions": [
                {
                    "name": "list_issues",
                    "request": {
                        "method": "GET",
                        "path": "/tasks",
                        "headers": {"Accept": "application/json"},
                    },
                    "output": {
                        "select": "$",
                        "id": "$.id",
                        "updated_at": "$.updated_at",
                        "next_header": "Link",
                        "next_rel": "next",
                        "exclude": "pull_request",
                    },
                }
            ],
        },
    }
    node.package_manifest = stored
    await connector.sync_pull(connector=node).__anext__()
    assert seen["path"] == "/tasks"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_issue",
                    "request": {"method": "GET", "path": "/old"},
                }
            ]
        }
    }
    node.package_manifest = old
    node.auth_state = {"owner": "acme", "repo": "widgets"}
    await connector.sync_pull(connector=node).__anext__()
    assert seen["path"] == "/repos/{{config.owner}}/{{config.repo}}/issues"
    missing = {"id": "acme_tasks", "capabilities": {"actions": []}}
    node.package_manifest = missing
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="list_issues"):
        await connector.sync_pull(connector=node).__anext__()


@pytest.mark.asyncio
async def test_github_next_page_uses_the_guarded_client(monkeypatch):
    """A later issue page uses the guarded client and the package host."""
    from contextlib import contextmanager

    from app.agentive.connectors.github_issues import GitHubIssuesConnector
    from app.connectors.package_discovery import rescan

    rescan()
    checked: list = []
    client = _GuardedLinkClient()

    async def allow(url):
        checked.append(url)

    @contextmanager
    def no_pin(url):
        yield

    async def fake_perform(
        manifest, request, config, secrets, inputs=None, client=None, with_headers=False
    ):
        body = [{"id": 7, "updated_at": "2024-01-01T00:00:00Z"}]
        headers = {"Link": fake_perform.link}
        if with_headers:
            return body, headers
        return body

    fake_perform.link = (
        '<https://api.github.com/repositories/1/issues?page=2>; rel="next"'
    )
    monkeypatch.setattr("app.connectors.package_runtime.perform_request", fake_perform)
    monkeypatch.setattr(
        "app.connectors.package_runtime.validate_outbound_http_url", allow
    )
    monkeypatch.setattr("app.connectors.package_runtime.pin_public_dns", no_pin)
    monkeypatch.setattr(
        "app.connectors.package_runtime.guarded_async_client", lambda **kwargs: client
    )
    connector = GitHubIssuesConnector()
    node = type("Row", (), {})()
    node.auth_state = {"owner": "acme", "repo": "widgets"}
    node.sync_cursor = "2020-01-01T00:00:00Z"
    node.package_config = {}
    node.package_manifest = None
    rows = [row async for row in connector.sync_pull(connector=node)]
    assert [row.external_id for row in rows] == ["7", "8"]
    assert client.calls[0][0] == "GET"
    assert client.calls[0][1] == "https://api.github.com/repositories/1/issues?page=2"
    assert client.calls[0][2]["Accept"] == "application/vnd.github+json"
    assert checked == ["https://api.github.com/repositories/1/issues?page=2"]
    client.calls.clear()
    checked.clear()
    fake_perform.link = '<https://evil.example/issues?page=2>; rel="next"'
    generated = connector.sync_pull(connector=node)
    assert (await generated.__anext__()).external_id == "7"
    with pytest.raises(ValueError, match="not the package host"):
        await generated.__anext__()
    assert client.calls == []
    stored = {
        "http": {"base_url": "https://tasks.example"},
        "capabilities": {
            "sync": {"max_pages": 2},
            "actions": [
                {
                    "name": "list_issues",
                    "request": {
                        "method": "GET",
                        "path": "/tasks",
                        "headers": {"Accept": "application/json"},
                    },
                    "output": {
                        "select": "$",
                        "id": "$.id",
                        "updated_at": "$.updated_at",
                        "next_header": "Link",
                        "next_rel": "next",
                        "exclude": "pull_request",
                    },
                }
            ],
        },
    }
    node.package_manifest = stored
    fake_perform.link = '<https://tasks.example/page/2>; rel="next"'
    rows = [row async for row in connector.sync_pull(connector=node)]
    assert rows[1].external_id == "8"
    assert client.calls[0][1] == "https://tasks.example/page/2"
    client.calls.clear()
    fake_perform.link = (
        '<https://api.github.com/repositories/1/issues?page=2>; rel="next"'
    )
    generated = connector.sync_pull(connector=node)
    await generated.__anext__()
    with pytest.raises(ValueError, match="not the package host"):
        await generated.__anext__()
    stored["http"] = {}
    fake_perform.link = '<https://tasks.example/page/2>; rel="next"'
    generated = connector.sync_pull(connector=node)
    await generated.__anext__()
    with pytest.raises(ValueError, match="does not declare a host"):
        await generated.__anext__()


class _GuardedLinkClient:
    """Stand-in guarded client. It never opens a socket."""

    def __init__(self):
        self.calls = []

    async def request(self, method, url, headers=None, **kwargs):
        self.calls.append((method, url, dict(headers or {})))
        response = type("Response", (), {})()
        response.content = b"[{}]"
        response.headers = {}
        response.raise_for_status = lambda: None
        response.json = lambda: [{"id": 8, "updated_at": "2024-02-01T00:00:00Z"}]
        return response

    async def aclose(self):
        return None


def test_github_issue_query_comes_from_the_package(monkeypatch):
    """Issue query uses the package query. A stored action wins."""
    from app.agentive.connectors.github_issues import _github_issue_params
    from app.connectors.package_discovery import rescan

    rescan()
    assert _github_issue_params(None, None) == {"state": "all", "per_page": "100"}
    assert _github_issue_params(None, "2026-01-01T00:00:00Z")["since"] == (
        "2026-01-01T00:00:00Z"
    )
    old = {"capabilities": {"sync": {"entry_type": "github_issues"}}}
    assert _github_issue_params(old, None)["state"] == "all"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {
                        "method": "GET",
                        "path": "/repos/acme/widgets/issues",
                        "query": {"state": "open", "per_page": "20"},
                    },
                }
            ]
        }
    }
    assert _github_issue_params(stored, None) == {"state": "open", "per_page": "20"}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="does not declare"):
        _github_issue_params(None, None)


def test_github_max_pages_comes_from_the_package(monkeypatch):
    """Issue sync follows the package page cap, then the admitted package."""
    from app.agentive.connectors.github_issues import _github_max_pages
    from app.connectors.package_discovery import rescan

    rescan()
    assert _github_max_pages(None) == 10
    old = {"capabilities": {"sync": {"entry_type": "github_issues"}}}
    assert _github_max_pages(old) == 10
    stored = {"capabilities": {"sync": {"entry_type": "github_issues", "max_pages": 3}}}
    assert _github_max_pages(stored) == 3
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="does not declare"):
        _github_max_pages(None)


def test_github_cursor_field_comes_from_the_package(monkeypatch):
    """The cursor query key comes from the package template."""
    from app.agentive.connectors.github_issues import _github_issue_params
    from app.connectors.package_discovery import rescan

    rescan()
    assert "since" not in _github_issue_params(None, None)
    assert _github_issue_params(None, "t1")["since"] == "t1"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {
                        "method": "GET",
                        "path": "/repos/acme/widgets/issues",
                        "query": {"state": "all", "per_page": "100"},
                    },
                }
            ]
        }
    }
    assert _github_issue_params(old, "t2")["since"] == "t2"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {
                        "method": "GET",
                        "path": "/repos/acme/widgets/issues",
                        "query": {"state": "open", "after": "{{cursor}}"},
                    },
                }
            ]
        }
    }
    params = _github_issue_params(stored, "t3")
    assert params["after"] == "t3"
    assert "since" not in params
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="cursor field"):
        _github_issue_params(old, "t4")


def test_github_issue_accept_comes_from_the_package(monkeypatch):
    """Issue sync uses the package Accept header. A stored action wins."""
    from app.agentive.connectors.github_issues import _github_issue_headers
    from app.connectors.package_discovery import rescan

    rescan()
    assert _github_issue_headers(None, "")["Accept"] == "application/vnd.github+json"
    assert "Authorization" not in _github_issue_headers(None, "")
    assert _github_issue_headers(None, "tok")["Authorization"] == "Bearer tok"
    old = {"capabilities": {"sync": {"entry_type": "github_issues"}}}
    assert _github_issue_headers(old, "")["Accept"] == "application/vnd.github+json"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {
                        "method": "GET",
                        "path": "/repos/acme/widgets/issues",
                        "headers": {"Accept": "application/vnd.github.raw+json"},
                    },
                }
            ]
        }
    }
    assert (
        _github_issue_headers(stored, "")["Accept"] == "application/vnd.github.raw+json"
    )
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="does not declare"):
        _github_issue_headers(None, "")


def test_github_issue_selector_comes_from_the_package(monkeypatch):
    """Issue rows are selected by the package output path."""
    from app.agentive.connectors.github_issues import _github_issue_select
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output

    rescan()
    assert _github_issue_select(None) == "$"
    assert select_output([{"id": 1}], _github_issue_select(None)) == [{"id": 1}]
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/repos/acme/widgets/issues"},
                }
            ]
        }
    }
    assert _github_issue_select(old) == "$"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/custom/issues"},
                    "output": {"select": "$.issues"},
                }
            ]
        }
    }
    assert _github_issue_select(stored) == "$.issues"
    assert select_output({"issues": [{"id": 9}]}, "$.issues") == [{"id": 9}]
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="issue selector"):
        _github_issue_select(old)


def test_github_issue_id_comes_from_the_package(monkeypatch):
    """Each issue id is read from the package output path."""
    from app.agentive.connectors.github_issues import _github_issue_id
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output

    rescan()
    assert select_output({"id": 7}, _github_issue_id(None)) == 7
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/repos/acme/widgets/issues"},
                    "output": {"select": "$"},
                }
            ]
        }
    }
    assert _github_issue_id(old) == "$.id"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/custom/issues"},
                    "output": {"select": "$.issues", "id": "$.number"},
                }
            ]
        }
    }
    assert _github_issue_id(stored) == "$.number"
    assert select_output({"number": 9}, _github_issue_id(stored)) == 9
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="issue id"):
        _github_issue_id(old)


def test_github_issue_timestamp_comes_from_the_package(monkeypatch):
    """Each issue timestamp is read from the package output path."""
    from app.agentive.connectors.github_issues import _github_issue_updated_at
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output

    rescan()
    assert (
        select_output(
            {"updated_at": "2020-01-01T00:00:00Z"}, _github_issue_updated_at(None)
        )
        == "2020-01-01T00:00:00Z"
    )
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/repos/acme/widgets/issues"},
                    "output": {"select": "$", "id": "$.id"},
                }
            ]
        }
    }
    assert _github_issue_updated_at(old) == "$.updated_at"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/custom/issues"},
                    "output": {"select": "$.issues", "updated_at": "$.closed_at"},
                }
            ]
        }
    }
    assert _github_issue_updated_at(stored) == "$.closed_at"
    assert select_output(
        {"closed_at": "2024-02-02"}, _github_issue_updated_at(stored)
    ) == ("2024-02-02")
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="issue timestamp"):
        _github_issue_updated_at(old)


def test_github_next_header_comes_from_the_package(monkeypatch):
    """The next page is read from the package response header."""
    from app.agentive.connectors.github_issues import _github_next_header
    from app.connectors.package_discovery import rescan

    rescan()
    assert _github_next_header(None) == "Link"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/repos/acme/widgets/issues"},
                    "output": {"select": "$", "id": "$.id"},
                }
            ]
        }
    }
    assert _github_next_header(old) == "Link"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/custom/issues"},
                    "output": {"select": "$.issues", "next_header": "X-Next"},
                }
            ]
        }
    }
    assert _github_next_header(stored) == "X-Next"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="next page header"):
        _github_next_header(old)


def test_github_issue_title_comes_from_the_package(monkeypatch):
    """Each issue title is read from the package output path."""
    from app.agentive.connectors.github_issues import (
        GitHubIssuesConnector,
        _github_issue_title,
    )
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output
    from app.services.connectors import ExternalRecord

    rescan()
    assert select_output({"title": "Bug"}, _github_issue_title(None)) == "Bug"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/repos/acme/widgets/issues"},
                    "output": {"select": "$", "id": "$.id"},
                }
            ]
        }
    }
    assert _github_issue_title(old) == "$.title"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/custom/issues"},
                    "output": {"select": "$.issues", "title": "$.name"},
                }
            ]
        }
    }
    assert _github_issue_title(stored) == "$.name"
    record = ExternalRecord(external_id="1", payload={"name": "Widget"})
    entry = GitHubIssuesConnector().to_entry(record, stored)
    assert entry.title == "Widget"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="issue title"):
        _github_issue_title(old)


def test_github_issue_body_comes_from_the_package(monkeypatch):
    """Each issue body is read from the package output path."""
    from app.agentive.connectors.github_issues import (
        GitHubIssuesConnector,
        _github_issue_body,
    )
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output
    from app.services.connectors import ExternalRecord

    rescan()
    assert select_output({"body": "details"}, _github_issue_body(None)) == "details"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/repos/acme/widgets/issues"},
                    "output": {"select": "$", "title": "$.title"},
                }
            ]
        }
    }
    assert _github_issue_body(old) == "$.body"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/custom/issues"},
                    "output": {"select": "$.issues", "body": "$.text"},
                }
            ]
        }
    }
    assert _github_issue_body(stored) == "$.text"
    record = ExternalRecord(external_id="1", payload={"text": "hello"})
    entry = GitHubIssuesConnector().to_entry(record, stored)
    assert entry.body == "hello"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="issue body"):
        _github_issue_body(old)


def test_github_issue_status_comes_from_the_package(monkeypatch):
    """Each issue status is read from the package output path."""
    from app.agentive.connectors.github_issues import (
        GitHubIssuesConnector,
        _github_issue_status,
    )
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output
    from app.services.connectors import ExternalRecord

    rescan()
    assert select_output({"state": "open"}, _github_issue_status(None)) == "open"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/repos/acme/widgets/issues"},
                    "output": {"select": "$", "body": "$.body"},
                }
            ]
        }
    }
    assert _github_issue_status(old) == "$.state"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/custom/issues"},
                    "output": {"select": "$.issues", "status": "$.phase"},
                }
            ]
        }
    }
    assert _github_issue_status(stored) == "$.phase"
    record = ExternalRecord(external_id="1", payload={"phase": "closed"})
    entry = GitHubIssuesConnector().to_entry(record, stored)
    assert entry.custom_fields["state"] == "closed"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="issue status"):
        _github_issue_status(old)


def test_github_issue_number_comes_from_the_package(monkeypatch):
    """Each issue number is read from the package output path."""
    from app.agentive.connectors.github_issues import (
        GitHubIssuesConnector,
        _github_issue_number,
    )
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output
    from app.services.connectors import ExternalRecord

    rescan()
    assert select_output({"number": 7}, _github_issue_number(None)) == 7
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/repos/acme/widgets/issues"},
                    "output": {"select": "$", "status": "$.state"},
                }
            ]
        }
    }
    assert _github_issue_number(old) == "$.number"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/custom/issues"},
                    "output": {"select": "$.issues", "number": "$.count"},
                }
            ]
        }
    }
    assert _github_issue_number(stored) == "$.count"
    record = ExternalRecord(external_id="1", payload={"count": 9})
    entry = GitHubIssuesConnector().to_entry(record, stored)
    assert entry.custom_fields["issue_number"] == 9
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="issue number"):
        _github_issue_number(old)


def test_github_issue_url_comes_from_the_package(monkeypatch):
    """Each issue link is read from the package output path."""
    from app.agentive.connectors.github_issues import (
        GitHubIssuesConnector,
        _github_issue_url,
    )
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output
    from app.services.connectors import ExternalRecord

    rescan()
    assert (
        select_output({"html_url": "https://example.test/1"}, _github_issue_url(None))
        == "https://example.test/1"
    )
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/repos/acme/widgets/issues"},
                    "output": {"select": "$", "number": "$.number"},
                }
            ]
        }
    }
    assert _github_issue_url(old) == "$.html_url"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/custom/issues"},
                    "output": {"select": "$.issues", "url": "$.link"},
                }
            ]
        }
    }
    assert _github_issue_url(stored) == "$.link"
    record = ExternalRecord(external_id="1", payload={"link": "https://example.test/9"})
    entry = GitHubIssuesConnector().to_entry(record, stored)
    assert entry.custom_fields["github_url"] == "https://example.test/9"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="issue link"):
        _github_issue_url(old)


def test_github_record_type_comes_from_the_package(monkeypatch):
    """Issue entries use the package record type, for any package id."""
    from app.agentive.connectors.github_issues import GitHubIssuesConnector
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_record_type
    from app.services.connectors import ExternalRecord

    rescan()
    assert package_record_type(None, "github_issues") == "github_issue"
    old = {"capabilities": {"sync": {"entry_type": "github_issues"}}}
    assert package_record_type(old, "github_issues") == "github_issue"
    stored = {
        "capabilities": {
            "sync": {"entry_type": "github_issues", "record_type": "ticket"}
        }
    }
    entry = GitHubIssuesConnector().to_entry(
        ExternalRecord(external_id="1", payload={"title": "Hello", "id": 1}),
        stored,
    )
    assert entry.entry_type_key == "ticket"
    foreign = {
        "id": "acme_tasks",
        "capabilities": {"sync": {"entry_type": "task", "record_type": "work_item"}},
    }
    assert package_record_type(foreign, "acme_tasks") == "work_item"
    missing = {"id": "acme_tasks", "capabilities": {"sync": {"entry_type": "task"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="record type"):
        package_record_type(missing, "acme_tasks")


def test_github_conflict_policy_comes_from_the_package(monkeypatch):
    """GitHub conflict policy comes from the package, for any package id."""
    from app.agentive.connectors.github_issues import GitHubIssuesConnector
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_conflict_policy

    rescan()
    assert package_conflict_policy(None, "github_issues") == "last_write_wins"
    assert GitHubIssuesConnector().conflict_policy == "last_write_wins"
    old = {"capabilities": {"sync": {"entry_type": "github_issues"}}}
    assert package_conflict_policy(old, "github_issues") == "last_write_wins"
    stored = {
        "capabilities": {
            "sync": {
                "entry_type": "github_issues",
                "conflict_policy": "manual_resolve",
            }
        }
    }
    assert package_conflict_policy(stored, "github_issues") == "manual_resolve"
    foreign = {
        "id": "acme_tasks",
        "capabilities": {
            "sync": {"entry_type": "task", "conflict_policy": "mirror_only"}
        },
    }
    assert package_conflict_policy(foreign, "acme_tasks") == "mirror_only"
    missing = {"id": "acme_tasks", "capabilities": {"sync": {"entry_type": "task"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="conflict policy"):
        package_conflict_policy(missing, "acme_tasks")


def test_github_issue_number_field_comes_from_the_package(monkeypatch):
    """Issue numbers are written under the package destination field name."""
    from app.agentive.connectors.github_issues import GitHubIssuesConnector
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_field_name
    from app.services.connectors import ExternalRecord

    rescan()
    assert package_field_name(None, "github_issues", "number") == "issue_number"
    old = {"capabilities": {"sync": {"entry_type": "github_issues"}}}
    assert package_field_name(old, "github_issues", "number") == "issue_number"
    stored = {
        "capabilities": {
            "sync": {
                "entry_type": "github_issues",
                "field_names": {"number": "ticket_number"},
            }
        }
    }
    entry = GitHubIssuesConnector().to_entry(
        ExternalRecord(external_id="1", payload={"number": 9}),
        stored,
    )
    assert entry.custom_fields["ticket_number"] == 9
    assert "issue_number" not in entry.custom_fields
    foreign = {
        "id": "acme_tasks",
        "capabilities": {
            "sync": {
                "entry_type": "task",
                "field_names": {"number": "work_number"},
            }
        },
    }
    assert package_field_name(foreign, "acme_tasks", "number") == "work_number"
    missing = {"id": "acme_tasks", "capabilities": {"sync": {"entry_type": "task"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="field name"):
        package_field_name(missing, "acme_tasks", "number")


def test_github_issue_status_field_comes_from_the_package(monkeypatch):
    """Issue status is written under the package destination field name."""
    from app.agentive.connectors.github_issues import GitHubIssuesConnector
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_field_name
    from app.services.connectors import ExternalRecord

    rescan()
    assert package_field_name(None, "github_issues", "status") == "state"
    old = {
        "capabilities": {
            "sync": {
                "entry_type": "github_issues",
                "field_names": {"number": "issue_number"},
            }
        }
    }
    assert package_field_name(old, "github_issues", "status") == "state"
    stored = {
        "capabilities": {
            "sync": {
                "entry_type": "github_issues",
                "field_names": {"status": "condition"},
            }
        }
    }
    entry = GitHubIssuesConnector().to_entry(
        ExternalRecord(external_id="1", payload={"state": "open"}),
        stored,
    )
    assert entry.custom_fields["condition"] == "open"
    assert "state" not in entry.custom_fields
    foreign = {
        "id": "acme_tasks",
        "capabilities": {
            "sync": {
                "entry_type": "task",
                "field_names": {"status": "condition"},
            }
        },
    }
    assert package_field_name(foreign, "acme_tasks", "status") == "condition"
    missing = {"id": "acme_tasks", "capabilities": {"sync": {"entry_type": "task"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="field name"):
        package_field_name(missing, "acme_tasks", "status")


def test_github_reporter_field_comes_from_the_package(monkeypatch):
    """Reporters are written under the package destination field name."""
    from app.agentive.connectors.github_issues import GitHubIssuesConnector
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_field_name
    from app.services.connectors import ExternalRecord

    rescan()
    assert package_field_name(None, "github_issues", "actor") == "reporter"
    old = {
        "capabilities": {
            "sync": {
                "entry_type": "github_issues",
                "field_names": {"status": "state"},
            }
        }
    }
    assert package_field_name(old, "github_issues", "actor") == "reporter"
    stored = {
        "capabilities": {
            "sync": {
                "entry_type": "github_issues",
                "field_names": {"actor": "author"},
            }
        }
    }
    entry = GitHubIssuesConnector().to_entry(
        ExternalRecord(
            external_id="1",
            payload={"user": {"login": "ada"}},
        ),
        stored,
    )
    assert entry.custom_fields["author"] == "ada"
    assert "reporter" not in entry.custom_fields
    foreign = {
        "id": "acme_tasks",
        "capabilities": {
            "sync": {
                "entry_type": "task",
                "field_names": {"actor": "author"},
            }
        },
    }
    assert package_field_name(foreign, "acme_tasks", "actor") == "author"
    missing = {"id": "acme_tasks", "capabilities": {"sync": {"entry_type": "task"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="field name"):
        package_field_name(missing, "acme_tasks", "actor")


def test_github_assignee_field_comes_from_the_package(monkeypatch):
    """Assignees are written under the package destination field name."""
    from app.agentive.connectors.github_issues import GitHubIssuesConnector
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_field_name
    from app.services.connectors import ExternalRecord

    rescan()
    assert package_field_name(None, "github_issues", "assignee") == "assignee"
    old = {
        "capabilities": {
            "sync": {
                "entry_type": "github_issues",
                "field_names": {"actor": "reporter"},
            }
        }
    }
    assert package_field_name(old, "github_issues", "assignee") == "assignee"
    stored = {
        "capabilities": {
            "sync": {
                "entry_type": "github_issues",
                "field_names": {"assignee": "owner"},
            }
        }
    }
    entry = GitHubIssuesConnector().to_entry(
        ExternalRecord(
            external_id="1",
            payload={"assignee": {"login": "bea"}},
        ),
        stored,
    )
    assert entry.custom_fields["owner"] == "bea"
    assert "assignee" not in entry.custom_fields
    foreign = {
        "id": "acme_tasks",
        "capabilities": {
            "sync": {
                "entry_type": "task",
                "field_names": {"assignee": "owner"},
            }
        },
    }
    assert package_field_name(foreign, "acme_tasks", "assignee") == "owner"
    missing = {"id": "acme_tasks", "capabilities": {"sync": {"entry_type": "task"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="field name"):
        package_field_name(missing, "acme_tasks", "assignee")


def test_github_url_field_comes_from_the_package(monkeypatch):
    """Issue links are written under the package destination field name."""
    from app.agentive.connectors.github_issues import GitHubIssuesConnector
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_field_name
    from app.services.connectors import ExternalRecord

    rescan()
    assert package_field_name(None, "github_issues", "url") == "github_url"
    old = {
        "capabilities": {
            "sync": {
                "entry_type": "github_issues",
                "field_names": {"assignee": "assignee"},
            }
        }
    }
    assert package_field_name(old, "github_issues", "url") == "github_url"
    stored = {
        "capabilities": {
            "sync": {
                "entry_type": "github_issues",
                "field_names": {"url": "link"},
            }
        }
    }
    entry = GitHubIssuesConnector().to_entry(
        ExternalRecord(
            external_id="1",
            payload={"html_url": "https://example.test/1"},
        ),
        stored,
    )
    assert entry.custom_fields["link"] == "https://example.test/1"
    assert "github_url" not in entry.custom_fields
    foreign = {
        "id": "acme_tasks",
        "capabilities": {
            "sync": {
                "entry_type": "task",
                "field_names": {"url": "link"},
            }
        },
    }
    assert package_field_name(foreign, "acme_tasks", "url") == "link"
    missing = {"id": "acme_tasks", "capabilities": {"sync": {"entry_type": "task"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="field name"):
        package_field_name(missing, "acme_tasks", "url")


def test_github_entry_timestamp_uses_the_package_path(monkeypatch):
    """Issue entries read external_updated_at from the package timestamp path."""
    from app.agentive.connectors.github_issues import (
        GitHubIssuesConnector,
        _github_issue_updated_at,
    )
    from app.connectors.package_discovery import rescan
    from app.services.connectors import ExternalRecord

    rescan()
    assert _github_issue_updated_at(None) == "$.updated_at"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/repos/acme/widgets/issues"},
                    "output": {"select": "$", "id": "$.id"},
                }
            ]
        }
    }
    assert _github_issue_updated_at(old) == "$.updated_at"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/custom/issues"},
                    "output": {"select": "$", "updated_at": "$.closed_at"},
                }
            ]
        }
    }
    record = ExternalRecord(
        external_id="1",
        payload={"updated_at": "2020-01-01", "closed_at": "2024-02-02"},
    )
    entry = GitHubIssuesConnector().to_entry(record, stored)
    assert entry.external_updated_at == "2024-02-02"
    fallback = GitHubIssuesConnector().to_entry(record, old)
    assert fallback.external_updated_at == "2020-01-01"
    foreign = {
        "id": "acme_tasks",
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/tasks"},
                    "output": {"updated_at": "$.closed_at"},
                }
            ]
        },
    }
    assert _github_issue_updated_at(foreign) == "$.closed_at"
    missing = {"id": "acme_tasks", "capabilities": {"sync": {"entry_type": "task"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="issue timestamp"):
        _github_issue_updated_at(missing)


def test_github_issue_reporter_comes_from_the_package(monkeypatch):
    """Each issue reporter is read from the package output path."""
    from app.agentive.connectors.github_issues import (
        GitHubIssuesConnector,
        _github_issue_actor,
    )
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output
    from app.services.connectors import ExternalRecord

    rescan()
    assert select_output({"user": {"login": "ada"}}, _github_issue_actor(None)) == "ada"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/repos/acme/widgets/issues"},
                    "output": {"select": "$", "url": "$.html_url"},
                }
            ]
        }
    }
    assert _github_issue_actor(old) == "$.user.login"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/custom/issues"},
                    "output": {"select": "$.issues", "actor": "$.owner.login"},
                }
            ]
        }
    }
    assert _github_issue_actor(stored) == "$.owner.login"
    record = ExternalRecord(external_id="1", payload={"owner": {"login": "ada"}})
    entry = GitHubIssuesConnector().to_entry(record, stored)
    assert entry.custom_fields["reporter"] == "ada"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="reporter"):
        _github_issue_actor(old)


def test_github_issue_assignee_comes_from_the_package(monkeypatch):
    """Each issue assignee is read from the package output path."""
    from app.agentive.connectors.github_issues import (
        GitHubIssuesConnector,
        _github_issue_assignee,
    )
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output
    from app.services.connectors import ExternalRecord

    rescan()
    assert (
        select_output({"assignee": {"login": "bey"}}, _github_issue_assignee(None))
        == "bey"
    )
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/repos/acme/widgets/issues"},
                    "output": {"select": "$", "actor": "$.user.login"},
                }
            ]
        }
    }
    assert _github_issue_assignee(old) == "$.assignee.login"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/custom/issues"},
                    "output": {"select": "$.issues", "assignee": "$.worker.login"},
                }
            ]
        }
    }
    assert _github_issue_assignee(stored) == "$.worker.login"
    record = ExternalRecord(external_id="1", payload={"worker": {"login": "bey"}})
    entry = GitHubIssuesConnector().to_entry(record, stored)
    assert entry.custom_fields["assignee"] == "bey"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="assignee"):
        _github_issue_assignee(old)


def test_github_issue_tags_come_from_the_package(monkeypatch):
    """Issue tags are read from the package output path."""
    from app.agentive.connectors.github_issues import (
        GitHubIssuesConnector,
        _github_issue_tags,
    )
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output
    from app.services.connectors import ExternalRecord

    rescan()
    assert select_output({"labels": [{"name": "bug"}]}, _github_issue_tags(None)) == [
        "bug"
    ]
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/repos/acme/widgets/issues"},
                    "output": {"select": "$", "assignee": "$.assignee.login"},
                }
            ]
        }
    }
    assert _github_issue_tags(old) == "$.labels.name"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/custom/issues"},
                    "output": {"select": "$.issues", "tags": "$.marks.name"},
                }
            ]
        }
    }
    assert _github_issue_tags(stored) == "$.marks.name"
    record = ExternalRecord(
        external_id="1",
        payload={"marks": [{"name": "bug"}, {"name": ""}]},
    )
    entry = GitHubIssuesConnector().to_entry(record, stored)
    assert entry.tags == ["bug"]
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="issue tags"):
        _github_issue_tags(old)


def test_github_next_relation_comes_from_the_package(monkeypatch):
    """The next-page link relation comes from the package."""
    from app.agentive.connectors.github_issues import (
        _github_next_rel,
        _parse_next_link,
    )
    from app.connectors.package_discovery import rescan

    rescan()
    assert _github_next_rel(None) == "next"
    link = (
        '<https://api.github.com/issues?page=2>; rel="next", '
        '<https://api.github.com/issues?page=5>; rel="last"'
    )
    assert _parse_next_link(link, _github_next_rel(None)) == (
        "https://api.github.com/issues?page=2"
    )
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/repos/a/b/issues"},
                    "output": {"select": "$", "next_header": "Link"},
                }
            ]
        }
    }
    assert _github_next_rel(old) == "next"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/custom/issues"},
                    "output": {"select": "$.issues", "next_rel": "following"},
                }
            ]
        }
    }
    assert _github_next_rel(stored) == "following"
    custom = '<https://example.test/page/2>; rel="following"'
    assert _parse_next_link(custom, _github_next_rel(stored)) == (
        "https://example.test/page/2"
    )
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="next relation"):
        _github_next_rel(old)


def test_github_excluded_record_key_comes_from_the_package(monkeypatch):
    """Rows carrying the package exclude key are the ones the pull drops."""
    from app.agentive.connectors.github_issues import _github_exclude_key
    from app.connectors.package_discovery import rescan

    rescan()
    assert _github_exclude_key(None) == "pull_request"
    assert _github_exclude_key(None) in {"pull_request": {"url": "x"}}
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/repos/a/b/issues"},
                    "output": {"select": "$", "next_rel": "next"},
                }
            ]
        }
    }
    assert _github_exclude_key(old) == "pull_request"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_issues",
                    "request": {"method": "GET", "path": "/custom/issues"},
                    "output": {"select": "$.issues", "exclude": "draft"},
                }
            ]
        }
    }
    assert _github_exclude_key(stored) == "draft"
    assert _github_exclude_key(stored) in {"draft": {}}
    assert _github_exclude_key(stored) not in {"pull_request": {}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="excluded record key"):
        _github_exclude_key(old)


@pytest.mark.asyncio
async def test_declared_mcp_oauth_starts_discovery_without_a_401(monkeypatch):
    """A package that requires MCP OAuth starts discovery even when the probe is 200."""
    from types import SimpleNamespace

    from app.agentive.connectors import mcp_adapter, mcp_oauth

    seen = {}

    async def allow(url):
        return None

    async def probe(url, headers):
        return SimpleNamespace(status_code=200, needs_oauth=False, response=None)

    async def start(url, *, probe_response, redirect_uri):
        seen["started"] = url
        return {"status": "pending"}

    connector = SimpleNamespace(
        id="c",
        auth_state={},
        subclass_slug="",
        workspace_id="",
        connection_mode="",
        label="",
    )

    async def create(**kwargs):
        return connector

    async def save(self):
        return None

    connector.save = save.__get__(connector, SimpleNamespace)
    monkeypatch.setattr(mcp_adapter, "validate_outbound_http_url", allow)
    monkeypatch.setattr(mcp_oauth, "probe_mcp_authorization", probe)
    monkeypatch.setattr(mcp_oauth, "start_mcp_oauth_session", start)
    monkeypatch.setattr(
        "app.agentive.services.connector_registry_node.create_connector", create
    )
    monkeypatch.setattr(mcp_oauth, "sign_mcp_oauth_state", lambda user, cid: "state")
    monkeypatch.setattr(mcp_adapter, "store_auth_state", lambda c, auth: None)

    async def health(*args, **kwargs):
        return None

    monkeypatch.setattr(mcp_adapter, "_set_health", health)
    result = await mcp_adapter.mount_mcp_connector(
        owner_id="u",
        workspace_id="ws",
        url="https://mcp.example/mcp",
        require_oauth=True,
    )
    assert seen["started"] == "https://mcp.example/mcp"
    assert result is connector


def test_notion_package_requires_mcp_oauth():
    """A package that declares mcp_oauth asks install to start discovery."""
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_requires_mcp_oauth

    rescan()
    assert package_requires_mcp_oauth("notion") is True
    assert package_requires_mcp_oauth("gmail") is False


def test_gmail_conflict_policy_comes_from_the_package(monkeypatch):
    """Gmail conflict policy comes from the package, for any package id."""
    from app.agentive.connectors.gmail import GmailConnector
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_conflict_policy

    rescan()
    assert package_conflict_policy(None, "gmail") == "mirror_only"
    assert GmailConnector().conflict_policy == "mirror_only"
    old = {
        "capabilities": {"sync": {"entry_type": "gmail", "record_type": "email_thread"}}
    }
    assert package_conflict_policy(old, "gmail") == "mirror_only"
    stored = {
        "capabilities": {
            "sync": {"entry_type": "gmail", "conflict_policy": "manual_resolve"}
        }
    }
    assert package_conflict_policy(stored, "gmail") == "manual_resolve"
    foreign = {
        "id": "acme_mail",
        "capabilities": {
            "sync": {"entry_type": "note", "conflict_policy": "last_write_wins"}
        },
    }
    assert package_conflict_policy(foreign, "acme_mail") == "last_write_wins"
    missing = {"id": "acme_mail", "capabilities": {"sync": {"entry_type": "note"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="conflict policy"):
        package_conflict_policy(missing, "acme_mail")


def test_gmail_title_prefix_comes_from_the_package(monkeypatch):
    """An empty Gmail subject uses the package title prefix."""
    from app.agentive.connectors.gmail import GmailConnector
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_title_prefix
    from app.services.connectors import ExternalRecord

    rescan()
    assert package_title_prefix(None, "gmail") == "Email thread"
    old = {
        "capabilities": {"sync": {"entry_type": "gmail", "record_type": "email_thread"}}
    }
    assert package_title_prefix(old, "gmail") == "Email thread"
    stored = {
        "capabilities": {"sync": {"entry_type": "gmail", "title_prefix": "Mailbox"}}
    }
    entry = GmailConnector().to_entry(
        ExternalRecord(external_id="t", payload={"id": "t"}),
        stored,
    )
    assert entry.title == "Mailbox t"
    foreign = {
        "id": "acme_mail",
        "capabilities": {"sync": {"entry_type": "note", "title_prefix": "Note"}},
    }
    assert package_title_prefix(foreign, "acme_mail") == "Note"
    missing = {"id": "acme_mail", "capabilities": {"sync": {"entry_type": "note"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="title prefix"):
        package_title_prefix(missing, "acme_mail")


def test_gmail_subject_field_comes_from_the_package(monkeypatch):
    """Gmail subjects are written under the package destination field name."""
    from app.agentive.connectors.gmail import GmailConnector
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_field_name
    from app.services.connectors import ExternalRecord

    rescan()
    assert package_field_name(None, "gmail", "subject") == "subject"
    old = {
        "capabilities": {
            "sync": {"entry_type": "gmail", "title_prefix": "Email thread"}
        }
    }
    assert package_field_name(old, "gmail", "subject") == "subject"
    stored = {
        "capabilities": {
            "sync": {"entry_type": "gmail", "field_names": {"subject": "topic"}}
        }
    }
    entry = GmailConnector().to_entry(
        ExternalRecord(
            external_id="t",
            payload={
                "id": "t",
                "messages": [
                    {"payload": {"headers": [{"name": "subject", "value": "Hello"}]}}
                ],
            },
        ),
        stored,
    )
    assert entry.custom_fields["topic"] == "Hello"
    assert "subject" not in entry.custom_fields
    foreign = {
        "id": "acme_mail",
        "capabilities": {
            "sync": {"entry_type": "note", "field_names": {"subject": "topic"}}
        },
    }
    assert package_field_name(foreign, "acme_mail", "subject") == "topic"
    missing = {"id": "acme_mail", "capabilities": {"sync": {"entry_type": "note"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="field name"):
        package_field_name(missing, "acme_mail", "subject")


def test_gmail_participants_field_comes_from_the_package(monkeypatch):
    """Gmail participant lists are written under the package field name."""
    from app.agentive.connectors.gmail import GmailConnector
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_field_name
    from app.services.connectors import ExternalRecord

    rescan()
    assert package_field_name(None, "gmail", "participants") == "participant_emails"
    old = {
        "capabilities": {
            "sync": {"entry_type": "gmail", "field_names": {"subject": "subject"}}
        }
    }
    assert package_field_name(old, "gmail", "participants") == "participant_emails"
    stored = {
        "capabilities": {
            "sync": {"entry_type": "gmail", "field_names": {"participants": "people"}}
        }
    }
    entry = GmailConnector().to_entry(
        ExternalRecord(
            external_id="t",
            payload={
                "id": "t",
                "messages": [
                    {
                        "payload": {
                            "headers": [{"name": "from", "value": "ada@example.com"}]
                        }
                    }
                ],
            },
        ),
        stored,
    )
    assert entry.custom_fields["people"] == ["ada@example.com"]
    assert "participant_emails" not in entry.custom_fields
    foreign = {
        "id": "acme_mail",
        "capabilities": {
            "sync": {"entry_type": "note", "field_names": {"participants": "people"}}
        },
    }
    assert package_field_name(foreign, "acme_mail", "participants") == "people"
    missing = {"id": "acme_mail", "capabilities": {"sync": {"entry_type": "note"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="field name"):
        package_field_name(missing, "acme_mail", "participants")


def test_gmail_message_count_field_comes_from_the_package(monkeypatch):
    """Gmail message counts are written under the package field name."""
    from app.agentive.connectors.gmail import GmailConnector
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_field_name
    from app.services.connectors import ExternalRecord

    rescan()
    assert package_field_name(None, "gmail", "message_count") == "message_count"
    old = {
        "capabilities": {
            "sync": {
                "entry_type": "gmail",
                "field_names": {"participants": "participant_emails"},
            }
        }
    }
    assert package_field_name(old, "gmail", "message_count") == "message_count"
    stored = {
        "capabilities": {
            "sync": {
                "entry_type": "gmail",
                "field_names": {"message_count": "count"},
            }
        }
    }
    entry = GmailConnector().to_entry(
        ExternalRecord(
            external_id="t",
            payload={"id": "t", "messages": [{}, {}]},
        ),
        stored,
    )
    assert entry.custom_fields["count"] == 2
    assert "message_count" not in entry.custom_fields
    foreign = {
        "id": "acme_mail",
        "capabilities": {
            "sync": {"entry_type": "note", "field_names": {"message_count": "count"}}
        },
    }
    assert package_field_name(foreign, "acme_mail", "message_count") == "count"
    missing = {"id": "acme_mail", "capabilities": {"sync": {"entry_type": "note"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="field name"):
        package_field_name(missing, "acme_mail", "message_count")


def test_gmail_first_message_field_comes_from_the_package(monkeypatch):
    """Gmail first-message times are written under the package field name."""
    from app.agentive.connectors.gmail import GmailConnector
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_field_name
    from app.services.connectors import ExternalRecord

    rescan()
    assert package_field_name(None, "gmail", "first_message_at") == "first_message_at"
    old = {
        "capabilities": {
            "sync": {
                "entry_type": "gmail",
                "field_names": {"message_count": "message_count"},
            }
        }
    }
    assert package_field_name(old, "gmail", "first_message_at") == "first_message_at"
    stored = {
        "capabilities": {
            "sync": {
                "entry_type": "gmail",
                "field_names": {"first_message_at": "opened"},
            }
        }
    }
    entry = GmailConnector().to_entry(
        ExternalRecord(
            external_id="t",
            payload={"id": "t", "messages": [{"internalDate": "1000"}]},
        ),
        stored,
    )
    assert entry.custom_fields["opened"] == "1970-01-01T00:00:01+00:00"
    assert "first_message_at" not in entry.custom_fields
    foreign = {
        "id": "acme_mail",
        "capabilities": {
            "sync": {
                "entry_type": "note",
                "field_names": {"first_message_at": "opened"},
            }
        },
    }
    assert package_field_name(foreign, "acme_mail", "first_message_at") == "opened"
    missing = {"id": "acme_mail", "capabilities": {"sync": {"entry_type": "note"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="field name"):
        package_field_name(missing, "acme_mail", "first_message_at")


def test_gmail_last_message_field_comes_from_the_package(monkeypatch):
    """Gmail last-message times are written under the package field name."""
    from app.agentive.connectors.gmail import GmailConnector
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_field_name
    from app.services.connectors import ExternalRecord

    rescan()
    assert package_field_name(None, "gmail", "last_message_at") == "last_message_at"
    old = {
        "capabilities": {
            "sync": {
                "entry_type": "gmail",
                "field_names": {"first_message_at": "first_message_at"},
            }
        }
    }
    assert package_field_name(old, "gmail", "last_message_at") == "last_message_at"
    stored = {
        "capabilities": {
            "sync": {
                "entry_type": "gmail",
                "field_names": {"last_message_at": "closed"},
            }
        }
    }
    entry = GmailConnector().to_entry(
        ExternalRecord(
            external_id="t",
            payload={
                "id": "t",
                "messages": [
                    {"internalDate": "1000"},
                    {"internalDate": "2000"},
                ],
            },
        ),
        stored,
    )
    assert entry.custom_fields["closed"] == "1970-01-01T00:00:02+00:00"
    assert "last_message_at" not in entry.custom_fields
    foreign = {
        "id": "acme_mail",
        "capabilities": {
            "sync": {
                "entry_type": "note",
                "field_names": {"last_message_at": "closed"},
            }
        },
    }
    assert package_field_name(foreign, "acme_mail", "last_message_at") == "closed"
    missing = {"id": "acme_mail", "capabilities": {"sync": {"entry_type": "note"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="field name"):
        package_field_name(missing, "acme_mail", "last_message_at")


def test_gmail_thread_id_field_comes_from_the_package(monkeypatch):
    """Gmail thread ids are written under the package field name."""
    from app.agentive.connectors.gmail import GmailConnector
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_field_name
    from app.services.connectors import ExternalRecord

    rescan()
    assert package_field_name(None, "gmail", "gmail_thread_id") == "gmail_thread_id"
    old = {
        "capabilities": {
            "sync": {
                "entry_type": "gmail",
                "field_names": {"last_message_at": "last_message_at"},
            }
        }
    }
    assert package_field_name(old, "gmail", "gmail_thread_id") == "gmail_thread_id"
    stored = {
        "capabilities": {
            "sync": {
                "entry_type": "gmail",
                "field_names": {"gmail_thread_id": "thread"},
            }
        }
    }
    record = ExternalRecord(
        external_id="t",
        payload={"id": "thr-1", "messages": [{"id": "m1"}]},
    )
    entry = GmailConnector().to_entry(record, stored)
    assert entry.custom_fields["thread"] == "thr-1"
    assert "gmail_thread_id" not in entry.custom_fields
    child = GmailConnector().to_anchored_children(record, stored)[0]
    assert child["materialized"].custom_fields["thread"] == "thr-1"
    assert "gmail_thread_id" not in child["materialized"].custom_fields
    foreign = {
        "id": "acme_mail",
        "capabilities": {
            "sync": {
                "entry_type": "note",
                "field_names": {"gmail_thread_id": "thread"},
            }
        },
    }
    assert package_field_name(foreign, "acme_mail", "gmail_thread_id") == "thread"
    missing = {"id": "acme_mail", "capabilities": {"sync": {"entry_type": "note"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="field name"):
        package_field_name(missing, "acme_mail", "gmail_thread_id")


def test_gmail_labels_field_comes_from_the_package(monkeypatch):
    """Gmail label lists are written under the package field name."""
    from app.agentive.connectors.gmail import GmailConnector
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_field_name
    from app.services.connectors import ExternalRecord

    rescan()
    assert package_field_name(None, "gmail", "gmail_labels") == "gmail_labels"
    old = {
        "capabilities": {
            "sync": {
                "entry_type": "gmail",
                "field_names": {"gmail_thread_id": "gmail_thread_id"},
            }
        }
    }
    assert package_field_name(old, "gmail", "gmail_labels") == "gmail_labels"
    stored = {
        "capabilities": {
            "sync": {
                "entry_type": "gmail",
                "field_names": {"gmail_labels": "labels"},
            }
        }
    }
    record = ExternalRecord(
        external_id="t",
        payload={"id": "thr-1", "messages": [{"labelIds": ["INBOX"]}]},
    )
    entry = GmailConnector().to_entry(record, stored)
    assert entry.custom_fields["labels"] == ["INBOX"]
    assert "gmail_labels" not in entry.custom_fields
    child = GmailConnector().to_anchored_children(record, stored)[0]
    assert child["materialized"].custom_fields["labels"] == ["INBOX"]
    assert "gmail_labels" not in child["materialized"].custom_fields
    foreign = {
        "id": "acme_mail",
        "capabilities": {
            "sync": {"entry_type": "note", "field_names": {"gmail_labels": "labels"}}
        },
    }
    assert package_field_name(foreign, "acme_mail", "gmail_labels") == "labels"
    missing = {"id": "acme_mail", "capabilities": {"sync": {"entry_type": "note"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="field name"):
        package_field_name(missing, "acme_mail", "gmail_labels")


def test_gmail_remaining_fields_come_from_the_package(monkeypatch):
    """Remaining Gmail destination fields use the package field names."""
    from app.agentive.connectors.gmail import GmailConnector
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_field_name
    from app.services.connectors import ExternalRecord

    remaining = {
        "_source_system": "origin",
        "_source_version": "revision",
        "_last_synced_at": "synced",
        "unmatched_participants": "unmatched",
        "from": "sender",
        "to": "recipients",
        "cc": "copies",
        "sent_at": "sent",
        "snippet": "preview",
        "body": "text",
        "attachments_meta": "files",
        "gmail_message_id": "message",
    }
    rescan()
    for key in remaining:
        assert package_field_name(None, "gmail", key) == key
    old = {
        "capabilities": {
            "sync": {
                "entry_type": "gmail",
                "field_names": {"gmail_labels": "gmail_labels"},
            }
        }
    }
    for key in remaining:
        assert package_field_name(old, "gmail", key) == key
    stored = {
        "capabilities": {
            "sync": {"entry_type": "gmail", "field_names": dict(remaining)}
        }
    }
    record = ExternalRecord(
        external_id="t",
        payload={
            "id": "thr-1",
            "historyId": "9",
            "messages": [
                {
                    "id": "m1",
                    "internalDate": "1000",
                    "snippet": "hello",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "Ada <ada@example.com>"},
                            {"name": "To", "value": "bob@example.com"},
                            {"name": "Cc", "value": "cara@example.com"},
                        ]
                    },
                }
            ],
        },
    )
    entry = GmailConnector().to_entry(record, stored)
    fields = entry.custom_fields
    assert fields["origin"] == "gmail"
    assert fields["revision"] == "9"
    assert fields["synced"]
    assert fields["unmatched"] == []
    for key in (
        "_source_system",
        "_source_version",
        "_last_synced_at",
        "unmatched_participants",
    ):
        assert key not in fields
    child = GmailConnector().to_anchored_children(record, stored)[0]
    message = child["materialized"].custom_fields
    assert message["origin"] == "gmail"
    assert message["revision"] == ""
    assert message["synced"]
    assert message["sender"] == "ada@example.com"
    assert message["recipients"] == ["bob@example.com"]
    assert message["copies"] == ["cara@example.com"]
    assert message["sent"] == "1970-01-01T00:00:01+00:00"
    assert message["preview"] == "hello"
    assert message["text"] == ""
    assert message["files"] == []
    assert message["message"] == "m1"
    for key in remaining:
        assert key not in message
    foreign = {
        "id": "acme_mail",
        "capabilities": {
            "sync": {"entry_type": "note", "field_names": dict(remaining)}
        },
    }
    for key, name in remaining.items():
        assert package_field_name(foreign, "acme_mail", key) == name
    missing = {"id": "acme_mail", "capabilities": {"sync": {"entry_type": "note"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    for key in remaining:
        with pytest.raises(ValueError, match="field name"):
            package_field_name(missing, "acme_mail", key)


def test_gmail_sent_timestamp_uses_the_package_path(monkeypatch):
    """Message timestamps are read from the package updated_at path."""
    from app.agentive.connectors.gmail import (
        GmailConnector,
        _gmail_message_updated_at,
    )
    from app.connectors.package_discovery import rescan
    from app.services.connectors import ExternalRecord

    rescan()
    assert _gmail_message_updated_at(None) == "$.internalDate"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/gmail/v1/users/me/threads/1",
                    },
                    "output": {"select": "$", "items": "$.messages"},
                }
            ]
        }
    }
    assert _gmail_message_updated_at(old) == "$.internalDate"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {"method": "GET", "path": "/custom/threads/1"},
                    "output": {"select": "$", "updated_at": "$.sent"},
                }
            ]
        }
    }
    record = ExternalRecord(
        external_id="t",
        payload={
            "id": "thr-1",
            "messages": [{"id": "m1", "internalDate": "1000", "sent": "2000"}],
        },
    )
    entry = GmailConnector().to_entry(record, stored)
    assert entry.custom_fields["first_message_at"] == "1970-01-01T00:00:02+00:00"
    assert entry.custom_fields["last_message_at"] == "1970-01-01T00:00:02+00:00"
    message = GmailConnector().to_anchored_children(record, stored)[0]["materialized"]
    assert message.custom_fields["sent_at"] == "1970-01-01T00:00:02+00:00"
    assert message.external_updated_at == "2000"
    fallback = GmailConnector().to_anchored_children(record, old)[0]["materialized"]
    assert fallback.external_updated_at == "1000"
    foreign = {
        "id": "acme_mail",
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {"method": "GET", "path": "/mail/1"},
                    "output": {"updated_at": "$.sent"},
                }
            ]
        },
    }
    assert _gmail_message_updated_at(foreign) == "$.sent"
    missing = {"id": "acme_mail", "capabilities": {"sync": {"entry_type": "note"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="message timestamp"):
        _gmail_message_updated_at(missing)


@pytest.mark.asyncio
async def test_gmail_reads_use_the_package_request(monkeypatch):
    """Thread list and thread read go through the shared package request."""
    from app.agentive.connectors.gmail import GmailConnector
    from app.connectors.package_discovery import rescan

    rescan()
    seen: dict = {}

    async def fake_perform(
        manifest, request, config, secrets, inputs=None, client=None
    ):
        seen["path"] = request["path"]
        seen["inputs"] = inputs
        seen["authorization"] = (request.get("headers") or {}).get("Authorization")
        if inputs and inputs.get("thread_id"):
            return {"id": inputs["thread_id"], "messages": []}
        return {"threads": [{"id": "t1"}]}

    monkeypatch.setattr("app.connectors.package_runtime.perform_request", fake_perform)
    connector = GmailConnector()
    client = object()
    ids = await connector._list_thread_ids_for_label(
        client=client, access_token="tok", label_id="INBOX"
    )
    assert ids == ["t1"]
    assert seen["path"] == "/gmail/v1/users/me/threads"
    assert seen["inputs"]["label_id"] == "INBOX"
    assert seen["inputs"]["cursor"] == ""
    assert seen["authorization"] == "Bearer tok"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_threads",
                    "request": {
                        "method": "GET",
                        "path": "/custom/threads",
                        "query": {"q": "{{input.label_id}}"},
                    },
                }
            ]
        }
    }
    await connector._list_thread_ids_for_label(
        client=client, access_token="tok", label_id="SENT", manifest=stored
    )
    assert seen["path"] == "/custom/threads"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {"method": "GET", "path": "/old"},
                }
            ]
        }
    }
    await connector._list_thread_ids_for_label(
        client=client, access_token="tok", label_id="INBOX", manifest=old
    )
    assert seen["path"] == "/gmail/v1/users/me/threads"
    foreign = {
        "id": "acme_mail",
        "capabilities": {
            "actions": [
                {
                    "name": "list_threads",
                    "request": {"method": "GET", "path": "/boxes"},
                },
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/boxes/{{input.thread_id}}",
                    },
                },
            ]
        },
    }
    await connector._list_thread_ids_for_label(
        client=client, access_token="tok", label_id="INBOX", manifest=foreign
    )
    assert seen["path"] == "/boxes"
    thread = await connector._get_thread(
        client=client, access_token="tok", thread_id="t9", manifest=foreign
    )
    assert thread["id"] == "t9"
    assert seen["path"] == "/boxes/{{input.thread_id}}"
    missing = {"id": "acme_mail", "capabilities": {"actions": []}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="does not declare"):
        await connector._list_thread_ids_for_label(
            client=client, access_token="tok", label_id="INBOX", manifest=missing
        )


def test_gmail_sync_host_comes_from_the_package(monkeypatch):
    """Thread sync uses the package base URL."""
    from app.agentive.connectors.gmail import _gmail_api_base
    from app.connectors.package_discovery import rescan

    rescan()
    assert _gmail_api_base() == "https://gmail.googleapis.com"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {"http": {"base_url": "https://mail.example"}},
    )
    assert _gmail_api_base() == "https://mail.example"
    assert (
        _gmail_api_base({"http": {"base_url": "https://stored-mail.example"}})
        == "https://stored-mail.example"
    )
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="does not declare"):
        _gmail_api_base()


def test_gmail_thread_list_path_comes_from_the_package(monkeypatch):
    """Thread list uses the package path. A stored action wins."""
    from urllib.parse import parse_qs, urlparse

    from app.agentive.connectors.gmail import _gmail_threads_url
    from app.connectors.package_discovery import rescan

    rescan()
    url = _gmail_threads_url("https://gmail.googleapis.com", "INBOX", 100, None)
    parsed = urlparse(url)
    assert parsed.path == "/gmail/v1/users/me/threads"
    assert parse_qs(parsed.query)["labelIds"] == ["INBOX"]
    assert parse_qs(parsed.query)["maxResults"] == ["100"]
    old = {"capabilities": {"sync": {"entry_type": "gmail"}}}
    old_url = _gmail_threads_url("https://stored-mail.example", "SENT", 50, old)
    assert urlparse(old_url).path == "/gmail/v1/users/me/threads"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_threads",
                    "request": {
                        "method": "GET",
                        "path": "/custom/threads",
                        "query": {"fields": "id"},
                    },
                }
            ]
        }
    }
    stored_url = _gmail_threads_url(
        "https://stored-mail.example", "INBOX", 100, stored, "next"
    )
    stored_parsed = urlparse(stored_url)
    assert stored_parsed.path == "/custom/threads"
    stored_query = parse_qs(stored_parsed.query)
    assert stored_query["fields"] == ["id"]
    assert stored_query["pageToken"] == ["next"]
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="does not declare"):
        _gmail_threads_url("https://gmail.googleapis.com", "INBOX", 100, None)


def test_gmail_page_size_comes_from_the_package(monkeypatch):
    """Thread list page size comes from the snapshot, then the admitted package."""
    from app.agentive.connectors.gmail import _gmail_page_size
    from app.connectors.package_discovery import rescan

    rescan()
    assert _gmail_page_size(None) == 100
    old = {"capabilities": {"sync": {"entry_type": "gmail"}}}
    assert _gmail_page_size(old) == 100
    stored = {"capabilities": {"sync": {"entry_type": "gmail", "page_size": 25}}}
    assert _gmail_page_size(stored) == 25
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="does not declare"):
        _gmail_page_size(None)


def test_gmail_page_size_field_comes_from_the_package(monkeypatch):
    """Thread lists send the page size under the package query key."""
    from urllib.parse import parse_qs, urlparse

    from app.agentive.connectors.gmail import _gmail_threads_url
    from app.connectors.package_discovery import rescan

    rescan()
    url = _gmail_threads_url("https://gmail.googleapis.com", "INBOX", 100, None)
    assert parse_qs(urlparse(url).query)["maxResults"] == ["100"]
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_threads",
                    "request": {
                        "method": "GET",
                        "path": "/gmail/v1/users/me/threads",
                        "query": {
                            "labelIds": "{{input.label_id}}",
                            "pageToken": "{{cursor}}",
                        },
                    },
                }
            ]
        }
    }
    old_query = parse_qs(
        urlparse(
            _gmail_threads_url("https://stored-mail.example", "SENT", 25, old)
        ).query
    )
    assert old_query["maxResults"] == ["25"]
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_threads",
                    "request": {
                        "method": "GET",
                        "path": "/custom/threads",
                        "query": {
                            "mailbox": "{{input.label_id}}",
                            "next": "{{cursor}}",
                            "limit": "{{input.page_size}}",
                        },
                    },
                }
            ]
        }
    }
    stored_query = parse_qs(
        urlparse(
            _gmail_threads_url("https://stored-mail.example", "INBOX", 40, stored)
        ).query
    )
    assert stored_query["limit"] == ["40"]
    assert "maxResults" not in stored_query
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="page size field"):
        _gmail_threads_url("https://stored-mail.example", "INBOX", 100, old)


def test_gmail_max_pages_comes_from_the_package(monkeypatch):
    """Label sync follows the package page cap, then the admitted package."""
    from app.agentive.connectors.gmail import _gmail_max_pages
    from app.connectors.package_discovery import rescan

    rescan()
    assert _gmail_max_pages(None) == 50
    old = {"capabilities": {"sync": {"entry_type": "gmail", "page_size": 100}}}
    assert _gmail_max_pages(old) == 50
    stored = {"capabilities": {"sync": {"entry_type": "gmail", "max_pages": 3}}}
    assert _gmail_max_pages(stored) == 3
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="page cap"):
        _gmail_max_pages(old)


def test_gmail_thread_record_type_comes_from_the_package(monkeypatch):
    """Gmail threads use the package record type, for any package id."""
    from app.agentive.connectors.gmail import GmailConnector
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_record_type
    from app.services.connectors import ExternalRecord

    rescan()
    assert package_record_type(None, "gmail") == "email_thread"
    old = {"capabilities": {"sync": {"entry_type": "gmail", "page_size": 100}}}
    assert package_record_type(old, "gmail") == "email_thread"
    stored = {
        "capabilities": {
            "sync": {"entry_type": "gmail", "record_type": "mailbox_thread"}
        }
    }
    entry = GmailConnector().to_entry(
        ExternalRecord(external_id="t", payload={"id": "t"}),
        stored,
    )
    assert entry.entry_type_key == "mailbox_thread"
    foreign = {
        "id": "acme_mail",
        "capabilities": {"sync": {"entry_type": "note", "record_type": "email_thread"}},
    }
    assert package_record_type(foreign, "acme_mail") == "email_thread"
    missing = {"id": "acme_mail", "capabilities": {"sync": {"entry_type": "note"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="record type"):
        package_record_type(missing, "acme_mail")


def test_gmail_message_child_type_comes_from_the_package(monkeypatch):
    """Gmail messages use the package child type, for any package id."""
    from app.agentive.connectors.gmail import GmailConnector
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_child_type
    from app.services.connectors import ExternalRecord

    rescan()
    assert package_child_type(None, "gmail") == "email_message"
    old = {
        "capabilities": {"sync": {"entry_type": "gmail", "record_type": "email_thread"}}
    }
    assert package_child_type(old, "gmail") == "email_message"
    stored = {
        "capabilities": {
            "sync": {
                "entry_type": "gmail",
                "record_type": "email_thread",
                "child_type": "note_message",
            }
        }
    }
    children = GmailConnector().to_anchored_children(
        ExternalRecord(external_id="t", payload={"messages": [{"id": "m1"}]}),
        stored,
    )
    assert children[0]["materialized"].entry_type_key == "note_message"
    foreign = {
        "id": "acme_mail",
        "capabilities": {"sync": {"entry_type": "note", "child_type": "work_note"}},
    }
    assert package_child_type(foreign, "acme_mail") == "work_note"
    missing = {"id": "acme_mail", "capabilities": {"sync": {"entry_type": "note"}}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="child type"):
        package_child_type(missing, "acme_mail")


def test_gmail_label_filter_comes_from_the_package(monkeypatch):
    """Thread list filters by the package's label query key."""
    from urllib.parse import parse_qs, urlparse

    from app.agentive.connectors.gmail import _gmail_threads_url
    from app.connectors.package_discovery import rescan

    rescan()
    url = _gmail_threads_url("https://gmail.googleapis.com", "INBOX", 100, None)
    assert parse_qs(urlparse(url).query)["labelIds"] == ["INBOX"]
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_threads",
                    "request": {"method": "GET", "path": "/gmail/v1/users/me/threads"},
                }
            ]
        }
    }
    old_query = parse_qs(
        urlparse(
            _gmail_threads_url("https://stored-mail.example", "SENT", 50, old)
        ).query
    )
    assert old_query["labelIds"] == ["SENT"]
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_threads",
                    "request": {
                        "method": "GET",
                        "path": "/custom/threads",
                        "query": {"mailbox": "{{input.label_id}}"},
                    },
                }
            ]
        }
    }
    stored_query = parse_qs(
        urlparse(
            _gmail_threads_url("https://stored-mail.example", "INBOX", 100, stored)
        ).query
    )
    assert stored_query["mailbox"] == ["INBOX"]
    assert "labelIds" not in stored_query
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="label filter"):
        _gmail_threads_url("https://stored-mail.example", "INBOX", 100, old)


def test_gmail_cursor_field_comes_from_the_package(monkeypatch):
    """Thread list sends the page cursor under the package query key."""
    from urllib.parse import parse_qs, urlparse

    from app.agentive.connectors.gmail import _gmail_threads_url
    from app.connectors.package_discovery import rescan

    rescan()
    admitted = parse_qs(
        urlparse(
            _gmail_threads_url(
                "https://gmail.googleapis.com", "INBOX", 100, None, "page-2"
            )
        ).query
    )
    assert admitted["pageToken"] == ["page-2"]
    assert "pageToken" not in parse_qs(
        urlparse(
            _gmail_threads_url("https://gmail.googleapis.com", "INBOX", 100, None)
        ).query
    )
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_threads",
                    "request": {
                        "method": "GET",
                        "path": "/gmail/v1/users/me/threads",
                        "query": {"labelIds": "{{input.label_id}}"},
                    },
                }
            ]
        }
    }
    old_query = parse_qs(
        urlparse(
            _gmail_threads_url(
                "https://stored-mail.example", "INBOX", 100, old, "older"
            )
        ).query
    )
    assert old_query["pageToken"] == ["older"]
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_threads",
                    "request": {
                        "method": "GET",
                        "path": "/custom/threads",
                        "query": {
                            "mailbox": "{{input.label_id}}",
                            "next": "{{cursor}}",
                        },
                    },
                }
            ]
        }
    }
    stored_query = parse_qs(
        urlparse(
            _gmail_threads_url(
                "https://stored-mail.example", "INBOX", 100, stored, "tok"
            )
        ).query
    )
    assert stored_query["next"] == ["tok"]
    assert "pageToken" not in stored_query
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="cursor field"):
        _gmail_threads_url("https://stored-mail.example", "INBOX", 100, old, "tok")


def test_gmail_next_page_field_comes_from_the_package(monkeypatch):
    """The next page token is read from the package output path."""
    from app.agentive.connectors.gmail import _gmail_page_cursor
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output

    rescan()
    assert (
        select_output({"nextPageToken": "abc", "threads": []}, _gmail_page_cursor(None))
        == "abc"
    )
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_threads",
                    "request": {"method": "GET", "path": "/gmail/v1/users/me/threads"},
                    "output": {"select": "$.threads"},
                }
            ]
        }
    }
    assert _gmail_page_cursor(old) == "$.nextPageToken"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_threads",
                    "request": {"method": "GET", "path": "/custom/threads"},
                    "output": {"select": "$.items", "next": "$.page"},
                }
            ]
        }
    }
    assert _gmail_page_cursor(stored) == "$.page"
    assert select_output({"page": "zzz"}, _gmail_page_cursor(stored)) == "zzz"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="next page field"):
        _gmail_page_cursor(old)


def test_gmail_thread_id_comes_from_the_package(monkeypatch):
    """Each thread id is read from the package output path."""
    from app.agentive.connectors.gmail import _gmail_thread_id
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output

    rescan()
    assert select_output({"id": "t1"}, _gmail_thread_id(None)) == "t1"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_threads",
                    "request": {"method": "GET", "path": "/gmail/v1/users/me/threads"},
                    "output": {"select": "$.threads", "next": "$.nextPageToken"},
                }
            ]
        }
    }
    assert _gmail_thread_id(old) == "$.id"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_threads",
                    "request": {"method": "GET", "path": "/custom/threads"},
                    "output": {"select": "$.items", "id": "$.threadId"},
                }
            ]
        }
    }
    assert _gmail_thread_id(stored) == "$.threadId"
    assert select_output({"threadId": "abc"}, _gmail_thread_id(stored)) == "abc"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="thread id"):
        _gmail_thread_id(old)


def test_gmail_thread_selector_comes_from_the_package(monkeypatch):
    """Thread rows are selected by the package output path."""
    from app.agentive.connectors.gmail import _gmail_thread_select
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output

    rescan()
    body = {"threads": [{"id": "t1"}], "other": [{"id": "no"}]}
    assert select_output(body, _gmail_thread_select(None)) == [{"id": "t1"}]
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_threads",
                    "request": {"method": "GET", "path": "/gmail/v1/users/me/threads"},
                }
            ]
        }
    }
    assert _gmail_thread_select(old) == "$.threads"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_threads",
                    "request": {"method": "GET", "path": "/custom/threads"},
                    "output": {"select": "$.items"},
                }
            ]
        }
    }
    assert _gmail_thread_select(stored) == "$.items"
    assert select_output({"items": [{"id": "a"}]}, _gmail_thread_select(stored)) == [
        {"id": "a"}
    ]
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="thread list selector"):
        _gmail_thread_select(old)


def test_gmail_thread_read_path_comes_from_the_package(monkeypatch):
    """One thread read uses the package path. A stored action wins."""
    from urllib.parse import parse_qs, urlparse

    from app.agentive.connectors.gmail import _gmail_thread_url
    from app.connectors.package_discovery import rescan

    rescan()
    url = _gmail_thread_url("https://gmail.googleapis.com", "th-1", None)
    parsed = urlparse(url)
    assert parsed.path == "/gmail/v1/users/me/threads/th-1"
    assert parsed.query == ""
    old = {"capabilities": {"sync": {"entry_type": "gmail"}}}
    old_url = _gmail_thread_url("https://stored-mail.example", "th-2", old)
    assert urlparse(old_url).path == "/gmail/v1/users/me/threads/th-2"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/custom/threads/{{input.thread_id}}",
                        "query": {"format": "metadata"},
                    },
                }
            ]
        }
    }
    stored_url = _gmail_thread_url("https://stored-mail.example", "th-3", stored)
    stored_parsed = urlparse(stored_url)
    assert stored_parsed.path == "/custom/threads/th-3"
    assert parse_qs(stored_parsed.query)["format"] == ["metadata"]
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="does not declare"):
        _gmail_thread_url("https://gmail.googleapis.com", "th-1", None)


def test_gmail_thread_body_comes_from_the_package(monkeypatch):
    """One thread read uses the package output selector."""
    from app.agentive.connectors.gmail import _gmail_thread_body
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output

    rescan()
    body = {"id": "th-1", "messages": [{"id": "m1"}]}
    assert select_output(body, _gmail_thread_body(None)) == body
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/gmail/v1/users/me/threads/{{input.thread_id}}",
                    },
                }
            ]
        }
    }
    assert _gmail_thread_body(old) == "$"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/custom/threads/{{input.thread_id}}",
                    },
                    "output": {"select": "$.thread"},
                }
            ]
        }
    }
    assert _gmail_thread_body(stored) == "$.thread"
    assert select_output({"thread": {"id": "th-9"}}, "$.thread") == {"id": "th-9"}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="thread body"):
        _gmail_thread_body(old)


def test_gmail_thread_messages_come_from_the_package(monkeypatch):
    """Messages inside a thread are read from the package output path."""
    from app.agentive.connectors.gmail import _gmail_thread_messages
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output

    rescan()
    body = {"messages": [{"id": "m1"}]}
    assert select_output(body, _gmail_thread_messages(None)) == [{"id": "m1"}]
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/gmail/v1/users/me/threads/{{input.thread_id}}",
                    },
                    "output": {"select": "$"},
                }
            ]
        }
    }
    assert _gmail_thread_messages(old) == "$.messages"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/custom/threads/{{input.thread_id}}",
                    },
                    "output": {"select": "$.thread", "items": "$.mail"},
                }
            ]
        }
    }
    assert _gmail_thread_messages(stored) == "$.mail"
    assert select_output({"mail": [{"id": "m9"}]}, "$.mail") == [{"id": "m9"}]
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="message field"):
        _gmail_thread_messages(old)


def test_gmail_message_timestamp_comes_from_the_package(monkeypatch):
    """The last message timestamp is read from the package output path."""
    from app.agentive.connectors.gmail import _gmail_message_updated_at
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output

    rescan()
    assert (
        select_output({"internalDate": "1000"}, _gmail_message_updated_at(None))
        == "1000"
    )
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/gmail/v1/users/me/threads/{{input.thread_id}}",
                    },
                    "output": {"select": "$", "items": "$.messages"},
                }
            ]
        }
    }
    assert _gmail_message_updated_at(old) == "$.internalDate"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/custom/threads/{{input.thread_id}}",
                    },
                    "output": {
                        "select": "$",
                        "items": "$.mail",
                        "updated_at": "$.when",
                    },
                }
            ]
        }
    }
    assert _gmail_message_updated_at(stored) == "$.when"
    assert select_output({"when": "2024-02-02"}, "$.when") == "2024-02-02"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="message timestamp"):
        _gmail_message_updated_at(old)


def test_gmail_message_id_comes_from_the_package(monkeypatch):
    """Each message id is read from the package output path."""
    from app.agentive.connectors.gmail import GmailConnector, _gmail_message_id
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output
    from app.services.connectors import ExternalRecord

    rescan()
    assert select_output({"id": "m1"}, _gmail_message_id(None)) == "m1"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/gmail/v1/users/me/threads/{{input.thread_id}}",
                    },
                    "output": {"select": "$", "items": "$.messages"},
                }
            ]
        }
    }
    assert _gmail_message_id(old) == "$.id"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/custom/threads/{{input.thread_id}}",
                    },
                    "output": {"select": "$", "items": "$.mail", "id": "$.mid"},
                }
            ]
        }
    }
    assert _gmail_message_id(stored) == "$.mid"
    record = ExternalRecord(external_id="t", payload={"mail": [{"mid": "m9"}]})
    children = GmailConnector().to_anchored_children(record, stored)
    assert children[0]["external_id"] == "m9"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="message id"):
        _gmail_message_id(old)


def test_gmail_history_field_comes_from_the_package(monkeypatch):
    """The thread history token is read from the package output path."""
    from app.agentive.connectors.gmail import GmailConnector, _gmail_thread_version
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output
    from app.services.connectors import ExternalRecord

    rescan()
    assert select_output({"historyId": "44"}, _gmail_thread_version(None)) == "44"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/gmail/v1/users/me/threads/{{input.thread_id}}",
                    },
                    "output": {"select": "$", "items": "$.messages"},
                }
            ]
        }
    }
    assert _gmail_thread_version(old) == "$.historyId"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/custom/threads/{{input.thread_id}}",
                    },
                    "output": {"select": "$", "version": "$.rev"},
                }
            ]
        }
    }
    assert _gmail_thread_version(stored) == "$.rev"
    record = ExternalRecord(external_id="t", payload={"rev": "9"})
    entry = GmailConnector().to_entry(record, stored)
    assert entry.custom_fields["_source_version"] == "9"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="history field"):
        _gmail_thread_version(old)


def test_gmail_message_summary_comes_from_the_package(monkeypatch):
    """Each message preview is read from the package output path."""
    from app.agentive.connectors.gmail import GmailConnector, _gmail_message_summary
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output
    from app.services.connectors import ExternalRecord

    rescan()
    assert select_output({"snippet": "hello"}, _gmail_message_summary(None)) == "hello"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/gmail/v1/users/me/threads/{{input.thread_id}}",
                    },
                    "output": {"select": "$", "items": "$.messages"},
                }
            ]
        }
    }
    assert _gmail_message_summary(old) == "$.snippet"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/custom/threads/{{input.thread_id}}",
                    },
                    "output": {
                        "select": "$",
                        "items": "$.messages",
                        "summary": "$.preview",
                    },
                }
            ]
        }
    }
    assert _gmail_message_summary(stored) == "$.preview"
    record = ExternalRecord(
        external_id="t",
        payload={"messages": [{"id": "m1", "preview": "hello"}]},
    )
    children = GmailConnector().to_anchored_children(record, stored)
    assert children[0]["materialized"].custom_fields["snippet"] == "hello"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="snippet field"):
        _gmail_message_summary(old)


def test_gmail_subject_header_comes_from_the_package(monkeypatch):
    """The message subject is read from the package header name."""
    from app.agentive.connectors.gmail import GmailConnector, _gmail_subject_header
    from app.connectors.package_discovery import rescan
    from app.services.connectors import ExternalRecord

    rescan()
    assert _gmail_subject_header(None) == "subject"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/gmail/v1/users/me/threads/{{input.thread_id}}",
                    },
                    "output": {"select": "$", "items": "$.messages"},
                }
            ]
        }
    }
    assert _gmail_subject_header(old) == "subject"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/custom/threads/{{input.thread_id}}",
                    },
                    "output": {"select": "$", "title_header": "Topic"},
                }
            ]
        }
    }
    assert _gmail_subject_header(stored) == "Topic"
    record = ExternalRecord(
        external_id="t",
        payload={
            "messages": [
                {"payload": {"headers": [{"name": "Topic", "value": "Hello"}]}}
            ]
        },
    )
    entry = GmailConnector().to_entry(record, stored)
    assert entry.custom_fields["subject"] == "Hello"
    assert entry.title == "Hello"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="subject field"):
        _gmail_subject_header(old)


def test_gmail_from_header_comes_from_the_package(monkeypatch):
    """The sender is read from the package header name."""
    from app.agentive.connectors.gmail import GmailConnector, _gmail_from_header
    from app.connectors.package_discovery import rescan
    from app.services.connectors import ExternalRecord

    rescan()
    assert _gmail_from_header(None) == "from"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/gmail/v1/users/me/threads/{{input.thread_id}}",
                    },
                    "output": {"select": "$", "title_header": "subject"},
                }
            ]
        }
    }
    assert _gmail_from_header(old) == "from"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/custom/threads/{{input.thread_id}}",
                    },
                    "output": {"select": "$", "from_header": "Sender"},
                }
            ]
        }
    }
    assert _gmail_from_header(stored) == "Sender"
    record = ExternalRecord(
        external_id="t",
        payload={
            "messages": [
                {
                    "payload": {
                        "headers": [
                            {"name": "Sender", "value": "Ada <ada@example.com>"}
                        ]
                    }
                }
            ]
        },
    )
    entry = GmailConnector().to_entry(record, stored)
    assert entry.custom_fields["participant_emails"] == ["ada@example.com"]
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="from field"):
        _gmail_from_header(old)


def test_gmail_to_header_comes_from_the_package(monkeypatch):
    """Recipients are read from the package header name."""
    from app.agentive.connectors.gmail import GmailConnector, _gmail_to_header
    from app.connectors.package_discovery import rescan
    from app.services.connectors import ExternalRecord

    rescan()
    assert _gmail_to_header(None) == "to"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/gmail/v1/users/me/threads/{{input.thread_id}}",
                    },
                    "output": {"select": "$", "from_header": "from"},
                }
            ]
        }
    }
    assert _gmail_to_header(old) == "to"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/custom/threads/{{input.thread_id}}",
                    },
                    "output": {"select": "$", "to_header": "Recipients"},
                }
            ]
        }
    }
    assert _gmail_to_header(stored) == "Recipients"
    record = ExternalRecord(
        external_id="t",
        payload={
            "messages": [
                {
                    "payload": {
                        "headers": [
                            {"name": "Recipients", "value": "Bob <bob@example.com>"}
                        ]
                    }
                }
            ]
        },
    )
    entry = GmailConnector().to_entry(record, stored)
    assert entry.custom_fields["participant_emails"] == ["bob@example.com"]
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="to field"):
        _gmail_to_header(old)


def test_gmail_cc_header_comes_from_the_package(monkeypatch):
    """Copied recipients are read from the package header name."""
    from app.agentive.connectors.gmail import GmailConnector, _gmail_cc_header
    from app.connectors.package_discovery import rescan
    from app.services.connectors import ExternalRecord

    rescan()
    assert _gmail_cc_header(None) == "cc"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/gmail/v1/users/me/threads/{{input.thread_id}}",
                    },
                    "output": {"select": "$", "to_header": "to"},
                }
            ]
        }
    }
    assert _gmail_cc_header(old) == "cc"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/custom/threads/{{input.thread_id}}",
                    },
                    "output": {"select": "$", "cc_header": "Copies"},
                }
            ]
        }
    }
    assert _gmail_cc_header(stored) == "Copies"
    record = ExternalRecord(
        external_id="t",
        payload={
            "messages": [
                {
                    "payload": {
                        "headers": [
                            {"name": "Copies", "value": "Cara <cara@example.com>"}
                        ]
                    }
                }
            ]
        },
    )
    entry = GmailConnector().to_entry(record, stored)
    assert entry.custom_fields["participant_emails"] == ["cara@example.com"]
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="cc field"):
        _gmail_cc_header(old)


def test_gmail_label_list_comes_from_the_package(monkeypatch):
    """Message labels are read from the package output path."""
    from app.agentive.connectors.gmail import GmailConnector, _gmail_label_list
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import select_output
    from app.services.connectors import ExternalRecord

    rescan()
    assert select_output({"labelIds": ["INBOX"]}, _gmail_label_list(None)) == ["INBOX"]
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/gmail/v1/users/me/threads/{{input.thread_id}}",
                    },
                    "output": {"select": "$", "cc_header": "cc"},
                }
            ]
        }
    }
    assert _gmail_label_list(old) == "$.labelIds"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/custom/threads/{{input.thread_id}}",
                    },
                    "output": {
                        "select": "$",
                        "items": "$.messages",
                        "labels": "$.tags",
                    },
                }
            ]
        }
    }
    assert _gmail_label_list(stored) == "$.tags"
    record = ExternalRecord(
        external_id="t",
        payload={"messages": [{"id": "m1", "tags": ["INBOX", "WORK"]}]},
    )
    children = GmailConnector().to_anchored_children(record, stored)
    assert children[0]["materialized"].custom_fields["gmail_labels"] == [
        "INBOX",
        "WORK",
    ]
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="label field"):
        _gmail_label_list(old)


def test_gmail_body_type_comes_from_the_package(monkeypatch):
    """Message text bodies use the package MIME type."""
    from app.agentive.connectors.gmail import GmailConnector, _gmail_body_type
    from app.connectors.package_discovery import rescan
    from app.services.connectors import ExternalRecord

    rescan()
    assert _gmail_body_type(None) == "text/plain"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/gmail/v1/users/me/threads/1",
                    },
                    "output": {"select": "$", "labels": "$.labelIds"},
                }
            ]
        }
    }
    assert _gmail_body_type(old) == "text/plain"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {"method": "GET", "path": "/custom/threads/1"},
                    "output": {"select": "$", "body_type": "text/html"},
                }
            ]
        }
    }
    assert _gmail_body_type(stored) == "text/html"
    record = ExternalRecord(
        external_id="t",
        payload={
            "messages": [
                {
                    "id": "m1",
                    "payload": {
                        "parts": [
                            {"mimeType": "text/plain", "body": {"data": "Plain!"}},
                            {"mimeType": "text/html", "body": {"data": "Hello!"}},
                        ]
                    },
                }
            ]
        },
    )
    children = GmailConnector().to_anchored_children(record, stored)
    assert children[0]["materialized"].body == "Hello!"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="body type"):
        _gmail_body_type(old)


def test_gmail_body_key_comes_from_the_package(monkeypatch):
    """Message body parts use the package MIME type key."""
    from app.agentive.connectors.gmail import GmailConnector, _gmail_body_key
    from app.connectors.package_discovery import rescan
    from app.services.connectors import ExternalRecord

    rescan()
    assert _gmail_body_key(None) == "mimeType"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/gmail/v1/users/me/threads/1",
                    },
                    "output": {"select": "$", "body_type": "text/plain"},
                }
            ]
        }
    }
    assert _gmail_body_key(old) == "mimeType"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {"method": "GET", "path": "/custom/threads/1"},
                    "output": {"select": "$", "body_key": "kind"},
                }
            ]
        }
    }
    assert _gmail_body_key(stored) == "kind"
    record = ExternalRecord(
        external_id="t",
        payload={
            "messages": [
                {
                    "id": "m1",
                    "payload": {
                        "parts": [
                            {"mimeType": "text/plain", "body": {"data": "Plain!"}},
                            {"kind": "text/plain", "body": {"data": "Hello!"}},
                        ]
                    },
                }
            ]
        },
    )
    children = GmailConnector().to_anchored_children(record, stored)
    assert children[0]["materialized"].body == "Hello!"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="body key"):
        _gmail_body_key(old)


def test_gmail_content_key_comes_from_the_package(monkeypatch):
    """Encoded message text uses the package content key."""
    from app.agentive.connectors.gmail import GmailConnector, _gmail_content_key
    from app.connectors.package_discovery import rescan
    from app.services.connectors import ExternalRecord

    rescan()
    assert _gmail_content_key(None) == "data"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/gmail/v1/users/me/threads/1",
                    },
                    "output": {"select": "$", "body_key": "mimeType"},
                }
            ]
        }
    }
    assert _gmail_content_key(old) == "data"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {"method": "GET", "path": "/custom/threads/1"},
                    "output": {"select": "$", "content_key": "text"},
                }
            ]
        }
    }
    assert _gmail_content_key(stored) == "text"
    record = ExternalRecord(
        external_id="t",
        payload={
            "messages": [
                {
                    "id": "m1",
                    "payload": {
                        "parts": [
                            {
                                "mimeType": "text/plain",
                                "body": {"data": "Plain!", "text": "Hello!"},
                            }
                        ]
                    },
                }
            ]
        },
    )
    children = GmailConnector().to_anchored_children(record, stored)
    assert children[0]["materialized"].body == "Hello!"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="content key"):
        _gmail_content_key(old)


def test_gmail_body_field_comes_from_the_package(monkeypatch):
    """Message text is read from the package body-object key."""
    from app.agentive.connectors.gmail import GmailConnector, _gmail_body_field
    from app.connectors.package_discovery import rescan
    from app.services.connectors import ExternalRecord

    rescan()
    assert _gmail_body_field(None) == "body"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/gmail/v1/users/me/threads/1",
                    },
                    "output": {"select": "$", "content_key": "data"},
                }
            ]
        }
    }
    assert _gmail_body_field(old) == "body"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {"method": "GET", "path": "/custom/threads/1"},
                    "output": {"select": "$", "body_field": "blob"},
                }
            ]
        }
    }
    assert _gmail_body_field(stored) == "blob"
    record = ExternalRecord(
        external_id="t",
        payload={
            "messages": [
                {
                    "id": "m1",
                    "payload": {
                        "parts": [
                            {
                                "mimeType": "text/plain",
                                "body": {"data": "Plain!"},
                                "blob": {"data": "Hello!"},
                            }
                        ]
                    },
                }
            ]
        },
    )
    children = GmailConnector().to_anchored_children(record, stored)
    assert children[0]["materialized"].body == "Hello!"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="body field"):
        _gmail_body_field(old)


def test_gmail_parts_key_comes_from_the_package(monkeypatch):
    """Multipart message bodies use the package part-list key."""
    from app.agentive.connectors.gmail import GmailConnector, _gmail_parts_key
    from app.connectors.package_discovery import rescan
    from app.services.connectors import ExternalRecord

    rescan()
    assert _gmail_parts_key(None) == "parts"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/gmail/v1/users/me/threads/1",
                    },
                    "output": {"select": "$", "body_field": "body"},
                }
            ]
        }
    }
    assert _gmail_parts_key(old) == "parts"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {"method": "GET", "path": "/custom/threads/1"},
                    "output": {"select": "$", "parts_key": "chunks"},
                }
            ]
        }
    }
    assert _gmail_parts_key(stored) == "chunks"
    record = ExternalRecord(
        external_id="t",
        payload={
            "messages": [
                {
                    "id": "m1",
                    "payload": {
                        "parts": [
                            {
                                "mimeType": "text/plain",
                                "body": {"data": "Plain!"},
                            }
                        ],
                        "chunks": [
                            {
                                "mimeType": "text/plain",
                                "body": {"data": "Hello!"},
                            }
                        ],
                    },
                }
            ]
        },
    )
    children = GmailConnector().to_anchored_children(record, stored)
    assert children[0]["materialized"].body == "Hello!"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="part list"):
        _gmail_parts_key(old)


def test_gmail_payload_key_comes_from_the_package(monkeypatch):
    """Message headers and bodies use the package payload key."""
    from app.agentive.connectors.gmail import GmailConnector, _gmail_payload_key
    from app.connectors.package_discovery import rescan
    from app.services.connectors import ExternalRecord

    rescan()
    assert _gmail_payload_key(None) == "payload"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/gmail/v1/users/me/threads/1",
                    },
                    "output": {"select": "$", "parts_key": "parts"},
                }
            ]
        }
    }
    assert _gmail_payload_key(old) == "payload"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {"method": "GET", "path": "/custom/threads/1"},
                    "output": {"select": "$", "payload_key": "envelope"},
                }
            ]
        }
    }
    assert _gmail_payload_key(stored) == "envelope"
    record = ExternalRecord(
        external_id="t",
        payload={
            "messages": [
                {
                    "id": "m1",
                    "payload": {
                        "parts": [
                            {
                                "mimeType": "text/plain",
                                "body": {"data": "Plain!"},
                            }
                        ]
                    },
                    "envelope": {
                        "parts": [
                            {
                                "mimeType": "text/plain",
                                "body": {"data": "Hello!"},
                            }
                        ]
                    },
                }
            ]
        },
    )
    children = GmailConnector().to_anchored_children(record, stored)
    assert children[0]["materialized"].body == "Hello!"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="message payload"):
        _gmail_payload_key(old)


def test_gmail_headers_key_comes_from_the_package(monkeypatch):
    """Message header lists use the package header key."""
    from app.agentive.connectors.gmail import GmailConnector, _gmail_headers_key
    from app.connectors.package_discovery import rescan
    from app.services.connectors import ExternalRecord

    rescan()
    assert _gmail_headers_key(None) == "headers"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/gmail/v1/users/me/threads/1",
                    },
                    "output": {"select": "$", "payload_key": "payload"},
                }
            ]
        }
    }
    assert _gmail_headers_key(old) == "headers"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {"method": "GET", "path": "/custom/threads/1"},
                    "output": {"select": "$", "headers_key": "fields"},
                }
            ]
        }
    }
    assert _gmail_headers_key(stored) == "fields"
    record = ExternalRecord(
        external_id="t",
        payload={
            "messages": [
                {
                    "id": "m1",
                    "payload": {
                        "headers": [{"name": "subject", "value": "Wrong"}],
                        "fields": [{"name": "subject", "value": "Hello"}],
                    },
                }
            ]
        },
    )
    children = GmailConnector().to_anchored_children(record, stored)
    assert children[0]["materialized"].custom_fields["subject"] == "Hello"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="header list"):
        _gmail_headers_key(old)


def test_gmail_header_name_comes_from_the_package(monkeypatch):
    """Header records use the package name key."""
    from app.agentive.connectors.gmail import GmailConnector, _gmail_name_key
    from app.connectors.package_discovery import rescan
    from app.services.connectors import ExternalRecord

    rescan()
    assert _gmail_name_key(None) == "name"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/gmail/v1/users/me/threads/1",
                    },
                    "output": {"select": "$", "headers_key": "headers"},
                }
            ]
        }
    }
    assert _gmail_name_key(old) == "name"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {"method": "GET", "path": "/custom/threads/1"},
                    "output": {"select": "$", "name_key": "label"},
                }
            ]
        }
    }
    assert _gmail_name_key(stored) == "label"
    record = ExternalRecord(
        external_id="t",
        payload={
            "messages": [
                {
                    "id": "m1",
                    "payload": {
                        "headers": [
                            {"name": "subject", "value": "Wrong"},
                            {"label": "subject", "value": "Hello"},
                        ]
                    },
                }
            ]
        },
    )
    children = GmailConnector().to_anchored_children(record, stored)
    assert children[0]["materialized"].custom_fields["subject"] == "Hello"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="header name"):
        _gmail_name_key(old)


def test_gmail_header_value_comes_from_the_package(monkeypatch):
    """Header records use the package value key."""
    from app.agentive.connectors.gmail import GmailConnector, _gmail_value_key
    from app.connectors.package_discovery import rescan
    from app.services.connectors import ExternalRecord

    rescan()
    assert _gmail_value_key(None) == "value"
    old = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {
                        "method": "GET",
                        "path": "/gmail/v1/users/me/threads/1",
                    },
                    "output": {"select": "$", "name_key": "name"},
                }
            ]
        }
    }
    assert _gmail_value_key(old) == "value"
    stored = {
        "capabilities": {
            "actions": [
                {
                    "name": "get_thread",
                    "request": {"method": "GET", "path": "/custom/threads/1"},
                    "output": {"select": "$", "value_key": "text"},
                }
            ]
        }
    }
    assert _gmail_value_key(stored) == "text"
    record = ExternalRecord(
        external_id="t",
        payload={
            "messages": [
                {
                    "id": "m1",
                    "payload": {
                        "headers": [
                            {"name": "subject", "value": "Wrong", "text": "Hello"}
                        ]
                    },
                }
            ]
        },
    )
    children = GmailConnector().to_anchored_children(record, stored)
    assert children[0]["materialized"].custom_fields["subject"] == "Hello"
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(ValueError, match="header value"):
        _gmail_value_key(old)


def test_google_catalog_packages_name_their_client_env():
    """Drive, Gmail, and Sheets read client credentials from the package env lists."""
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import admitted_oauth

    rescan()
    for package_id in ("google_drive", "google_gmail", "google_sheets"):
        auth = admitted_oauth(package_id)
        assert auth is not None
        assert "GOOGLE_OAUTH_CLIENT_ID" in auth["client"]["id_env"]
        assert "GOOGLE_OAUTH_CLIENT_SECRET" in auth["client"]["secret_env"]


def test_saved_connector_must_belong_to_the_oauth_package():
    """A QuickBooks connector cannot start the Gmail consent URL."""
    from types import SimpleNamespace

    from app.agentive.api.connectors import _require_package_connector
    from app.api.errors import BadRequestError

    other = SimpleNamespace(
        auth_state={"catalog_slug": "quickbooks"},
        subclass_slug="quickbooks",
    )
    with pytest.raises(BadRequestError):
        _require_package_connector(other, "gmail")
    _require_package_connector(other, "quickbooks")


def test_oauth_modules_do_not_build_a_hardcoded_consent_url():
    """Consent URLs come from the admitted package, not a provider constant."""
    from app.services.connectors import gmail_oauth, quickbooks_oauth

    assert not hasattr(gmail_oauth, "build_consent_url")
    assert not hasattr(quickbooks_oauth, "build_consent_url")
    assert not hasattr(gmail_oauth, "GOOGLE_CONSENT_URL")
    assert not hasattr(quickbooks_oauth, "QBO_CONSENT_URL")


def test_reauth_consent_uses_one_package_builder():
    """Gmail and QuickBooks re-authorize share the admitted-package consent builder."""
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import consent_url_for_package

    rescan()
    gmail = consent_url_for_package(
        "gmail",
        stored_client_id="cid",
        redirect_uri="https://app.example/cb",
        state="signed",
    )
    books = consent_url_for_package(
        "quickbooks",
        stored_client_id="cid",
        redirect_uri="https://app.example/cb",
        state="signed",
    )
    assert gmail is not None and gmail.startswith("https://accounts.google.com/")
    assert books is not None and "appcenter.intuit.com" in books
    assert (
        consent_url_for_package(
            "missing_pkg",
            stored_client_id="cid",
            redirect_uri="https://app.example/cb",
            state="signed",
        )
        is None
    )


def test_gmail_reauth_consent_uses_the_package():
    """Re-authorize uses the Gmail package authorize URL and extra parameters."""
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import admitted_oauth, oauth_consent_url

    rescan()
    auth = admitted_oauth("gmail")
    assert auth is not None
    url = oauth_consent_url(
        auth,
        client_id="cid",
        redirect_uri="https://app.example/cb",
        state="signed",
    )
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert auth["client"]["id_env"] == [
        "GMAIL_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_ID",
    ]
    assert auth["redirect_env"] == ["GMAIL_OAUTH_REDIRECT_URI"]


def test_gmail_label_url_comes_from_the_package():
    """The mailbox label request uses the action declared by the Gmail package."""
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import package_action_url

    rescan()
    assert package_action_url("gmail", "list_labels") == (
        "https://gmail.googleapis.com/gmail/v1/users/me/labels"
    )


def test_stored_package_snapshot_wins_for_the_action_url():
    """A catalog connector keeps the action URL captured at install."""
    from types import SimpleNamespace

    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import (
        capture_package_snapshot,
        package_action_url,
    )

    rescan()
    connector = SimpleNamespace()
    capture_package_snapshot(connector, "gmail")
    assert connector.package_version == "1.0.0"
    stored = connector.package_manifest
    stored["capabilities"]["actions"][0]["request"]["path"] = "/gmail/v1/users/me/old"
    assert package_action_url("gmail", "list_labels", manifest=stored) == (
        "https://gmail.googleapis.com/gmail/v1/users/me/old"
    )
    assert package_action_url("gmail", "list_labels") == (
        "https://gmail.googleapis.com/gmail/v1/users/me/labels"
    )


def test_oauth_snapshot_fills_once_without_touching_tokens():
    """OAuth completion stores a missing snapshot and leaves tokens and the cursor."""
    from types import SimpleNamespace

    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import capture_package_snapshot

    rescan()
    connector = SimpleNamespace(
        auth_state={"access_token": "tok", "refresh_token": "ref"},
        sync_cursor="cursor-1",
        mapping_profile=None,
    )
    capture_package_snapshot(connector, "gmail", replace=False)
    assert connector.package_manifest["http"]["base_url"] == (
        "https://gmail.googleapis.com"
    )
    assert connector.auth_state == {"access_token": "tok", "refresh_token": "ref"}
    assert connector.sync_cursor == "cursor-1"
    assert connector.mapping_profile is None
    connector.package_manifest = {"http": {"base_url": "https://kept.example"}}
    capture_package_snapshot(connector, "gmail", replace=False)
    assert connector.package_manifest["http"]["base_url"] == "https://kept.example"


def test_first_party_catalog_packages_are_admitted():
    """The curated catalog is loaded as packages. Trusted ones stay inside Core's folder."""
    from app.connectors.package_discovery import _FIRST_PARTY

    admission = scan_packages()
    expected = {
        "calendly",
        "cloudflare",
        "github_issues",
        "gmail",
        "google_drive",
        "google_gmail",
        "google_sheets",
        "notion",
        "quickbooks",
        "quickbooks_mcp",
        "serper_web_search",
    }
    assert expected <= set(admission.packages)
    outside = scan_packages([_FIRST_PARTY])
    assert "gmail" not in outside.packages
    assert any(row["id"] == "gmail" for row in outside.rejected)


@pytest.mark.asyncio
async def test_example_contacts_installation_runs_declarative_sync(monkeypatch):
    """A new example_contacts installation pulls through DeclarativePackageSync."""
    from types import SimpleNamespace

    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import DeclarativePackageSync
    from app.services.connectors.sync_runtime import (
        adapter_for_connector,
        adapter_for_slug,
    )

    admitted = rescan()
    manifest = admitted.packages["example_contacts"]
    gmail = adapter_for_slug("gmail")
    assert not isinstance(gmail, DeclarativePackageSync)
    catalog = SimpleNamespace(
        subclass_slug="gmail", mapping_profile=None, package_manifest=None
    )
    assert not isinstance(adapter_for_connector(catalog), DeclarativePackageSync)

    installation = SimpleNamespace(
        subclass_slug="example_contacts",
        mapping_profile="package",
        package_manifest=manifest,
        package_config={},
        auth_state={},
        sync_cursor="",
    )
    adapter = adapter_for_connector(installation)
    assert isinstance(adapter, DeclarativePackageSync)

    async def fake(_manifest, _request, _config, _secrets, inputs=None, client=None):
        return {"items": [{"id": "c1", "name": "Ada"}]}

    monkeypatch.setattr("app.connectors.package_runtime.perform_request", fake)
    records = [record async for record in adapter.sync_pull(connector=installation)]
    assert records[0].external_id == "c1"
    assert records[0].payload["title"] == "Ada"


def test_core_specifier_must_include_this_core(tmp_path: Path, monkeypatch):
    """Admission rejects a package whose Core specifier excludes this version."""
    monkeypatch.setattr("app.__version__", "0.1.0")
    folder = tmp_path / "acme_crm"
    folder.mkdir()
    manifest_path = folder / "connector.yaml"
    manifest_path.write_text(_VALID + "core: '>=9'\n")
    manifest, reason = load_package(folder)
    assert manifest is None
    assert reason == "core requires >=9"
    manifest_path.write_text(_VALID + "core: 'not a specifier'\n")
    _manifest, reason = load_package(folder)
    assert reason == "core specifier is invalid"
    manifest_path.write_text(_VALID + "core: '>=0.1'\n")
    manifest, reason = load_package(folder)
    assert reason == ""
    assert manifest is not None


def test_folder_name_must_match_manifest_id(tmp_path: Path):
    """The directory name is the package id."""
    folder = tmp_path / "other_name"
    folder.mkdir()
    (folder / "connector.yaml").write_text(_VALID)
    manifest, reason = load_package(folder)
    assert manifest is None
    assert "folder name" in reason


@pytest.mark.asyncio
async def test_declarative_sync_maps_each_page(monkeypatch):
    """A sync source pages with {{cursor}} and maps external ids without writing Entries."""
    manifest = {
        "http": {"base_url": "https://acme.example"},
        "capabilities": {
            "sync": {
                "source": {
                    "request": {
                        "method": "GET",
                        "path": "/contacts",
                        "query": {"cursor": "{{cursor}}"},
                    },
                    "items": "$.items",
                    "paginate": {"next_cursor": "$.next", "max_pages": 5},
                },
                "map": {
                    "external_id": "$.id",
                    "title": "$.name",
                    "fields": {"email": "$.email"},
                },
            }
        },
    }
    pages = {
        "": {
            "items": [{"id": "1", "name": "Ada", "email": "a@acme.example"}],
            "next": "2",
        },
        "2": {
            "items": [{"id": "2", "name": "Bea", "email": "b@acme.example"}],
            "next": None,
        },
    }

    async def fake_request(
        manifest, request, config, secrets, inputs=None, client=None
    ):
        return pages[(inputs or {}).get("cursor") or ""]

    monkeypatch.setattr("app.connectors.package_runtime.perform_request", fake_request)
    rows, cursor = await pull_declared_sync(
        manifest, {"base_url": "https://acme.example"}, {}
    )
    assert cursor == "2"
    assert [
        (row["external_id"], row["title"], row["fields"]["email"]) for row in rows
    ] == [
        ("1", "Ada", "a@acme.example"),
        ("2", "Bea", "b@acme.example"),
    ]


@pytest.mark.asyncio
async def test_unregistered_package_sync_uses_the_manifest(monkeypatch):
    """A package id with no adapter still maps records and keeps the cursor."""
    from types import SimpleNamespace

    from app.services.connectors.sync_runtime import adapter_for_slug

    manifest = {
        "id": "acme_sync_demo",
        "capabilities": {
            "sync": {
                "entry_type": "contact",
                "conflict_policy": "mirror_only",
                "source": {
                    "request": {"method": "GET", "path": "/x"},
                    "items": "$.items",
                },
                "map": {"external_id": "$.id", "title": "$.name"},
            }
        },
    }
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: manifest if package_id == "acme_sync_demo" else None,
    )

    async def fake_pull(manifest, config, secrets, start_cursor=""):
        assert start_cursor == "1"
        return ([{"external_id": "2", "title": "Bea", "fields": {}}], "3")

    monkeypatch.setattr("app.connectors.package_runtime.pull_declared_sync", fake_pull)
    adapter = adapter_for_slug("acme_sync_demo")
    connector = SimpleNamespace(auth_state={}, package_config={}, sync_cursor="1")
    records = [record async for record in adapter.sync_pull(connector=connector)]
    assert [record.external_id for record in records] == ["2"]
    assert connector.sync_cursor == "3"
    assert adapter.to_entry(records[0]).entry_type_key == "contact"
    assert adapter.to_entry(records[0]).title == "Bea"


@pytest.mark.asyncio
async def test_disabled_package_installation_is_not_synced(monkeypatch):
    """The scheduler does not pull a package installation that is still disabled."""
    from types import SimpleNamespace

    from app.services.connectors.sync_runtime import sync_one_connector

    def boom(slug):
        raise AssertionError("disabled package must not resolve an adapter")

    monkeypatch.setattr("app.services.connectors.sync_runtime.adapter_for_slug", boom)
    connector = SimpleNamespace(
        id="c1",
        mapping_profile="package",
        enabled=False,
        subclass_slug="acme",
    )
    stats = await sync_one_connector(connector)
    assert stats["skipped"] == 1
    assert stats["created"] == 0


def test_declared_sync_source_replaces_a_registered_adapter(monkeypatch):
    """A package source is used even when a Python adapter is registered for that id."""
    from app.services.connectors import sync_runtime

    sentinel = object()
    monkeypatch.setattr(sync_runtime, "get_sync_connector", lambda slug: sentinel)
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {
            "id": "gmail",
            "capabilities": {
                "sync": {
                    "source": {
                        "request": {"method": "GET", "path": "/x"},
                        "items": "$.items",
                    },
                    "map": {"external_id": "$.id"},
                }
            },
        },
    )
    assert sync_runtime.adapter_for_slug("gmail") is not sentinel
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {"capabilities": {"sync": {"entry_type": "mail"}}},
    )
    assert sync_runtime.adapter_for_slug("gmail") is sentinel


def test_admitted_package_records_its_yaml_fingerprint(tmp_path: Path):
    """Admission records the SHA-256 of connector.yaml."""
    import hashlib

    root = tmp_path / "root"
    package = root / "acme_crm"
    package.mkdir(parents=True)
    (package / "connector.yaml").write_text(_VALID, encoding="utf-8")
    admission = scan_packages([root])
    digest = hashlib.sha256(_VALID.encode()).hexdigest()
    assert admission.fingerprints["acme_crm"] == digest


@pytest.mark.smoke
def test_cli_scaffolds_each_connector_kind(tmp_path):
    """new writes a package that validate accepts for every kind."""
    from app.cli import main

    for kind in ("actions", "mcp", "sync", "channel"):
        package_id = f"acme_{kind}"
        assert (
            main(
                [
                    "connectors",
                    "new",
                    package_id,
                    "--kind",
                    kind,
                    "--directory",
                    str(tmp_path),
                ]
            )
            == 0
        )
        assert main(["connectors", "validate", str(tmp_path / package_id)]) == 0


def test_stdio_config_reference_reads_package_config(monkeypatch):
    """A config reference uses the installation configuration, never PATH."""
    from app.agentive.connectors.mcp_client import _build_spawn_env
    from app.connectors.package_discovery import PackageAdmission

    manifest = {
        "capabilities": {
            "mcp": {
                "env": {
                    "ACME_REGION": {"config": "region"},
                    "PATH": {"config": "path"},
                }
            }
        }
    }
    monkeypatch.setattr(
        "app.connectors.package_discovery.current_admission",
        lambda: PackageAdmission(packages={"acme_stdio": manifest}),
    )
    env = _build_spawn_env(
        {"catalog_slug": "acme_stdio"},
        config={"region": "eu", "path": "/tmp/evil"},
    )
    assert env["ACME_REGION"] == "eu"
    assert env.get("PATH") != "/tmp/evil"


def test_stdio_package_env_literal_is_passed_and_path_is_not():
    """A package may pass only the env keys its manifest names."""
    from app.agentive.connectors.mcp_client import _build_spawn_env
    from app.connectors.package_discovery import rescan

    rescan()
    env = _build_spawn_env(
        {
            "catalog_slug": "serper_web_search",
            "env": {"SERPER_API_KEY": "k", "PATH": "/tmp/evil"},
        }
    )
    assert env["SERPER_API_KEY"] == "k"
    assert env["SERPER_REGION"] == "us"
    assert env.get("PATH") != "/tmp/evil"


@pytest.mark.asyncio
async def test_package_requests_pin_the_outbound_host(monkeypatch):
    """Declarative and token requests keep the validated address for the call."""
    import httpx

    from app.connectors.package_runtime import (
        call_mcp_tool,
        perform_request,
        post_oauth_token,
    )

    pinned: list[str] = []

    class _Pin:
        def __init__(self, url: str) -> None:
            pinned.append(url)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("app.connectors.package_runtime.pin_public_dns", _Pin)

    async def allow(url: str) -> None:
        return None

    monkeypatch.setattr(
        "app.connectors.package_runtime.validate_outbound_http_url", allow
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    manifest = {"http": {"base_url": "https://acme.example"}, "auth": {"type": "none"}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await perform_request(
            manifest,
            {"method": "GET", "path": "/ping"},
            {},
            {},
            client=client,
        )
        await post_oauth_token(
            {
                "token_url": "https://oauth.example/token",
                "client_authentication": "client_secret_post",
            },
            {"grant_type": "authorization_code"},
            client_id="id",
            client_secret="sec",
            client=client,
        )
        await call_mcp_tool(
            {"url": "{{config.base_url}}/mcp"},
            "search",
            {},
            client=client,
            config={"base_url": "https://mcp.example"},
        )
        with pytest.raises(ValueError, match="secrets"):
            await call_mcp_tool(
                {"url": "https://mcp.example/{{secret.token}}"},
                "search",
                {},
                client=client,
            )
    assert pinned == [
        "https://acme.example/ping",
        "https://oauth.example/token",
        "https://mcp.example/mcp",
    ]


def test_stdio_env_injects_secret_and_oauth_refs(monkeypatch):
    """Typed secret and oauth refs are passed. PATH stays the safe default."""
    from app.agentive.connectors.mcp_client import _build_spawn_env
    from app.connectors import package_discovery as discovery

    first = discovery.rescan()
    manifest = dict(first.packages["serper_web_search"])
    capabilities = dict(manifest["capabilities"])
    mcp = dict(capabilities["mcp"])
    env_spec = dict(mcp.get("env") or {})
    env_spec["ACME_SECRET"] = {"secret": "api_key"}
    env_spec["ACME_TOKEN"] = {"oauth": "access_token"}
    mcp["env"] = env_spec
    capabilities["mcp"] = mcp
    manifest["capabilities"] = capabilities
    packages = dict(first.packages)
    packages["serper_web_search"] = manifest
    monkeypatch.setattr(
        discovery,
        "current_admission",
        lambda: discovery.PackageAdmission(
            packages=packages, rejected=list(first.rejected)
        ),
    )
    env = _build_spawn_env(
        {
            "catalog_slug": "serper_web_search",
            "api_key": "secret-value",
            "oauth": {"access_token": "tok"},
            "env": {"PATH": "/tmp/evil"},
        }
    )
    assert env["ACME_SECRET"] == "secret-value"
    assert env["ACME_TOKEN"] == "tok"
    assert env.get("PATH") != "/tmp/evil"


@pytest.mark.asyncio
async def test_token_exchange_uses_the_package_token_url(monkeypatch):
    """Gmail posts the secret in the body. QuickBooks sends HTTP Basic."""
    import httpx

    from app.connectors.package_discovery import rescan
    from app.services.connectors import gmail_oauth, quickbooks_oauth

    rescan()

    def package_auth(package_id: str):
        if package_id == "gmail":
            return {
                "type": "oauth2",
                "token_url": "https://example.com/gmail-token",
                "client_authentication": "client_secret_post",
                "client": {
                    "id_env": ["GMAIL_OAUTH_CLIENT_ID"],
                    "secret_env": ["GMAIL_OAUTH_CLIENT_SECRET"],
                },
            }
        return {
            "type": "oauth2",
            "token_url": "https://example.com/quickbooks-token",
            "client_authentication": "client_secret_basic",
            "client": {
                "id_env": ["QUICKBOOKS_CLIENT_ID"],
                "secret_env": ["QUICKBOOKS_CLIENT_SECRET"],
            },
        }

    monkeypatch.setattr("app.connectors.package_runtime.admitted_oauth", package_auth)
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_ID", "gmail-id")
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_SECRET", "gmail-secret")
    monkeypatch.setenv("QUICKBOOKS_CLIENT_ID", "qb-id")
    monkeypatch.setenv("QUICKBOOKS_CLIENT_SECRET", "qb-secret")

    async def fake_guard(url: str) -> None:
        return None

    monkeypatch.setattr(
        "app.connectors.package_runtime.validate_outbound_http_url", fake_guard
    )
    seen: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                str(request.url),
                request.headers.get("authorization") or "",
                request.content.decode(),
            )
        )
        return httpx.Response(200, json={"access_token": "token"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await gmail_oauth.exchange_code("code", http_client=client)
        await quickbooks_oauth.exchange_code("code", http_client=client)

    assert seen[0][0] == "https://example.com/gmail-token"
    assert seen[0][1] == ""
    assert "client_secret=gmail-secret" in seen[0][2]
    assert seen[1][0] == "https://example.com/quickbooks-token"
    assert seen[1][1].startswith("Basic ")
    assert "client_secret" not in seen[1][2]


@pytest.mark.asyncio
async def test_token_exchange_refuses_without_the_package(monkeypatch):
    """A missing package does not post the code to a hardcoded token URL."""
    from app.api.errors import ConnectorAuthError
    from app.services.connectors import gmail_oauth, quickbooks_oauth

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_oauth", lambda _package_id: None
    )
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_ID", "gmail-id")
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_SECRET", "gmail-secret")
    monkeypatch.setenv("QUICKBOOKS_CLIENT_ID", "qb-id")
    monkeypatch.setenv("QUICKBOOKS_CLIENT_SECRET", "qb-secret")
    with pytest.raises(ConnectorAuthError, match="not admitted"):
        await gmail_oauth.exchange_code("code")
    with pytest.raises(ConnectorAuthError, match="not admitted"):
        await quickbooks_oauth.exchange_code("code")


def test_catalog_lookup_prefers_the_package_url(monkeypatch):
    """The library row shows the package URL, not a stale catalog copy."""
    from app.connectors.catalog_loader import get_catalog_entry

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {
                "name": "Package Notion",
                "description": "From the package.",
                "auth": {"type": "mcp_oauth"},
                "capabilities": {
                    "mcp": {
                        "transport": "streamable_http",
                        "url": "https://pkg.example/mcp",
                    }
                },
            }
            if package_id == "notion"
            else None
        ),
    )
    shown = get_catalog_entry("notion")
    assert shown["url"] == "https://pkg.example/mcp"
    assert shown["display_name"] == "Package Notion"
    assert shown["description"] == "From the package."
    assert shown["auth"]["type"] == "mcp_oauth"


def test_catalog_mcp_mount_uses_the_package():
    """A catalog URL does not override the admitted package."""
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import mcp_mount_fields

    rescan()
    notion = mcp_mount_fields(
        {"slug": "notion", "url": "https://old.example/mcp", "transport": "stdio"}
    )
    assert notion["url"] == "https://mcp.notion.com/mcp"
    assert notion["transport"] == "streamable_http"
    local = mcp_mount_fields(
        {"slug": "quickbooks_mcp", "command": "old", "args": ["x"]}
    )
    assert local["command"] == "npx"
    assert "github:intuit/quickbooks-online-mcp-server" in local["args"]
    untouched = mcp_mount_fields(
        {
            "slug": "not-a-package",
            "url": "https://mcp.example.test/mcp",
            "transport": "streamable_http",
        }
    )
    assert untouched["url"] == "https://mcp.example.test/mcp"


@pytest.mark.asyncio
async def test_whatsapp_package_does_not_copy_grants():
    """Add refuses a second WhatsApp installation. The grant ciphertext stays put."""
    from app.connectors.channel_packages import ChannelPackageRegistry
    from app.connectors.package_channels import register_packaged_managed_text

    register_packaged_managed_text(ChannelPackageRegistry())

    import importlib.util
    from types import SimpleNamespace

    from app.agentive.services.connector_packages import install_package
    from app.api.errors import BadRequestError
    from app.connectors.package_discovery import first_party_package_dir, rescan

    rescan()
    path = first_party_package_dir("whatsapp") / "adapter" / "grants.py"
    spec = importlib.util.spec_from_file_location("whatsapp_grants", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    existing_grant = module.existing_grant
    binding = SimpleNamespace(
        id="n.WhatsAppChannelBinding.1",
        enabled=True,
        api_key_ciphertext="cipher-key",
        webhook_secret_ciphertext="cipher-hook",
    )
    assert existing_grant(binding) == {
        "id": "n.WhatsAppChannelBinding.1",
        "edge": "USES_WHATSAPP_LINE",
        "api_key_ciphertext": "cipher-key",
        "webhook_secret_ciphertext": "cipher-hook",
    }
    from app.connectors.package_channels import read_host_grant

    status = module.grant_status(binding)
    assert status == {
        "edge": "USES_WHATSAPP_LINE",
        "present": True,
        "id": "n.WhatsAppChannelBinding.1",
        "enabled": True,
    }
    assert "cipher-key" not in str(status)
    assert module.grant_status(None) == {
        "edge": "USES_WHATSAPP_LINE",
        "present": False,
    }
    assert read_host_grant("whatsapp", binding) == status
    assert read_host_grant("gmail", binding) is None
    with pytest.raises(BadRequestError, match="grants"):
        await install_package(
            actor_id="u",
            workspace_id="ws",
            package_id="whatsapp",
            values={},
        )


@pytest.mark.asyncio
async def test_package_catalogue_attaches_the_whatsapp_grant(monkeypatch):
    """The package list carries grant status and not the ciphertext."""
    from app.connectors.channel_packages import ChannelPackageRegistry
    from app.connectors.package_channels import register_packaged_managed_text

    register_packaged_managed_text(ChannelPackageRegistry())

    from types import SimpleNamespace

    from app.agentive.services.connector_packages import attach_host_grants

    binding = SimpleNamespace(
        id="b1",
        enabled=True,
        api_key_ciphertext="cipher-key",
        webhook_secret_ciphertext="cipher-hook",
    )

    async def find_binding(_workspace_id):
        return binding

    monkeypatch.setattr(
        "app.agentive.services.whatsapp_channel_binding.binding_for_workspace",
        find_binding,
    )
    packages = [
        {"id": "whatsapp", "host_managed": True},
        {"id": "gmail", "host_managed": False},
    ]
    await attach_host_grants(packages, "ws")
    assert packages[0]["grant"]["id"] == "b1"
    assert packages[0]["grant"]["edge"] == "USES_WHATSAPP_LINE"
    assert "cipher-key" not in str(packages[0]["grant"])
    assert "grant" not in packages[1]


@pytest.mark.asyncio
async def test_host_grant_binding_comes_from_the_package(monkeypatch):
    """Only a package that declares a binding loader reads that binding."""
    from app.connectors.channel_packages import ChannelPackageRegistry
    from app.connectors.package_channels import register_packaged_managed_text

    register_packaged_managed_text(ChannelPackageRegistry())

    from app.agentive.services.connector_packages import attach_host_grants

    calls = []

    async def find_binding(workspace_id):
        calls.append(workspace_id)
        return None

    monkeypatch.setattr(
        "app.agentive.services.whatsapp_channel_binding.binding_for_workspace",
        find_binding,
    )
    packages = [
        {"id": "whatsapp", "host_managed": True},
        {"id": "acme_host", "host_managed": True},
    ]
    await attach_host_grants(packages, "ws-2")
    assert calls == ["ws-2"]
    assert packages[0]["grant"]["present"] is False
    assert packages[0]["grant"]["edge"] == "USES_WHATSAPP_LINE"
    assert "grant" not in packages[1]


def test_web_chat_package_registers_the_managed_channel():
    """The first-party Web Chat package supplies the management channel."""
    from app.connectors.channel_packages import ChannelPackageRegistry
    from app.connectors.package_channels import register_packaged_managed_text
    from app.connectors.package_discovery import rescan

    admitted = rescan()
    assert "public_web_chat" in admitted.packages
    registry = ChannelPackageRegistry()
    register_packaged_managed_text(registry)
    package = registry.resolve("public_web_chat")
    assert package is not None
    assert package.definition.slug == "public_web_chat"


@pytest.mark.asyncio
async def test_upgrade_keeps_shared_fields_and_rollback_restores_them(monkeypatch):
    """Upgrade drops removed fields and rollback puts that installation back."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "ab" * 32)
    from types import SimpleNamespace

    from app.agentive.services.connector_packages import (
        rollback_package,
        upgrade_package,
    )
    from app.agentive.services.connector_registry_node import decrypt_auth_state
    from app.connectors.package_discovery import PackageAdmission

    stored = {
        "version": "1.0.0",
        "capabilities": {"actions": [{"name": "find"}]},
        "configuration": [
            {"key": "base_url", "secret": False},
            {"key": "api_key", "secret": True},
            {"key": "old_token", "secret": True},
        ],
    }
    newer = {
        "version": "2.0.0",
        "capabilities": {"actions": [{"name": "search"}]},
        "configuration": [
            {"key": "base_url", "secret": False},
            {"key": "api_key", "secret": True},
        ],
    }
    connector = SimpleNamespace(
        subclass_slug="acme",
        package_version="1.0.0",
        package_config={"base_url": "https://acme.example", "region": "us"},
        package_manifest=stored,
        package_fingerprint="",
        package_previous={},
        package_revision=1,
        enabled=True,
        health_status="healthy",
        auth_state={"api_key": "live", "old_token": "gone"},
    )

    async def save():
        return None

    connector.save = save

    async def owned(*_args, **_kwargs):
        return connector

    monkeypatch.setattr("app.agentive.services.connector_packages._owned", owned)
    monkeypatch.setattr(
        "app.agentive.services.connector_packages._manifest", lambda _package_id: newer
    )
    monkeypatch.setattr(
        "app.agentive.services.connector_packages.current_admission",
        lambda: PackageAdmission(packages={"acme": newer}),
    )

    await upgrade_package("c1", "u1")
    assert connector.package_version == "2.0.0"
    assert connector.package_config == {"base_url": "https://acme.example"}
    assert decrypt_auth_state(connector.auth_state) == {"api_key": "live"}
    assert connector.enabled is False
    assert connector.package_manifest["capabilities"]["actions"][0]["name"] == "search"

    await rollback_package("c1", "u1")
    assert connector.package_version == "1.0.0"
    assert connector.package_config["region"] == "us"
    assert decrypt_auth_state(connector.auth_state)["old_token"] == "gone"
    assert connector.package_manifest["capabilities"]["actions"][0]["name"] == "find"
    assert connector.package_previous == {}


@pytest.mark.asyncio
async def test_null_secret_is_revoked_and_a_blank_secret_is_kept(monkeypatch):
    """JSON null removes a stored secret. A blank value leaves it in place."""
    from types import SimpleNamespace

    from app.agentive.services.connector_packages import configure_package
    from app.agentive.services.connector_registry_node import decrypt_auth_state
    from app.connectors.package_discovery import PackageAdmission

    manifest = {
        "capabilities": {"actions": [{"name": "ping"}]},
        "configuration": [
            {"key": "base_url", "type": "url", "label": "URL"},
            {"key": "api_key", "type": "secret", "label": "Key", "required": True},
        ],
    }
    connector = SimpleNamespace(
        id="c1",
        subclass_slug="acme",
        package_manifest=manifest,
        package_config={"base_url": "https://acme.example"},
        package_revision=1,
        auth_state={"api_key": "live"},
        enabled=True,
        health_status="healthy",
    )

    async def save():
        return None

    async def owned(*_args, **_kwargs):
        return connector

    connector.save = save
    monkeypatch.setattr("app.agentive.services.connector_packages._owned", owned)
    monkeypatch.setattr(
        "app.agentive.services.connector_packages.current_admission",
        lambda: PackageAdmission(packages={"acme": manifest}),
    )

    await configure_package(
        "c1", "u1", {"base_url": "https://acme.example", "api_key": ""}
    )
    assert decrypt_auth_state(connector.auth_state)["api_key"] == "live"

    await configure_package(
        "c1", "u1", {"base_url": "https://acme.example", "api_key": None}
    )
    assert "api_key" not in decrypt_auth_state(connector.auth_state)


@pytest.mark.asyncio
async def test_failed_review_keeps_the_staged_write(monkeypatch):
    """A write that fails the fresh check can be approved again."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.services import connector_packages
    from app.api.errors import ResourceConflictError
    from app.connectors.package_discovery import PackageAdmission

    pending = {
        "id": "rev1",
        "kind": "action",
        "action": "create",
        "input": {"name": "Ada"},
        "status": "pending",
    }
    connector = SimpleNamespace(
        id="c1",
        subclass_slug="acme",
        pending_review=dict(pending),
        package_manifest={
            "capabilities": {
                "actions": [
                    {
                        "name": "create",
                        "effect": "write",
                        "request": {"method": "POST", "path": "/x"},
                    }
                ]
            }
        },
    )

    async def save():
        return None

    connector.save = save

    async def owned(*_args, **_kwargs):
        return connector

    async def changed(*_args, **_kwargs):
        raise ResourceConflictError("Connector changed before the action ran")

    monkeypatch.setattr(connector_packages, "_owned", owned)
    monkeypatch.setattr(connector_packages, "_execute", changed)
    monkeypatch.setattr(
        connector_packages,
        "current_admission",
        lambda: PackageAdmission(packages={"acme": {}}),
    )
    monkeypatch.setattr(
        connector_packages.Connector, "get", AsyncMock(return_value=connector)
    )
    with pytest.raises(ResourceConflictError):
        await connector_packages.approve_review("c1", "u1", "rev1")
    assert connector.pending_review["id"] == "rev1"
    assert connector.pending_review["input"] == {"name": "Ada"}


@pytest.mark.asyncio
async def test_disabled_mcp_review_does_not_call_the_tool(monkeypatch):
    """A staged MCP write is not sent when the installation is no longer enabled."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.services import connector_packages
    from app.api.errors import ResourceConflictError
    from app.connectors.package_discovery import PackageAdmission

    pending = {
        "id": "rev2",
        "kind": "mcp",
        "action": "create_page",
        "input": {"title": "Ada"},
        "status": "pending",
    }
    connector = SimpleNamespace(
        id="c1",
        subclass_slug="acme",
        enabled=False,
        package_revision=1,
        package_config={"base_url": "https://acme.example"},
        pending_review=dict(pending),
        package_manifest={
            "capabilities": {
                "mcp": {
                    "transport": "streamable_http",
                    "url": "https://acme.example/mcp",
                }
            }
        },
    )

    async def save():
        return None

    connector.save = save

    async def owned(*_args, **_kwargs):
        return connector

    async def tool(*_args, **_kwargs):
        raise AssertionError("tool must not run")

    monkeypatch.setattr(connector_packages, "_owned", owned)
    monkeypatch.setattr(connector_packages, "call_mcp_tool", tool)
    monkeypatch.setattr(
        connector_packages,
        "current_admission",
        lambda: PackageAdmission(packages={"acme": {}}),
    )
    monkeypatch.setattr(
        connector_packages.Connector, "get", AsyncMock(return_value=connector)
    )
    with pytest.raises(ResourceConflictError):
        await connector_packages.approve_review("c1", "u1", "rev2")
    assert connector.pending_review["action"] == "create_page"
    assert connector.pending_review["input"] == {"title": "Ada"}


@pytest.mark.asyncio
async def test_health_status_comes_from_the_package(monkeypatch):
    """A stored health status list wins. An older snapshot uses the package."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.services import connector_packages
    from app.connectors.package_discovery import PackageAdmission

    seen: dict = {}

    async def probe(*_args, **kwargs):
        seen["ok"] = kwargs.get("ok_statuses")
        return {"ok": True}

    connector = SimpleNamespace(
        id="c1",
        enabled=False,
        updated_at="t1",
        package_revision=1,
        subclass_slug="acme",
        package_config={},
        auth_state={"access_token": "tok"},
        health_status="unknown",
        last_error=None,
        package_manifest={
            "id": "acme_books",
            "trust": {"tier": "declarative"},
            "http": {"base_url": "https://acme.example"},
            "health": {
                "request": {"method": "GET", "path": "/health"},
                "expect_status": [204],
            },
            "capabilities": {"actions": [{"name": "ping"}]},
        },
    )
    connector.save = AsyncMock()

    async def owned(*_args, **_kwargs):
        return connector

    monkeypatch.setattr(connector_packages, "_owned", owned)
    monkeypatch.setattr(connector_packages, "perform_request", probe)
    monkeypatch.setattr(
        connector_packages, "sync_package_action_tools", lambda _row: None
    )
    monkeypatch.setattr(
        connector_packages,
        "current_admission",
        lambda: PackageAdmission(packages={"acme": {}}),
    )
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {
                "health": {
                    "request": {"method": "GET", "path": "/health"},
                    "expect_status": [201],
                }
            }
            if package_id == "acme"
            else None
        ),
    )
    monkeypatch.setattr(
        connector_packages.Connector, "get", AsyncMock(return_value=connector)
    )
    await connector_packages.set_enabled("c1", "u1", True)
    assert seen["ok"] == [204]
    assert connector.auth_state["access_token"] == "tok"
    assert connector.subclass_slug == "acme"
    connector.package_manifest["health"] = {
        "request": {"method": "GET", "path": "/health"}
    }
    await connector_packages.set_enabled("c1", "u1", True)
    assert seen["ok"] == [201]
    connector.package_manifest["health"] = {
        "request": {"method": "GET", "path": "/health"},
        "expect_status": ["ok"],
    }
    with pytest.raises(ValueError, match="does not declare health statuses"):
        await connector_packages.set_enabled("c1", "u1", True)
    assert connector.auth_state["access_token"] == "tok"


@pytest.mark.asyncio
async def test_enable_does_not_override_a_concurrent_disable(monkeypatch):
    """A health probe that finishes after another write does not enable the row."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.services import connector_packages
    from app.api.errors import ResourceConflictError
    from app.connectors.package_discovery import PackageAdmission

    original = SimpleNamespace(
        id="c1",
        enabled=False,
        updated_at="t1",
        package_revision=1,
        subclass_slug="acme",
        package_config={},
        auth_state={},
        health_status="unknown",
        last_error=None,
        package_manifest={
            "trust": {"tier": "declarative"},
            "http": {"base_url": "https://acme.example"},
            "health": {"request": {"method": "GET", "path": "/health"}},
            "capabilities": {"actions": [{"name": "ping"}]},
        },
    )
    later = SimpleNamespace(
        id="c1",
        enabled=False,
        updated_at="t2",
        package_revision=1,
        subclass_slug="acme",
    )

    async def save():
        return None

    original.save = save
    later.save = save

    async def owned(*_args, **_kwargs):
        return original

    async def probe(*_args, **_kwargs):
        return {"ok": True}

    monkeypatch.setattr(connector_packages, "_owned", owned)
    monkeypatch.setattr(connector_packages, "perform_request", probe)
    monkeypatch.setattr(
        connector_packages,
        "current_admission",
        lambda: PackageAdmission(packages={"acme": {}}),
    )
    monkeypatch.setattr(
        connector_packages.Connector, "get", AsyncMock(return_value=later)
    )
    with pytest.raises(ResourceConflictError):
        await connector_packages.set_enabled("c1", "u1", True)
    assert later.enabled is False


@pytest.mark.asyncio
async def test_probe_failure_does_not_overwrite_a_newer_row(monkeypatch):
    """A failed probe does not record itself over a row that changed."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import httpx

    from app.agentive.services import connector_packages
    from app.api.errors import ServiceUnavailableError
    from app.connectors.package_discovery import PackageAdmission

    original = SimpleNamespace(
        id="c1",
        enabled=False,
        updated_at="t1",
        package_revision=1,
        subclass_slug="acme",
        package_config={},
        auth_state={},
        health_status="unknown",
        last_error=None,
        package_manifest={
            "trust": {"tier": "declarative"},
            "health": {"request": {"method": "GET", "path": "/health"}},
            "capabilities": {"actions": [{"name": "ping"}]},
        },
    )
    later = SimpleNamespace(
        id="c1",
        enabled=False,
        updated_at="t2",
        package_revision=1,
        health_status="healthy",
        last_error=None,
    )

    async def save():
        return None

    original.save = save
    later.save = save

    async def owned(*_args, **_kwargs):
        return original

    async def probe(*_args, **_kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(connector_packages, "_owned", owned)
    monkeypatch.setattr(connector_packages, "perform_request", probe)
    monkeypatch.setattr(
        connector_packages,
        "current_admission",
        lambda: PackageAdmission(packages={"acme": {}}),
    )
    monkeypatch.setattr(
        connector_packages.Connector, "get", AsyncMock(return_value=later)
    )
    with pytest.raises(ServiceUnavailableError):
        await connector_packages.set_enabled("c1", "u1", True)
    assert later.health_status == "healthy"
    assert later.last_error is None


def test_conflict_policy_follows_the_package_source(monkeypatch):
    """A declared sync source supplies the conflict policy shown for that id."""
    from app.agentive.api.connectors import _derive_conflict_policy

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {
                "id": "gmail",
                "capabilities": {
                    "sync": {
                        "conflict_policy": "manual_resolve",
                        "source": {
                            "request": {"method": "GET", "path": "/x"},
                            "items": "$.items",
                        },
                        "map": {"external_id": "$.id"},
                    }
                },
            }
            if package_id == "gmail"
            else None
        ),
    )
    assert _derive_conflict_policy("gmail") == "manual_resolve"

    class _Registered:
        conflict_policy = "mirror_only"

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: None,
    )
    monkeypatch.setattr(
        "app.services.connectors.sync_runtime.get_sync_connector",
        lambda slug: _Registered(),
    )
    assert _derive_conflict_policy("gmail") == "mirror_only"


def test_installed_sync_source_is_used_until_upgrade():
    """Sync uses the manifest captured at Add, not a newer admitted source."""
    from types import SimpleNamespace

    from app.services.connectors.sync_runtime import adapter_for_connector

    connector = SimpleNamespace(
        subclass_slug="acme",
        mapping_profile="package",
        package_manifest={
            "id": "acme",
            "capabilities": {
                "sync": {
                    "conflict_policy": "manual_resolve",
                    "source": {
                        "request": {"method": "GET", "path": "/old"},
                        "items": "$.items",
                    },
                    "map": {"external_id": "$.id"},
                }
            },
        },
    )
    adapter = adapter_for_connector(connector)
    assert adapter.conflict_policy == "manual_resolve"
    assert (
        adapter.manifest["capabilities"]["sync"]["source"]["request"]["path"] == "/old"
    )


@pytest.mark.asyncio
async def test_package_patch_cannot_replace_the_interval(monkeypatch):
    """A generic connector patch cannot override a package installation."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.services.connector_registry_node import update_connector

    row = SimpleNamespace(
        mapping_profile="package",
        sync_interval_seconds=3600,
        subclass_slug="acme",
    )
    monkeypatch.setattr(
        "app.agentive.services.connector_registry_node.Connector.get",
        AsyncMock(return_value=row),
    )
    with pytest.raises(ValueError, match="package API"):
        await update_connector("c1", sync_interval_seconds=30)
    assert row.sync_interval_seconds == 3600
    plain = SimpleNamespace(mapping_profile=None, subclass_slug="custom")
    monkeypatch.setattr(
        "app.agentive.services.connector_registry_node.Connector.get",
        AsyncMock(return_value=plain),
    )
    with pytest.raises(ValueError, match="package API"):
        await update_connector("c2", mapping_profile="package")
    assert plain.mapping_profile is None


@pytest.mark.asyncio
async def test_deleting_a_package_installation_disables_it_first(monkeypatch):
    """Delete turns a package installation off before the row is removed."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.services.connector_registry_node import delete_connector

    seen = {}
    row = SimpleNamespace(mapping_profile="package", enabled=True)

    async def save():
        seen["saved_enabled"] = row.enabled

    async def delete():
        seen["deleted_while_enabled"] = row.enabled

    row.save = save
    row.delete = delete
    monkeypatch.setattr(
        "app.agentive.services.connector_registry_node.Connector.get",
        AsyncMock(return_value=row),
    )
    assert await delete_connector("c1") is True
    assert seen["saved_enabled"] is False
    assert seen["deleted_while_enabled"] is False


def test_connector_response_uses_the_captured_interval_and_enabled_flag():
    """The list row shows the package interval and whether the installation is on."""
    from types import SimpleNamespace

    from app.agentive.api.connectors import _to_response

    connector = SimpleNamespace(
        id="c1",
        kind="custom",
        owner="u",
        auth_state={},
        sync_cursor=None,
        mapping_profile="package",
        permissions=[],
        capabilities=["sync"],
        subclass_slug="acme",
        sync_interval_seconds=30,
        enabled=True,
        package_manifest={"capabilities": {"sync": {"interval_seconds": 3600}}},
        last_synced_at=None,
        connection_mode="shared",
        label="Acme",
        workspace_id="ws",
        health_status="healthy",
        last_error=None,
        last_health_at=None,
        created_at="t",
        updated_at="t",
    )
    shown = _to_response(connector)
    assert shown.enabled is True
    assert shown.sync_interval_seconds == 3600


def test_package_sync_interval_comes_from_the_manifest():
    """The scheduler interval is the one declared by the package."""
    from types import SimpleNamespace

    from app.agentive.services.connector_packages import _apply_sync_interval

    row = SimpleNamespace(sync_interval_seconds=300)
    _apply_sync_interval(row, {"capabilities": {"sync": {"interval_seconds": 3600}}})
    assert row.sync_interval_seconds == 3600
    row.mapping_profile = "package"
    row.package_manifest = {"capabilities": {"sync": {"interval_seconds": 3600}}}
    from app.agentive.services.connector_packages import installed_sync_interval

    assert installed_sync_interval(row) == 3600
    _apply_sync_interval(row, {"capabilities": {"sync": {"interval_seconds": True}}})
    assert row.sync_interval_seconds == 3600
    _apply_sync_interval(row, {"capabilities": {"actions": []}})
    assert row.sync_interval_seconds == 3600


def test_quickbooks_sync_interval_is_a_configuration_field():
    """The QuickBooks interval is a package configuration field."""
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import admitted_manifest

    rescan()
    quickbooks = admitted_manifest("quickbooks") or {}
    fields = {field["key"]: field for field in quickbooks.get("configuration") or []}
    assert fields["sync_interval_seconds"]["type"] == "number"
    gmail = admitted_manifest("gmail") or {}
    gmail_keys = {field["key"] for field in gmail.get("configuration") or []}
    assert "sync_interval_seconds" not in gmail_keys


@pytest.mark.asyncio
async def test_catalog_read_uses_the_package_request(monkeypatch):
    """A catalog read uses the shared package request and the guarded client."""
    from unittest.mock import AsyncMock

    from app.agentive.api import connectors as api
    from app.api.errors import BadRequestError
    from app.connectors.package_discovery import rescan

    rescan()
    seen: dict = {}

    async def fake_perform(
        manifest, request, config, secrets, inputs=None, client=None, with_headers=False
    ):
        seen["path"] = request["path"]
        seen["authorization"] = (request.get("headers") or {}).get("Authorization")
        seen["client"] = client
        seen["base"] = manifest["http"]["base_url"]
        return {"labels": [{"id": "INBOX"}]}

    monkeypatch.setattr("app.connectors.package_runtime.perform_request", fake_perform)
    monkeypatch.setattr(api, "resolve_principal_id", lambda request: "user-1")
    monkeypatch.setattr(api, "connector_owner_is", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "app.agentive.services.connector_registry_node.decrypt_auth_state",
        lambda state: state or {},
    )
    connector = type("Row", (), {})()
    connector.auth_state = {"access_token": "tok", "catalog_slug": "gmail"}
    connector.subclass_slug = "gmail"
    connector.package_manifest = None
    connector.package_config = {}
    monkeypatch.setattr(api, "get_connector", AsyncMock(return_value=connector))
    result = await api.get_connector_read_action(None, "c1", "list_labels")
    assert result["body"]["labels"][0]["id"] == "INBOX"
    assert seen["path"] == "/gmail/v1/users/me/labels"
    assert seen["authorization"] == "Bearer tok"
    assert seen["client"] is None
    assert seen["base"] == "https://gmail.googleapis.com"
    assert connector.subclass_slug == "gmail"
    assert connector.auth_state["access_token"] == "tok"
    connector.package_manifest = {
        "http": {"base_url": "https://mail.example"},
        "capabilities": {
            "actions": [
                {
                    "name": "list_labels",
                    "effect": "read",
                    "request": {"method": "GET", "path": "/boxes"},
                }
            ]
        },
    }
    connector.auth_state = {"access_token": "tok", "catalog_slug": "acme_mail"}
    connector.subclass_slug = "acme_mail"
    await api.get_connector_read_action(None, "c1", "list_labels")
    assert seen["path"] == "/boxes"
    assert seen["base"] == "https://mail.example"
    connector.package_manifest = {
        "capabilities": {
            "actions": [
                {
                    "name": "other",
                    "effect": "read",
                    "request": {"method": "GET", "path": "/old"},
                }
            ]
        }
    }
    connector.auth_state = {"access_token": "tok", "catalog_slug": "gmail"}
    connector.subclass_slug = "gmail"
    await api.get_connector_read_action(None, "c1", "list_labels")
    assert seen["path"] == "/gmail/v1/users/me/labels"
    connector.package_manifest = {
        "http": {"base_url": "https://mail.example"},
        "capabilities": {
            "actions": [
                {
                    "name": "list_labels",
                    "effect": "read",
                    "request": {"method": "POST", "path": "/boxes"},
                }
            ]
        },
    }
    with pytest.raises(BadRequestError, match="GET"):
        await api.get_connector_read_action(None, "c1", "list_labels")
    connector.package_manifest = {"capabilities": {"actions": []}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(BadRequestError, match="does not declare"):
        await api.get_connector_read_action(None, "c1", "list_labels")


@pytest.mark.asyncio
async def test_gmail_label_route_uses_the_package_request(monkeypatch):
    """The dedicated label route uses the shared package request."""
    from unittest.mock import AsyncMock

    from app.agentive.api import connectors as api
    from app.api.errors import BadRequestError
    from app.connectors.package_discovery import rescan

    rescan()
    seen: dict = {}

    async def fake_perform(
        manifest, request, config, secrets, inputs=None, client=None, with_headers=False
    ):
        seen["path"] = request["path"]
        seen["authorization"] = (request.get("headers") or {}).get("Authorization")
        seen["client"] = client
        return {"labels": [{"id": "INBOX", "name": "Inbox", "type": "system"}]}

    monkeypatch.setattr("app.connectors.package_runtime.perform_request", fake_perform)
    monkeypatch.setattr(api, "resolve_principal_id", lambda request: "user-1")
    monkeypatch.setattr(api, "connector_owner_is", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "app.agentive.services.connector_registry_node.decrypt_auth_state",
        lambda state: state or {},
    )
    connector = type("Row", (), {})()
    connector.auth_state = {"access_token": "tok", "catalog_slug": "gmail"}
    connector.subclass_slug = "gmail"
    connector.package_manifest = None
    connector.package_config = {}
    monkeypatch.setattr(api, "get_connector", AsyncMock(return_value=connector))
    result = await api.get_gmail_labels(None, "c1")
    assert result.labels[0].id == "INBOX"
    assert seen["path"] == "/gmail/v1/users/me/labels"
    assert seen["authorization"] == "Bearer tok"
    assert seen["client"] is None
    assert connector.subclass_slug == "gmail"
    connector.package_manifest = {
        "http": {"base_url": "https://mail.example"},
        "capabilities": {
            "actions": [
                {
                    "name": "list_labels",
                    "effect": "read",
                    "request": {"method": "GET", "path": "/boxes"},
                }
            ]
        },
    }
    connector.auth_state = {"access_token": "tok", "catalog_slug": "acme_mail"}
    connector.subclass_slug = "acme_mail"
    await api.get_gmail_labels(None, "c1")
    assert seen["path"] == "/boxes"
    connector.package_manifest = {
        "capabilities": {
            "actions": [
                {
                    "name": "other",
                    "effect": "read",
                    "request": {"method": "GET", "path": "/old"},
                }
            ]
        }
    }
    connector.auth_state = {"access_token": "tok", "catalog_slug": "gmail"}
    connector.subclass_slug = "gmail"
    await api.get_gmail_labels(None, "c1")
    assert seen["path"] == "/gmail/v1/users/me/labels"
    connector.package_manifest = {"capabilities": {"actions": []}}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(BadRequestError, match="does not declare"):
        await api.get_gmail_labels(None, "c1")


@pytest.mark.asyncio
async def test_label_selection_uses_the_declared_action(monkeypatch):
    """Label selection accepts the package that declares list_labels."""
    from unittest.mock import AsyncMock

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "ab" * 32)

    from app.agentive.api import connectors as api
    from app.api.errors import BadRequestError
    from app.connectors.package_discovery import rescan

    rescan()
    monkeypatch.setattr(api, "resolve_principal_id", lambda request: "user-1")
    monkeypatch.setattr(api, "connector_owner_is", AsyncMock(return_value=True))
    monkeypatch.setattr(api, "emit_change_event", AsyncMock())
    connector = type("Row", (), {})()
    connector.id = "c1"
    connector.owner = "user-1"
    connector.auth_state = {"access_token": "tok", "catalog_slug": "gmail"}
    connector.subclass_slug = "gmail"
    connector.package_manifest = None
    connector.save = AsyncMock()
    monkeypatch.setattr(api, "get_connector", AsyncMock(return_value=connector))
    saved = await api.post_gmail_set_labels(None, "c1", ["INBOX"], True)
    assert saved.label_ids == ["INBOX"]
    assert connector.auth_state["access_token"].startswith("v1:")
    assert api.decrypt_auth_state(connector.auth_state)["access_token"] == "tok"
    assert connector.auth_state["labels"] == ["INBOX"]
    assert connector.subclass_slug == "gmail"
    connector.package_manifest = {
        "capabilities": {
            "actions": [
                {
                    "name": "list_labels",
                    "effect": "read",
                    "request": {"method": "GET", "path": "/boxes"},
                }
            ]
        }
    }
    connector.auth_state = {"access_token": "tok", "catalog_slug": "acme_mail"}
    connector.subclass_slug = "acme_mail"
    await api.post_gmail_set_labels(None, "c1", ["WORK"], True)
    assert connector.auth_state["labels"] == ["WORK"]
    assert connector.subclass_slug == "acme_mail"
    connector.package_manifest = {
        "capabilities": {
            "actions": [
                {
                    "name": "other",
                    "effect": "read",
                    "request": {"method": "GET", "path": "/old"},
                }
            ]
        }
    }
    connector.auth_state = {
        "access_token": "tok",
        "catalog_slug": "gmail",
        "labels": [],
    }
    connector.subclass_slug = "gmail"
    await api.post_gmail_set_labels(None, "c1", ["SENT"], False)
    assert connector.auth_state["labels"] == ["SENT"]
    connector.package_manifest = {"capabilities": {"actions": []}}
    connector.auth_state = {"access_token": "tok", "labels": ["SENT"]}
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: {},
    )
    with pytest.raises(BadRequestError, match="does not declare"):
        await api.post_gmail_set_labels(None, "c1", ["OTHER"], True)
    assert connector.auth_state["labels"] == ["SENT"]


def test_gmail_labels_load_from_the_declared_read_action():
    """Gmail label choices name the package list_labels action."""
    from app.connectors.package_discovery import rescan
    from app.connectors.package_runtime import admitted_manifest

    rescan()
    gmail = admitted_manifest("gmail") or {}
    fields = {field["key"]: field for field in gmail.get("configuration") or []}
    source = fields["labels"]["options_from"]
    assert fields["labels"]["multiple"] is True
    assert source["action"] == "list_labels"
    assert source["items"] == "$.labels"


def test_installed_manifest_is_kept_when_the_package_changes(monkeypatch):
    """Add freezes the manifest. A later package edit does not change that copy."""
    from types import SimpleNamespace

    from app.agentive.services.connector_packages import _installed_manifest
    from app.connectors.package_discovery import PackageAdmission

    stored = {"capabilities": {"actions": [{"name": "find"}]}}
    connector = SimpleNamespace(subclass_slug="acme", package_manifest=stored)
    monkeypatch.setattr(
        "app.agentive.services.connector_packages.current_admission",
        lambda: PackageAdmission(packages={"acme": {"capabilities": {"actions": []}}}),
    )
    assert (
        _installed_manifest(connector)["capabilities"]["actions"][0]["name"] == "find"
    )


def test_unavailable_package_installation_is_disable_only():
    """An installation stays visible after its package is no longer admitted."""
    from types import SimpleNamespace

    from app.agentive.services.connector_packages import projection

    connector = SimpleNamespace(
        id="c",
        subclass_slug="gone",
        label="Gone",
        package_version="1",
        package_revision=1,
        enabled=True,
        health_status="healthy",
        package_config={},
        package_fingerprint="",
        package_previous={},
        package_manifest={},
        pending_review={},
    )
    shown = projection(connector)
    assert shown["package_available"] is False
    assert shown["id"] == "c"


@pytest.mark.asyncio
async def test_linked_webhook_records_the_event_only_after_the_turn(monkeypatch):
    """A failed turn leaves the event unrecorded so the sender can retry."""
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.services import connector_packages
    from app.connectors.package_discovery import PackageAdmission

    saved = {"n": 0}

    async def save():
        saved["n"] += 1

    connector = SimpleNamespace(
        id="c",
        mapping_profile="package",
        enabled=True,
        owner="owner",
        workspace_id="ws",
        subclass_slug="desk_hook",
        auth_state={"hook_secret": "sek"},
        sync_cursor="",
        package_manifest={
            "capabilities": {
                "channel": {
                    "mode": "webhook",
                    "ingress": {
                        "verify": {
                            "scheme": "hmac_sha256",
                            "header": "X-Signature",
                            "secret": "hook_secret",
                        },
                        "event_id": "$.id",
                        "sender": "$.from",
                        "text": "$.text",
                    },
                }
            }
        },
        save=save,
    )
    monkeypatch.setattr(
        connector_packages.Connector, "get", AsyncMock(return_value=connector)
    )
    monkeypatch.setattr(
        connector_packages,
        "current_admission",
        lambda: PackageAdmission(packages={"desk_hook": connector.package_manifest}),
    )
    monkeypatch.setattr(
        connector_packages, "webhook_signature_ok", lambda *_a, **_k: True
    )
    monkeypatch.setattr(
        connector_packages, "_sender_is_linked", AsyncMock(return_value=True)
    )

    async def fail_turn(*_args, **_kwargs):
        raise RuntimeError("turn failed")

    monkeypatch.setattr(connector_packages, "_start_linked_turn", fail_turn)
    body = json.dumps({"id": "e1", "from": "ada", "text": "hello"}).encode()
    with pytest.raises(RuntimeError):
        await connector_packages.receive_package_webhook(
            "c", body, {"X-Signature": "ok"}
        )
    assert saved["n"] == 0

    async def start_turn(*_args, **_kwargs):
        return None

    monkeypatch.setattr(connector_packages, "_start_linked_turn", start_turn)
    result = await connector_packages.receive_package_webhook(
        "c", body, {"X-Signature": "ok"}
    )
    assert result["turn_started"] is True
    assert saved["n"] == 1
    assert connector.sync_cursor == "e1"


@pytest.mark.asyncio
async def test_linked_turn_submits_the_message_for_the_owner(monkeypatch):
    """The queued turn carries the message text for the linked owner and workspace."""
    from types import SimpleNamespace

    from app.agentive.services.connector_packages import _start_linked_turn

    connector = SimpleNamespace(
        id="c", owner="owner", workspace_id="ws", subclass_slug="desk_hook"
    )
    thread = SimpleNamespace(
        id="t1", user_id="owner", workspace_id="ws", archived=False
    )
    submitted: dict = {}

    async def linked(*_args, **_kwargs):
        return True

    async def get_session(_session_id):
        return thread

    async def submit(request):
        submitted["text"] = request.parts[0]["text"]
        submitted["principal"] = request.principal_id
        submitted["workspace"] = request.workspace_id
        submitted["thread"] = request.thread_id

    monkeypatch.setattr(
        "app.agentive.services.connector_packages._sender_is_linked", linked
    )
    monkeypatch.setattr("app.services.chat_threads.get_thread_by_session", get_session)
    monkeypatch.setattr("app.services.chat_turn_submissions.submit_chat_turn", submit)
    await _start_linked_turn(connector, "ada", "hello", "e1")
    assert submitted == {
        "text": "hello",
        "principal": "owner",
        "workspace": "ws",
        "thread": "t1",
    }


@pytest.mark.asyncio
async def test_webhook_link_rechecks_the_connector_after_the_lookup(monkeypatch):
    """A connector disabled while the identity is read is not linked."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.nodes import ChannelIdentity
    from app.agentive.services.connector_packages import Connector, _sender_is_linked
    from app.connectors.package_discovery import PackageAdmission

    connector = SimpleNamespace(
        id="c", subclass_slug="desk_hook", owner="owner", enabled=True
    )
    identity = SimpleNamespace(
        verified=True, channel="desk_hook", user_id="owner", channel_user_id="ada"
    )
    monkeypatch.setattr(ChannelIdentity, "find", AsyncMock(return_value=[identity]))
    monkeypatch.setattr(
        Connector,
        "get",
        AsyncMock(
            return_value=SimpleNamespace(
                id="c", subclass_slug="desk_hook", owner="owner", enabled=False
            )
        ),
    )
    monkeypatch.setattr(
        "app.agentive.services.connector_packages.current_admission",
        lambda: PackageAdmission(packages={"desk_hook": {}}),
    )
    assert await _sender_is_linked(connector, "ada") is False


@pytest.mark.asyncio
async def test_outbound_message_stops_when_the_connector_changes(monkeypatch):
    """A channel send does not run after the installation revision changes."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.services import connector_packages
    from app.api.errors import ResourceConflictError
    from app.connectors.package_discovery import PackageAdmission

    connector = SimpleNamespace(
        id="c",
        subclass_slug="desk_hook",
        owner="owner",
        enabled=True,
        package_revision=1,
        mapping_profile="package",
    )
    monkeypatch.setattr(connector_packages, "_owned", AsyncMock(return_value=connector))
    monkeypatch.setattr(
        connector_packages.Connector,
        "get",
        AsyncMock(
            return_value=SimpleNamespace(
                id="c",
                subclass_slug="desk_hook",
                enabled=True,
                package_revision=2,
                auth_state={},
                package_config={},
            )
        ),
    )
    monkeypatch.setattr(
        connector_packages,
        "current_admission",
        lambda: PackageAdmission(packages={"desk_hook": {}}),
    )
    called = {"request": False}

    async def fake_request(*args, **kwargs):
        called["request"] = True
        return {"ok": True}

    monkeypatch.setattr(connector_packages, "perform_request", fake_request)
    with pytest.raises(ResourceConflictError):
        await connector_packages.send_package_message("c", "owner", {"text": "hi"})
    assert called["request"] is False


@pytest.mark.asyncio
async def test_sync_stops_when_the_connector_changes_during_the_pull(monkeypatch):
    """A sync does not store a cursor if the installation changed while pulling."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.services import connector_packages
    from app.api.errors import ResourceConflictError
    from app.connectors.package_discovery import PackageAdmission

    connector = SimpleNamespace(
        id="c",
        subclass_slug="acme",
        owner="owner",
        enabled=True,
        package_revision=1,
        mapping_profile="package",
        auth_state={},
        package_config={},
        sync_cursor="",
    )
    changed = SimpleNamespace(
        id="c",
        subclass_slug="acme",
        owner="owner",
        enabled=True,
        package_revision=2,
        auth_state={},
        package_config={},
        sync_cursor="",
    )
    monkeypatch.setattr(connector_packages, "_owned", AsyncMock(return_value=connector))
    monkeypatch.setattr(
        connector_packages.Connector,
        "get",
        AsyncMock(side_effect=[connector, changed]),
    )
    monkeypatch.setattr(
        connector_packages,
        "current_admission",
        lambda: PackageAdmission(packages={"acme": {}}),
    )
    monkeypatch.setattr(
        connector_packages,
        "_manifest",
        lambda package_id: {
            "trust": {"tier": "declarative"},
            "http": {"base_url": "https://acme.example"},
            "capabilities": {
                "sync": {
                    "source": {"request": {"method": "GET", "path": "/rows"}},
                    "map": {"external_id": "$.id"},
                }
            },
        },
    )

    async def fake_pull(manifest, config, secrets, start_cursor=""):
        return ([{"external_id": "1"}], "2")

    monkeypatch.setattr(connector_packages, "pull_declared_sync", fake_pull)

    async def claim(row, **kwargs):
        return object()

    async def release(lease):
        return None

    monkeypatch.setattr("app.services.connectors.sync_lease.claim_sync", claim)
    monkeypatch.setattr("app.services.connectors.sync_lease.release_sync", release)
    with pytest.raises(ResourceConflictError):
        await connector_packages.sync_package("c", "owner")


@pytest.mark.asyncio
async def test_sync_refuses_when_a_lease_is_held(monkeypatch):
    """A second sync does not pull while the first still holds the lease."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.services import connector_packages
    from app.api.errors import ResourceConflictError
    from app.connectors.package_discovery import PackageAdmission

    connector = SimpleNamespace(
        id="c",
        subclass_slug="acme",
        owner="owner",
        enabled=True,
        package_revision=1,
        mapping_profile="package",
        workspace_id="ws",
    )
    monkeypatch.setattr(connector_packages, "_owned", AsyncMock(return_value=connector))
    monkeypatch.setattr(
        connector_packages.Connector, "get", AsyncMock(return_value=connector)
    )
    monkeypatch.setattr(
        connector_packages,
        "current_admission",
        lambda: PackageAdmission(packages={"acme": {}}),
    )
    called = {"pull": False}

    async def fake_pull(*_args, **_kwargs):
        called["pull"] = True
        return ([], "")

    async def claim(row, **kwargs):
        return None

    monkeypatch.setattr(connector_packages, "pull_declared_sync", fake_pull)
    monkeypatch.setattr("app.services.connectors.sync_lease.claim_sync", claim)
    with pytest.raises(ResourceConflictError, match="already running"):
        await connector_packages.sync_package("c", "owner")
    assert called["pull"] is False


@pytest.mark.asyncio
async def test_mcp_tool_stops_when_the_connector_revision_changes(monkeypatch):
    """A read-only MCP tool does not run after the installation revision changes."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.services import connector_packages
    from app.api.errors import ResourceConflictError
    from app.connectors.package_discovery import PackageAdmission

    connector = SimpleNamespace(
        id="c",
        subclass_slug="desk",
        owner="owner",
        enabled=True,
        package_revision=1,
        mapping_profile="package",
        auth_state={},
        package_config={},
    )
    monkeypatch.setattr(connector_packages, "_owned", AsyncMock(return_value=connector))
    monkeypatch.setattr(
        connector_packages.Connector,
        "get",
        AsyncMock(
            return_value=SimpleNamespace(
                id="c",
                subclass_slug="desk",
                owner="owner",
                enabled=True,
                package_revision=2,
                auth_state={},
                package_config={},
            )
        ),
    )
    monkeypatch.setattr(
        connector_packages,
        "current_admission",
        lambda: PackageAdmission(packages={"desk": {}}),
    )
    monkeypatch.setattr(
        connector_packages,
        "_manifest",
        lambda package_id: {
            "capabilities": {
                "mcp": {
                    "transport": "streamable_http",
                    "url": "https://mcp.example/mcp",
                    "read_only_tools": ["search"],
                }
            }
        },
    )
    with pytest.raises(ResourceConflictError):
        await connector_packages.invoke_mcp_tool("c", "owner", "search", {})


@pytest.mark.asyncio
async def test_action_stops_when_the_connector_revision_changes(monkeypatch):
    """A read does not run after the installation revision changes."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.services.connector_packages import Connector, _execute
    from app.api.errors import ResourceConflictError
    from app.connectors.package_discovery import PackageAdmission

    connector = SimpleNamespace(
        id="c", subclass_slug="acme", package_revision=1, enabled=True
    )
    monkeypatch.setattr(
        Connector,
        "get",
        AsyncMock(
            return_value=SimpleNamespace(
                id="c",
                subclass_slug="acme",
                package_revision=2,
                enabled=True,
                auth_state={},
                package_config={},
            )
        ),
    )
    monkeypatch.setattr(
        "app.agentive.services.connector_packages.current_admission",
        lambda: PackageAdmission(packages={"acme": {"capabilities": {"actions": []}}}),
    )
    with pytest.raises(ResourceConflictError):
        await _execute(
            connector,
            {},
            {
                "name": "find",
                "effect": "read",
                "request": {"method": "GET", "path": "/x"},
            },
            {},
        )


def test_webhook_sender_links_only_through_a_verified_identity():
    """An unverified or different owner does not count as a linked sender."""
    from types import SimpleNamespace

    from app.agentive.services.connector_packages import _identity_matches

    good = SimpleNamespace(
        verified=True, channel="desk_hook", user_id="owner", channel_user_id="ada"
    )
    assert _identity_matches(good, "desk_hook", "owner", "ada")
    assert not _identity_matches(good, "desk_hook", "other", "ada")
    unverified = SimpleNamespace(
        verified=False, channel="desk_hook", user_id="owner", channel_user_id="ada"
    )
    assert not _identity_matches(unverified, "desk_hook", "owner", "ada")


def test_webhook_signature_matches_the_raw_body():
    """The digest is over the raw bytes. A wrong secret does not pass."""
    import hashlib
    import hmac

    from app.connectors.package_runtime import webhook_signature_ok

    body = b'{"id":"1"}'
    secret = "hook-secret"
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert webhook_signature_ok(body, f"sha256={digest}", secret)
    assert not webhook_signature_ok(body, digest, "other-secret")


def test_templates_do_not_accept_secrets():
    """Config values can be templated. Secret values cannot."""
    with pytest.raises(ValueError, match="secrets"):
        render("{{secret.api_key}}", {})
    assert render("{{config.base_url}}/x", {"base_url": "https://acme.example"}) == (
        "https://acme.example/x"
    )
    assert select_output({"items": [{"id": "1"}]}, "$.items[*]") == [{"id": "1"}]


def _install_manifest(cardinality: str | None) -> dict:
    binding = {"scopes": ["workspace"]}
    if cardinality is not None:
        binding["cardinality"] = {"workspace": cardinality}
    return {
        "name": "Acme",
        "version": "1",
        "binding": binding,
        "capabilities": {"actions": []},
        "configuration": [],
    }


def test_workspace_cardinality_comes_from_the_package(monkeypatch):
    """A stored cardinality wins. An older package uses the admitted value."""
    from app.agentive.services.connector_packages import _workspace_cardinality

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {"binding": {"cardinality": {"workspace": "many"}}}
            if package_id == "acme"
            else None
        ),
    )
    assert (
        _workspace_cardinality(
            {"id": "acme_books", "binding": {"cardinality": {"workspace": "one"}}},
            "acme",
        )
        == "one"
    )
    assert _workspace_cardinality({"id": "acme_books", "binding": {}}, "acme") == "many"
    assert _workspace_cardinality({"id": "acme_books"}, "missing") == "one"
    with pytest.raises(ValueError, match="does not declare a workspace cardinality"):
        _workspace_cardinality(
            {"id": "acme_books", "binding": {"cardinality": {"workspace": "lots"}}},
            "acme_books",
        )


@pytest.mark.asyncio
async def test_workspace_cardinality_one_admits_one_concurrent_install(monkeypatch):
    """Two adds at once still leave a single installation when cardinality is one."""
    import asyncio
    from types import SimpleNamespace

    from app.agentive.services import connector_packages as svc
    from app.api.errors import ResourceConflictError

    created: list[SimpleNamespace] = []

    async def fake_find(_query):
        return list(created)

    async def fake_create(**_kwargs):
        await asyncio.sleep(0)
        row = SimpleNamespace(id=f"c{len(created) + 1}")

        async def save():
            return None

        row.save = save
        created.append(row)
        return row

    monkeypatch.setattr(svc, "_manifest", lambda _package_id: _install_manifest(None))
    monkeypatch.setattr(svc.Connector, "find", fake_find)
    monkeypatch.setattr(svc, "create_connector", fake_create)
    monkeypatch.setattr(
        svc, "current_admission", lambda: SimpleNamespace(fingerprints={})
    )
    results = await asyncio.gather(
        svc.install_package(
            actor_id="u", workspace_id="ws", package_id="acme", values={}
        ),
        svc.install_package(
            actor_id="u", workspace_id="ws", package_id="acme", values={}
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(item, Exception) for item in results) == 1
    assert any(isinstance(item, ResourceConflictError) for item in results)


@pytest.mark.asyncio
async def test_workspace_cardinality_many_allows_a_second_install(monkeypatch):
    """A package that declares many can be added twice in one workspace."""
    from types import SimpleNamespace

    from app.agentive.services import connector_packages as svc

    created: list[SimpleNamespace] = []

    async def fake_find(_query):
        return list(created)

    async def fake_create(**_kwargs):
        row = SimpleNamespace(id=f"c{len(created) + 1}")

        async def save():
            return None

        row.save = save
        created.append(row)
        return row

    monkeypatch.setattr(svc, "_manifest", lambda _package_id: _install_manifest("many"))
    monkeypatch.setattr(svc.Connector, "find", fake_find)
    monkeypatch.setattr(svc, "create_connector", fake_create)
    monkeypatch.setattr(
        svc, "current_admission", lambda: SimpleNamespace(fingerprints={})
    )
    first = await svc.install_package(
        actor_id="u", workspace_id="ws", package_id="acme", values={}
    )
    second = await svc.install_package(
        actor_id="u", workspace_id="ws", package_id="acme", values={}
    )
    assert first.id != second.id


@pytest.mark.asyncio
async def test_package_call_receipt_omits_input_and_secrets(monkeypatch):
    """A successful read records the action and leaves the secret out of the receipt."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.services import connector_packages as svc
    from app.connectors.package_discovery import PackageAdmission

    connector = SimpleNamespace(
        id="c1",
        owner="owner",
        enabled=True,
        package_revision=1,
        subclass_slug="acme",
        workspace_id="ws",
        package_manifest={
            "trust": {"tier": "declarative"},
            "http": {"base_url": "https://acme.example"},
            "capabilities": {
                "actions": [
                    {
                        "name": "find",
                        "effect": "read",
                        "request": {"method": "GET", "path": "/x"},
                        "input": {
                            "type": "object",
                            "properties": {"q": {"type": "string"}},
                            "additionalProperties": False,
                        },
                    }
                ]
            },
        },
        package_config={"token": "super-secret"},
        auth_state={},
    )
    seen: dict = {}

    async def emit(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(svc.Connector, "get", AsyncMock(return_value=connector))
    monkeypatch.setattr(
        svc,
        "current_admission",
        lambda: PackageAdmission(packages={"acme": {}}),
    )
    monkeypatch.setattr(
        svc,
        "perform_request",
        AsyncMock(return_value={"token": "super-secret", "name": "Ada"}),
    )
    monkeypatch.setattr(
        "app.services.change_event.emit_change_event",
        emit,
    )
    result = await svc._execute(
        connector,
        connector.package_manifest,
        {"name": "find", "effect": "read", "request": {"method": "GET", "path": "/x"}},
        {"q": "super-secret"},
    )
    assert result["output"]["name"] == "Ada"
    assert seen["action"] == "tool.invoke"
    assert seen["details"] == {
        "package_id": "acme",
        "action": "find",
        "effect": "read",
        "status": "ok",
    }
    assert "super-secret" not in str(seen)


@pytest.mark.asyncio
async def test_read_action_must_use_get(monkeypatch):
    """A stored read that is not GET does not send the request."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.services import connector_packages as svc
    from app.api.errors import BadRequestError
    from app.connectors.package_discovery import PackageAdmission

    action = {
        "name": "find",
        "effect": "read",
        "request": {"method": "POST", "path": "/search"},
    }
    connector = SimpleNamespace(
        id="c1",
        owner="owner",
        enabled=True,
        package_revision=1,
        subclass_slug="acme",
        package_manifest={"capabilities": {"actions": [action]}},
        package_config={},
        auth_state={},
    )
    monkeypatch.setattr(svc.Connector, "get", AsyncMock(return_value=connector))
    monkeypatch.setattr(
        svc, "current_admission", lambda: PackageAdmission(packages={"acme": {}})
    )
    monkeypatch.setattr(
        svc, "perform_request", AsyncMock(side_effect=AssertionError("sent"))
    )
    with pytest.raises(BadRequestError, match="GET"):
        await svc._execute(connector, connector.package_manifest, action, {})


def test_action_output_list_is_capped():
    """A list result stops at max_items, or at 50, and never exceeds 200."""
    from app.agentive.services.connector_packages import _limit_items

    items = [{"id": str(index)} for index in range(210)]
    assert len(_limit_items(items, {})) == 50
    assert len(_limit_items(items, {"output": {"max_items": 2}})) == 2
    assert len(_limit_items(items, {"output": {"max_items": 500}})) == 200
    assert _limit_items({"id": "1"}, {"output": {"max_items": 1}}) == {"id": "1"}


@pytest.mark.asyncio
async def test_action_input_must_match_the_schema(monkeypatch):
    """A mismatched argument is refused and is not copied into the error."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.services import connector_packages as svc
    from app.api.errors import BadRequestError
    from app.connectors.package_discovery import PackageAdmission

    action = {
        "name": "find",
        "effect": "read",
        "request": {"method": "GET", "path": "/x"},
        "input": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    }
    connector = SimpleNamespace(
        id="c1",
        enabled=True,
        package_revision=1,
        subclass_slug="acme",
        package_manifest={"capabilities": {"actions": [action]}},
        package_config={},
        auth_state={},
    )
    monkeypatch.setattr(svc.Connector, "get", AsyncMock(return_value=connector))
    monkeypatch.setattr(
        svc, "current_admission", lambda: PackageAdmission(packages={"acme": {}})
    )
    monkeypatch.setattr(
        svc, "perform_request", AsyncMock(side_effect=AssertionError("sent"))
    )
    with pytest.raises(BadRequestError, match="schema") as raised:
        await svc._execute(
            connector, connector.package_manifest, action, {"note": "secret-value"}
        )
    assert "secret-value" not in str(raised.value)


def test_request_path_cannot_climb_out_of_the_base():
    """A path that leaves the package base URL is refused."""
    from app.connectors.package_runtime import request_url

    manifest = {"http": {"base_url": "https://acme.example/v1"}}
    assert (
        request_url(manifest, {"path": "/contacts"}, {}, {})
        == "https://acme.example/v1/contacts"
    )
    for path in (
        "/../admin",
        "/./secret",
        "//evil.example/x",
        "/%2e%2e/admin",
        "https://evil.example",
    ):
        with pytest.raises(ValueError, match="relative"):
            request_url(manifest, {"path": path}, {}, {})


def test_enabled_package_action_is_a_workspace_tool(monkeypatch):
    """Enable publishes <package>.<action>. Disable removes that tool."""
    from types import SimpleNamespace

    from app.agentive.services.connector_packages import sync_package_action_tools
    from app.connectors.package_discovery import PackageAdmission
    from app.services.hooks.registry import (
        get_workspace_tools,
        unregister_bundle_registrations,
    )

    monkeypatch.setattr(
        "app.agentive.services.connector_packages.current_admission",
        lambda: PackageAdmission(packages={"acme": {}}),
    )
    connector = SimpleNamespace(
        id="c-tool",
        workspace_id="ws-tool",
        subclass_slug="acme",
        enabled=True,
        package_manifest={
            "capabilities": {
                "actions": [
                    {
                        "name": "find",
                        "description": "Find contacts.",
                        "effect": "read",
                        "input": {"type": "object"},
                    }
                ]
            }
        },
    )
    unregister_bundle_registrations("ws-tool", "connector-package:c-tool")
    sync_package_action_tools(connector)
    spec = get_workspace_tools("ws-tool")["acme.find"]
    assert spec["_package_installation_id"] == "c-tool"
    assert spec["_package_action"] == "find"
    connector.enabled = False
    sync_package_action_tools(connector)
    assert "acme.find" not in get_workspace_tools("ws-tool")


@pytest.mark.asyncio
async def test_rescan_removes_tools_for_a_rejected_package(monkeypatch):
    """A package that is no longer admitted loses its workspace tools."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.services import connector_packages as svc
    from app.connectors.package_discovery import PackageAdmission
    from app.services.hooks.registry import (
        get_workspace_tools,
        unregister_bundle_registrations,
    )

    packages = {"acme": {}}
    monkeypatch.setattr(
        svc,
        "current_admission",
        lambda: PackageAdmission(packages=dict(packages)),
    )
    connector = SimpleNamespace(
        id="c-rescan",
        workspace_id="ws-rescan",
        subclass_slug="acme",
        enabled=True,
        package_manifest={
            "capabilities": {
                "actions": [
                    {
                        "name": "find",
                        "description": "Find contacts.",
                        "effect": "read",
                        "input": {"type": "object"},
                    }
                ]
            }
        },
    )
    unregister_bundle_registrations("ws-rescan", "connector-package:c-rescan")
    svc.sync_package_action_tools(connector)
    assert "acme.find" in get_workspace_tools("ws-rescan")
    packages.clear()
    monkeypatch.setattr(svc.Connector, "find", AsyncMock(return_value=[connector]))
    await svc.refresh_package_action_tools()
    assert "acme.find" not in get_workspace_tools("ws-rescan")


@pytest.mark.asyncio
async def test_package_tool_call_uses_the_registered_installation(monkeypatch):
    """The caller cannot choose a different installation in the arguments."""
    from app.services.hooks.registry import ToolContext
    from app.services.hooks.tool_dispatch import _run_tool

    seen: dict = {}

    async def invoke(connector_id, actor_id, action_name, payload, workspace_id=None):
        seen["connector_id"] = connector_id
        seen["actor_id"] = actor_id
        seen["action"] = action_name
        seen["workspace_id"] = workspace_id
        seen["payload"] = payload
        return {"status": "ok", "output": {"n": 1}}

    monkeypatch.setattr(
        "app.agentive.services.connector_packages.invoke_action", invoke
    )
    result = await _run_tool(
        {
            "key": "acme.find",
            "input_schema": {"type": "object"},
            "_package_installation_id": "c-tool",
            "_package_action": "find",
        },
        {"q": "ada", "installation": "other"},
        ToolContext(user_id="owner", workspace_id="ws-tool", scope="tool:acme.find"),
    )
    assert result["output"]["n"] == 1
    assert seen["connector_id"] == "c-tool"
    assert seen["action"] == "find"
    assert seen["actor_id"] == "owner"
    assert seen["workspace_id"] == "ws-tool"
    assert seen["payload"]["installation"] == "other"


@pytest.mark.asyncio
async def test_package_write_tool_stages_only_the_package_review(monkeypatch):
    """A workspace call of a package write does not open a second proposal."""
    from app.agentive.tooling.bindings import (
        _propose_principal,
        _stage_call_workspace_tool,
    )
    from app.services.agent_scope import current_scope_workspace_id

    spec = {
        "key": "acme.create",
        "name": "acme.create",
        "side_effects": "write",
        "agent_callable": True,
        "_package_installation_id": "c1",
        "_package_action": "create",
        "parameters_schema": {"type": "object"},
    }

    async def invoke(**kwargs):
        assert kwargs["tool_key"] == "acme.create"
        assert kwargs["payload"] == {"name": "Ada"}
        return {"status": "review", "review_id": "r1"}

    monkeypatch.setattr(
        "app.services.workspace_tools.lookup_workspace_tool", lambda _ws, _key: spec
    )
    monkeypatch.setattr(
        "app.services.workspace_tools.is_agent_callable", lambda _s: True
    )
    monkeypatch.setattr("app.services.workspace_tools.is_write_tool", lambda _s: True)

    async def allow(**_kwargs):
        return None

    monkeypatch.setattr(
        "app.services.workspace_tools.require_workspace_tool_access", allow
    )
    monkeypatch.setattr("app.services.workspace_tools.invoke_workspace_tool", invoke)
    scope_tok = current_scope_workspace_id.set("ws")
    prin_tok = _propose_principal.set("owner")
    try:
        result = await _stage_call_workspace_tool(
            {"tool_key": "acme.create", "input": {"name": "Ada"}}
        )
    finally:
        _propose_principal.reset(prin_tok)
        current_scope_workspace_id.reset(scope_tok)
    assert result["_no_stage"] is True
    assert result["data"]["output"]["review_id"] == "r1"
    assert result.get("kind") is None


def test_sync_page_limit_defaults_and_caps():
    """Pagination defaults to 50 pages and never exceeds 1000."""
    from app.connectors.package_runtime import page_limit

    assert page_limit({}) == 1
    assert page_limit({"next_cursor": "$.next"}) == 50
    assert page_limit({"next_cursor": "$.next", "max_pages": 2}) == 2
    assert page_limit({"next_cursor": "$.next", "max_pages": 5000}) == 1000


@pytest.mark.asyncio
async def test_sync_without_max_pages_stops_at_fifty(monkeypatch):
    """A paginated source that omits max_pages stops after the default."""
    from app.connectors.package_runtime import pull_declared_sync

    calls = {"n": 0}

    async def fake(_manifest, _request, _config, _secrets, inputs=None, client=None):
        calls["n"] += 1
        return {"items": [{"id": "1", "title": "A"}], "next": "more"}

    monkeypatch.setattr("app.connectors.package_runtime.perform_request", fake)
    manifest = {
        "http": {"base_url": "https://acme.example"},
        "capabilities": {
            "sync": {
                "source": {
                    "request": {"method": "GET", "path": "/items"},
                    "items": "$.items",
                    "paginate": {"next_cursor": "$.next"},
                },
                "map": {"external_id": "$.id", "title": "$.title"},
            }
        },
    }
    rows, cursor = await pull_declared_sync(manifest, {}, {})
    assert calls["n"] == 50
    assert len(rows) == 50
    assert cursor == "more"


@pytest.mark.asyncio
async def test_webhook_receipt_omits_the_message(monkeypatch):
    """An accepted webhook records the delivery and leaves the message out."""
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.services import connector_packages as svc
    from app.connectors.package_discovery import PackageAdmission

    connector = SimpleNamespace(
        id="c",
        mapping_profile="package",
        enabled=True,
        owner="owner",
        workspace_id="ws",
        subclass_slug="desk_hook",
        auth_state={"hook_secret": "sek"},
        sync_cursor="",
        package_manifest={
            "capabilities": {
                "channel": {
                    "mode": "webhook",
                    "ingress": {
                        "verify": {
                            "scheme": "hmac_sha256",
                            "header": "X-Signature",
                            "secret": "hook_secret",
                        },
                        "event_id": "$.id",
                        "sender": "$.from",
                        "text": "$.text",
                    },
                }
            }
        },
        save=AsyncMock(),
    )
    seen: dict = {}

    async def emit(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(svc.Connector, "get", AsyncMock(return_value=connector))
    monkeypatch.setattr(
        svc, "current_admission", lambda: PackageAdmission(packages={"desk_hook": {}})
    )
    monkeypatch.setattr(svc, "webhook_signature_ok", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(svc, "_sender_is_linked", AsyncMock(return_value=False))
    monkeypatch.setattr("app.services.change_event.emit_change_event", emit)
    body = json.dumps({"id": "e1", "from": "ada", "text": "hello secret"}).encode()
    result = await svc.receive_package_webhook("c", body, {"X-Signature": "ok"})
    assert result["accepted"] is True
    assert seen["details"]["action"] == "webhook"
    assert "hello secret" not in str(seen)
    assert "sek" not in str(seen)


@pytest.mark.asyncio
async def test_sync_receipt_omits_the_pulled_records(monkeypatch):
    """A completed package sync records the pull and leaves the records out."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.services import connector_packages as svc
    from app.connectors.package_discovery import PackageAdmission

    connector = SimpleNamespace(
        id="c",
        subclass_slug="acme",
        owner="owner",
        enabled=True,
        package_revision=1,
        mapping_profile="package",
        workspace_id="ws",
        auth_state={},
        package_config={},
        sync_cursor="",
        package_manifest={
            "trust": {"tier": "declarative"},
            "http": {"base_url": "https://acme.example"},
            "capabilities": {
                "sync": {
                    "source": {"request": {"method": "GET", "path": "/rows"}},
                    "map": {"external_id": "$.id"},
                }
            },
        },
        save=AsyncMock(),
    )
    seen: dict = {}

    async def emit(**kwargs):
        seen.update(kwargs)

    async def pull(*_args, **_kwargs):
        return ([{"title": "secret row"}], "cursor-2")

    monkeypatch.setattr(svc, "_owned", AsyncMock(return_value=connector))
    monkeypatch.setattr(svc.Connector, "get", AsyncMock(return_value=connector))
    monkeypatch.setattr(
        svc, "current_admission", lambda: PackageAdmission(packages={"acme": {}})
    )
    monkeypatch.setattr(svc, "pull_declared_sync", pull)
    monkeypatch.setattr(
        "app.services.connectors.sync_lease.claim_sync",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr("app.services.connectors.sync_lease.release_sync", AsyncMock())
    monkeypatch.setattr("app.services.change_event.emit_change_event", emit)
    result = await svc.sync_package("c", "owner")
    assert result["records"][0]["title"] == "secret row"
    assert seen["details"] == {
        "package_id": "acme",
        "action": "sync",
        "effect": "read",
        "status": "ok",
    }
    assert "secret row" not in str(seen)


@pytest.mark.asyncio
async def test_remove_clears_secret_handles_before_delete(monkeypatch):
    """Remove wipes secret handles before delete. A failed delete leaves the row."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.services import connector_packages as svc

    row = SimpleNamespace(
        enabled=True,
        auth_state={"api_key": "v1:secret"},
        package_previous={"secrets": {"old": "v1:old"}},
        pending_review={"input": {"token": "staged-secret"}},
    )
    saves: list[dict] = []

    async def save():
        saves.append(
            {
                "enabled": row.enabled,
                "auth_state": dict(row.auth_state),
                "previous": dict(row.package_previous or {}),
                "pending": dict(row.pending_review or {}),
            }
        )

    async def delete():
        raise RuntimeError("delete failed")

    row.save = save
    row.delete = delete
    monkeypatch.setattr(svc, "_owned", AsyncMock(return_value=row))
    with pytest.raises(RuntimeError, match="delete failed"):
        await svc.remove_package("c", "owner")
    assert saves[0]["enabled"] is False
    assert saves[0]["auth_state"]["api_key"] == "v1:secret"
    assert saves[1]["auth_state"] == {}
    assert saves[1]["previous"] == {}
    assert saves[1]["pending"] == {}
    assert row.auth_state == {}


def test_schema_comes_from_the_package(monkeypatch):
    """A stored schema wins. An older package uses the admitted schema."""
    from app.connectors.package_runtime import package_schema

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {"schema": "integral.connector/v1"} if package_id == "acme" else None
        ),
    )
    assert (
        package_schema({"id": "acme_books", "schema": "integral.connector/v1"}, "acme")
        == "integral.connector/v1"
    )
    assert (
        package_schema({"id": "acme_books", "name": "Acme"}, "acme")
        == "integral.connector/v1"
    )
    with pytest.raises(ValueError, match="does not declare a schema"):
        package_schema({"id": "acme_books"}, "missing")
    with pytest.raises(ValueError, match="does not declare a schema"):
        package_schema({"id": "acme_books", "schema": "other/v1"}, "acme_books")


def test_capabilities_come_from_the_package(monkeypatch):
    """A stored capability block wins. An older package uses the admitted kinds."""
    from app.connectors.package_runtime import package_capabilities

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {"capabilities": {"sync": {}}} if package_id == "acme" else None
        ),
    )
    assert package_capabilities(
        {"id": "acme_books", "capabilities": {"channel": {}}}, "acme"
    ) == ["channel"]
    assert package_capabilities({"id": "acme_books", "name": "Acme"}, "acme") == [
        "sync"
    ]
    with pytest.raises(ValueError, match="does not declare a capability"):
        package_capabilities({"id": "acme_books"}, "missing")
    with pytest.raises(ValueError, match="does not declare a capability"):
        package_capabilities(
            {"id": "acme_books", "capabilities": {"billing": {}}}, "acme_books"
        )


def test_core_range_comes_from_the_package(monkeypatch):
    """A stored Core range wins. An older package uses the admitted range."""
    from app.connectors.package_runtime import package_core

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: ({"core": ">=0.1"} if package_id == "acme" else None),
    )
    assert package_core({"id": "acme_books", "core": "==0.1.0"}, "acme") == "==0.1.0"
    assert package_core({"id": "acme_books", "name": "Acme"}, "acme") == ">=0.1"
    assert package_core({"id": "acme_books"}, "missing") is None
    with pytest.raises(ValueError, match="does not declare a core range"):
        package_core({"id": "acme_books", "core": ">=9"}, "acme_books")


def test_icon_comes_from_the_package(monkeypatch):
    """A stored icon path wins. An older package uses the admitted icon."""
    from app.connectors.package_runtime import package_icon

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {"icon": "marks/admitted.svg"} if package_id == "acme" else None
        ),
    )
    assert (
        package_icon({"id": "acme_books", "icon": "marks/stored.png"}, "acme")
        == "marks/stored.png"
    )
    assert package_icon({"id": "acme_books", "name": "Acme"}, "acme") == (
        "marks/admitted.svg"
    )
    assert package_icon({"id": "acme_books"}, "missing") is None
    with pytest.raises(ValueError, match="does not declare an icon"):
        package_icon({"id": "acme_books", "icon": "../secret.svg"}, "acme_books")


def test_publisher_comes_from_the_package(monkeypatch):
    """A stored publisher wins. An older package uses the admitted handle."""
    from app.connectors.package_runtime import package_publisher

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {"publisher": "admitted"} if package_id == "acme" else None
        ),
    )
    assert (
        package_publisher({"id": "acme_books", "publisher": "stored"}, "acme")
        == "stored"
    )
    assert package_publisher({"id": "acme_books", "name": "Acme"}, "acme") == "admitted"
    with pytest.raises(ValueError, match="does not declare a publisher"):
        package_publisher({"id": "acme_books"}, "missing")
    with pytest.raises(ValueError, match="does not declare a publisher"):
        package_publisher({"id": "acme_books", "publisher": "Local"}, "acme_books")


def test_version_comes_from_the_package(monkeypatch):
    """A stored version wins. An older package uses the admitted version."""
    from app.connectors.package_runtime import package_version

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: ({"version": "1.2.3"} if package_id == "acme" else None),
    )
    assert package_version({"id": "acme_books", "version": "2.0.0"}, "acme") == "2.0.0"
    assert package_version({"id": "acme_books", "name": "Acme"}, "acme") == "1.2.3"
    with pytest.raises(ValueError, match="does not declare a version"):
        package_version({"id": "acme_books"}, "missing")
    with pytest.raises(ValueError, match="does not declare a version"):
        package_version({"id": "acme_books", "version": "1"}, "acme_books")


def test_name_comes_from_the_package(monkeypatch):
    """A stored library name wins. An older package uses the admitted name."""
    from app.connectors.package_runtime import package_name

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: ({"name": "Admitted"} if package_id == "acme" else None),
    )
    assert package_name({"id": "acme_books", "name": "Stored"}, "acme") == "Stored"
    assert package_name({"id": "acme_books", "description": "Books"}, "acme") == (
        "Admitted"
    )
    with pytest.raises(ValueError, match="does not declare a name"):
        package_name({"id": "acme_books"}, "missing")
    with pytest.raises(ValueError, match="does not declare a name"):
        package_name({"id": "acme_books", "name": "x" * 81}, "acme_books")


def test_description_comes_from_the_package(monkeypatch):
    """A stored description wins. An older package uses the admitted text."""
    from app.connectors.package_runtime import package_description

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {"description": "Admitted books"} if package_id == "acme" else None
        ),
    )
    assert (
        package_description({"id": "acme_books", "description": "Stored books"}, "acme")
        == "Stored books"
    )
    assert (
        package_description({"id": "acme_books", "name": "Acme"}, "acme")
        == "Admitted books"
    )
    with pytest.raises(ValueError, match="does not declare a description"):
        package_description({"id": "acme_books"}, "missing")
    with pytest.raises(ValueError, match="does not declare a description"):
        package_description(
            {"id": "acme_books", "description": "x" * 401}, "acme_books"
        )


def test_host_management_comes_from_the_package(monkeypatch):
    """A stored host-managed flag wins. An older package uses the admitted flag."""
    from app.connectors.package_runtime import package_host_managed

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: ({"host_managed": True} if package_id == "acme" else None),
    )
    assert package_host_managed(
        {"id": "acme_books", "host_managed": False}, "acme"
    ) is (False)
    assert package_host_managed({"id": "acme_books"}, "acme") is True
    assert package_host_managed({"id": "acme_books"}, "missing") is False
    with pytest.raises(ValueError, match="does not declare host management"):
        package_host_managed({"id": "acme_books", "host_managed": "yes"}, "acme_books")


def test_binding_scopes_come_from_the_package(monkeypatch):
    """A stored scope list wins. An older package uses the admitted scopes."""
    from app.agentive.services.connector_packages import _binding_scopes

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {"binding": {"scopes": ["workspace"]}} if package_id == "acme" else None
        ),
    )
    assert _binding_scopes(
        {"id": "acme_books", "binding": {"scopes": ["personal"]}}, "acme"
    ) == ["personal"]
    assert _binding_scopes({"id": "acme_books", "binding": {}}, "acme") == ["workspace"]
    with pytest.raises(ValueError, match="does not declare binding scopes"):
        _binding_scopes({"id": "acme_books"}, "missing")
    with pytest.raises(ValueError, match="does not declare binding scopes"):
        _binding_scopes(
            {"id": "acme_books", "binding": {"scopes": ["workspace", "org"]}},
            "acme_books",
        )


def test_workspace_library_omits_a_personal_package(monkeypatch):
    """A package that cannot be installed for a workspace is not offered there."""
    from app.agentive.services import connector_packages as svc
    from app.connectors.package_discovery import PackageAdmission

    def manifest(name: str, scopes: list[str]) -> dict:
        return {
            "name": name,
            "description": name,
            "version": "1.0.0",
            "publisher": "local",
            "schema": "integral.connector/v1",
            "capabilities": {"actions": []},
            "binding": {"scopes": scopes},
            "configuration": [],
        }

    monkeypatch.setattr(
        svc,
        "current_admission",
        lambda: PackageAdmission(
            packages={
                "desk": manifest("Desk", ["workspace"]),
                "mine": manifest("Mine", ["personal"]),
            },
            rejected=[{"id": "broken", "reason": "schema"}],
        ),
    )
    listed = svc.catalogue()
    assert {row["id"] for row in listed["packages"]} == {"desk"}
    assert listed["rejected"] == [{"id": "broken", "reason": "schema"}]


def test_workspace_install_omits_a_personal_field(monkeypatch):
    """A personal field is not offered or saved for a workspace installation."""
    from app.agentive.services import connector_packages as svc
    from app.api.errors import BadRequestError
    from app.connectors.package_discovery import PackageAdmission

    manifest = {
        "name": "Desk",
        "description": "Desk",
        "version": "1.0.0",
        "publisher": "local",
        "schema": "integral.connector/v1",
        "capabilities": {"actions": []},
        "binding": {"scopes": ["workspace", "personal"]},
        "configuration": [
            {"key": "base_url", "label": "URL", "type": "url", "required": True},
            {
                "key": "home",
                "label": "Home",
                "type": "string",
                "required": True,
                "scope": "personal",
            },
        ],
    }
    monkeypatch.setattr(
        svc,
        "current_admission",
        lambda: PackageAdmission(packages={"desk": manifest}),
    )
    shown = svc.catalogue()["packages"][0]["configuration"]
    assert [field["key"] for field in shown] == ["base_url"]
    config, secrets = svc._split(manifest, {"base_url": "https://acme.example"})
    assert config == {"base_url": "https://acme.example"}
    assert secrets == {}
    with pytest.raises(BadRequestError, match="Unknown"):
        svc._split(manifest, {"base_url": "https://acme.example", "home": "mine"})


@pytest.mark.asyncio
async def test_disable_clears_a_staged_write_after_turning_off(monkeypatch):
    """Disable is saved before the staged review is cleared."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.services import connector_packages as svc

    row = SimpleNamespace(
        id="c",
        enabled=True,
        package_revision=1,
        pending_review={"id": "r", "status": "pending", "input": {"name": "Ada"}},
    )
    order: list[str] = []

    async def save():
        order.append(
            "off" if row.enabled is False and row.pending_review else "cleared"
        )

    row.save = save
    monkeypatch.setattr(svc, "_owned", AsyncMock(return_value=row))
    monkeypatch.setattr(svc.Connector, "get", AsyncMock(return_value=row))
    result = await svc.set_enabled("c", "u", False)
    assert result.enabled is False
    assert result.pending_review == {}
    assert order == ["off", "cleared"]


@pytest.mark.asyncio
async def test_disable_leaves_a_newer_enable_in_place(monkeypatch):
    """A disable that lost the race does not clear the newer staged review."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.services import connector_packages as svc

    original = SimpleNamespace(
        id="c", enabled=True, package_revision=1, pending_review={"id": "old"}
    )
    later = SimpleNamespace(
        id="c", enabled=True, package_revision=2, pending_review={"id": "new"}
    )

    async def save():
        return None

    original.save = save
    later.save = save
    monkeypatch.setattr(svc, "_owned", AsyncMock(return_value=original))
    monkeypatch.setattr(svc.Connector, "get", AsyncMock(return_value=later))
    result = await svc.set_enabled("c", "u", False)
    assert result is later
    assert later.enabled is True
    assert later.pending_review == {"id": "new"}


def test_trusted_host_url_requires_the_operator_list(monkeypatch):
    """A trusted URL is saved only when the operator named that host."""
    from app.agentive.services.connector_packages import _split
    from app.api.errors import BadRequestError

    manifest = {
        "configuration": [
            {
                "key": "base_url",
                "label": "URL",
                "type": "url",
                "egress": "trusted_host",
            }
        ]
    }
    monkeypatch.delenv("INTEGRAL_OUTBOUND_TRUSTED_HOSTS", raising=False)
    with pytest.raises(BadRequestError, match="trusted host"):
        _split(manifest, {"base_url": "https://crm.internal"})
    monkeypatch.setenv("INTEGRAL_OUTBOUND_TRUSTED_HOSTS", "crm.internal")
    config, secrets = _split(manifest, {"base_url": "https://crm.internal"})
    assert config["base_url"] == "https://crm.internal"
    assert secrets == {}


@pytest.mark.asyncio
async def test_request_rechecks_a_trusted_host_url(monkeypatch):
    """A saved trusted URL is refused once the operator removes that host."""
    from app.api.errors import BadRequestError
    from app.connectors.package_runtime import perform_request

    monkeypatch.delenv("INTEGRAL_OUTBOUND_TRUSTED_HOSTS", raising=False)

    async def fail(*_args, **_kwargs):
        raise AssertionError("request was sent")

    monkeypatch.setattr(
        "app.connectors.package_runtime.validate_outbound_http_url", fail
    )
    with pytest.raises(BadRequestError, match="trusted host"):
        await perform_request(
            {
                "configuration": [
                    {"key": "base_url", "type": "url", "egress": "trusted_host"}
                ],
                "http": {"base_url": "{{config.base_url}}"},
            },
            {"method": "GET", "path": "/ping"},
            {"base_url": "https://crm.internal"},
            {},
        )


def test_request_timeout_and_response_limit_are_capped():
    """Timeout stops at 30 seconds and a response limit stops at 1048576 bytes."""
    from app.connectors.package_runtime import _request_timeout, _response_limit

    assert _request_timeout({}) == 10
    assert _request_timeout({"http": {"timeout_seconds": 90}}) == 30
    assert _response_limit({}) == 262144
    assert _response_limit({"http": {"max_response_bytes": 9_000_000}}) == 1048576


@pytest.mark.asyncio
async def test_oversized_package_response_is_not_parsed(monkeypatch):
    """A body larger than the package limit is refused before JSON parsing."""
    from contextlib import nullcontext

    import httpx

    from app.connectors.package_runtime import perform_request

    async def allow(_url: str) -> None:
        return None

    monkeypatch.setattr(
        "app.connectors.package_runtime.validate_outbound_http_url", allow
    )
    monkeypatch.setattr(
        "app.connectors.package_runtime.pin_public_dns", lambda _url: nullcontext()
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 1100)

    manifest = {
        "http": {"base_url": "https://acme.example", "max_response_bytes": 1024},
        "auth": {"type": "none"},
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="max_response_bytes"):
            await perform_request(
                manifest, {"method": "GET", "path": "/ping"}, {}, {}, client=client
            )


def test_configuration_values_match_the_declared_type():
    """A saved value must match the field type on the package manifest."""
    from app.agentive.services.connector_packages import _split
    from app.api.errors import BadRequestError

    manifest = {
        "id": "acme_books",
        "configuration": [
            {"key": "name", "label": "Name", "type": "string"},
            {"key": "count", "label": "Count", "type": "number"},
            {"key": "active", "label": "Active", "type": "boolean"},
            {
                "key": "plan",
                "label": "Plan",
                "type": "select",
                "options": [
                    {"value": "a", "label": "A"},
                    {"value": "b", "label": "B"},
                ],
            },
        ],
    }
    config, secrets = _split(
        manifest, {"name": "Ada", "count": 0, "active": False, "plan": "b"}
    )
    assert config == {"name": "Ada", "count": 0, "active": False, "plan": "b"}
    assert secrets == {}
    with pytest.raises(BadRequestError, match="count must be a number"):
        _split(manifest, {"count": True})
    with pytest.raises(
        BadRequestError, match="plan must be one of the declared choices"
    ):
        _split(manifest, {"plan": "z"})


def test_package_configuration_comes_from_the_package(monkeypatch):
    """A stored field list wins. An older snapshot uses the admitted fields."""
    from app.agentive.services.connector_packages import _with_package_configuration

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {
                "configuration": [
                    {"key": "realm", "label": "Realm", "type": "string"},
                ]
            }
            if package_id == "acme"
            else None
        ),
    )
    stored = {
        "id": "acme_books",
        "configuration": [{"key": "account", "label": "Account", "type": "string"}],
    }
    fields = _with_package_configuration(stored, "acme")["configuration"]
    assert [field["key"] for field in fields] == ["account"]
    older = {"http": {"base_url": "https://books.example"}}
    assert _with_package_configuration(older, "acme")["configuration"][0]["key"] == (
        "realm"
    )
    assert "configuration" not in older
    assert "configuration" not in _with_package_configuration(
        {"id": "acme_books"}, "missing"
    )
    assert (
        _with_package_configuration({"id": "acme_books", "configuration": []}, "acme")[
            "configuration"
        ]
        == []
    )
    with pytest.raises(ValueError, match="does not declare configuration"):
        _with_package_configuration(
            {
                "id": "acme_books",
                "configuration": [
                    {"key": "account", "label": "Account", "type": "file"}
                ],
            },
            "acme_books",
        )


def test_package_auth_comes_from_the_package(monkeypatch):
    """A stored auth block wins. An older snapshot uses the admitted auth."""
    from app.agentive.services.connector_packages import _with_package_auth

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {
                "auth": {
                    "type": "api_key",
                    "secret": "admitted_key",
                    "inject": {"in": "header", "name": "X-Admitted"},
                }
            }
            if package_id == "acme"
            else None
        ),
    )
    stored = {
        "id": "acme_books",
        "auth": {
            "type": "api_key",
            "secret": "hook_key",
            "inject": {"in": "header", "name": "X-Acme"},
        },
    }
    assert _with_package_auth(stored, "acme")["auth"]["secret"] == "hook_key"
    older = {"http": {"base_url": "https://books.example"}}
    assert _with_package_auth(older, "acme")["auth"]["inject"]["name"] == "X-Admitted"
    assert "auth" not in _with_package_auth({"id": "acme_books"}, "missing")
    with pytest.raises(ValueError, match="does not declare auth"):
        _with_package_auth(
            {"id": "acme_books", "auth": {"type": "basic"}},
            "acme_books",
        )


def test_package_response_limit_comes_from_the_package(monkeypatch):
    """A stored response limit wins. An older snapshot uses the admitted limit."""
    from app.agentive.services.connector_packages import _with_package_response_limit

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {"http": {"max_response_bytes": 4096}} if package_id == "acme" else None
        ),
    )
    stored = {"id": "acme_books", "http": {"max_response_bytes": 2048}}
    assert (
        _with_package_response_limit(stored, "acme")["http"]["max_response_bytes"]
        == 2048
    )
    older = {"http": {"base_url": "https://books.example"}}
    assert (
        _with_package_response_limit(older, "acme")["http"]["max_response_bytes"]
        == 4096
    )
    assert (
        _with_package_response_limit({"id": "acme_books"}, "missing")["http"][
            "max_response_bytes"
        ]
        == 262144
    )
    with pytest.raises(ValueError, match="does not declare a response limit"):
        _with_package_response_limit(
            {"id": "acme_books", "http": {"max_response_bytes": 100}},
            "acme_books",
        )


def test_package_timeout_comes_from_the_package(monkeypatch):
    """A stored timeout wins. An older snapshot uses the admitted timeout."""
    from app.agentive.services.connector_packages import _with_package_timeout

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {"http": {"timeout_seconds": 20}} if package_id == "acme" else None
        ),
    )
    stored = {"id": "acme_books", "http": {"timeout_seconds": 12}}
    assert _with_package_timeout(stored, "acme")["http"]["timeout_seconds"] == 12.0
    older = {"http": {"base_url": "https://books.example"}}
    assert _with_package_timeout(older, "acme")["http"]["timeout_seconds"] == 20.0
    assert (
        _with_package_timeout({"id": "acme_books"}, "missing")["http"][
            "timeout_seconds"
        ]
        == 10.0
    )
    with pytest.raises(ValueError, match="does not declare a timeout"):
        _with_package_timeout(
            {"id": "acme_books", "http": {"timeout_seconds": 90}},
            "acme_books",
        )


def test_package_base_url_comes_from_the_package(monkeypatch):
    """A stored base URL wins. An older snapshot uses the admitted host."""
    from app.agentive.services.connector_packages import _with_package_base

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {"http": {"base_url": "https://admitted.example"}}
            if package_id == "acme"
            else None
        ),
    )
    stored = {"id": "acme_books", "http": {"base_url": "https://books.example"}}
    assert (
        _with_package_base(stored, "acme")["http"]["base_url"]
        == "https://books.example"
    )
    older = {"capabilities": {"actions": []}}
    assert (
        _with_package_base(older, "acme")["http"]["base_url"]
        == "https://admitted.example"
    )
    with pytest.raises(ValueError, match="does not declare a base URL"):
        _with_package_base({"id": "acme_books"}, "missing")
    with pytest.raises(ValueError, match="does not declare a base URL"):
        _with_package_base(
            {"id": "acme_books", "http": {"base_url": "http://books.example"}},
            "acme_books",
        )


def test_declared_sync_comes_from_the_package(monkeypatch):
    """A stored sync source wins. An older snapshot uses the admitted source."""
    from app.agentive.services.connector_packages import _declared_sync

    admitted = {
        "capabilities": {
            "sync": {
                "source": {"request": {"method": "GET", "path": "/admitted"}},
                "map": {"external_id": "$.id"},
            }
        }
    }
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: admitted if package_id == "acme" else None,
    )
    stored = {
        "id": "acme_books",
        "capabilities": {
            "sync": {
                "source": {"request": {"method": "GET", "path": "/hooks"}},
                "map": {"external_id": "$.key"},
            }
        },
    }
    assert _declared_sync(stored, "acme")["source"]["request"]["path"] == "/hooks"
    older = {"capabilities": {"sync": {"entry_type": "contact"}}}
    assert _declared_sync(older, "acme")["map"]["external_id"] == "$.id"
    assert _declared_sync({"capabilities": {}}, "missing") is None
    with pytest.raises(ValueError, match="does not declare a sync source"):
        _declared_sync(
            {
                "id": "acme_books",
                "capabilities": {
                    "sync": {
                        "source": {"request": {"method": "POST", "path": "/hooks"}},
                        "map": {"external_id": "$.id"},
                    }
                },
            },
            "acme_books",
        )


def test_package_action_comes_from_the_package(monkeypatch):
    """A stored action wins. An older snapshot uses the admitted action."""
    from app.agentive.services.connector_packages import _package_action
    from app.api.errors import BadRequestError

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {
                "capabilities": {
                    "actions": [
                        {
                            "name": "ping",
                            "effect": "read",
                            "request": {"method": "GET", "path": "/admitted"},
                        }
                    ]
                }
            }
            if package_id == "acme"
            else None
        ),
    )
    stored = {
        "id": "acme_books",
        "capabilities": {
            "actions": [
                {
                    "name": "ping",
                    "effect": "read",
                    "request": {"method": "GET", "path": "/hooks"},
                }
            ]
        },
    }
    assert _package_action(stored, "acme", "ping")["request"]["path"] == "/hooks"
    older = {"capabilities": {"actions": []}}
    assert _package_action(older, "acme", "ping")["request"]["path"] == "/admitted"
    assert _package_action({"capabilities": {}}, "missing", "ping") is None
    with pytest.raises(BadRequestError, match="GET"):
        _package_action(
            {
                "id": "acme_books",
                "capabilities": {
                    "actions": [
                        {
                            "name": "ping",
                            "effect": "read",
                            "request": {"method": "POST", "path": "/hooks"},
                        }
                    ]
                },
            },
            "acme_books",
            "ping",
        )


def test_health_request_comes_from_the_package(monkeypatch):
    """A stored health request wins. An older snapshot uses the admitted one."""
    from app.agentive.services.connector_packages import _health_request

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {"health": {"request": {"method": "GET", "path": "/admitted"}}}
            if package_id == "acme"
            else None
        ),
    )
    stored = {
        "id": "acme_books",
        "health": {"request": {"method": "GET", "path": "/hooks"}},
    }
    assert _health_request(stored, "acme")["path"] == "/hooks"
    older = {"capabilities": {"actions": [{"name": "ping"}]}}
    assert _health_request(older, "acme")["path"] == "/admitted"
    assert _health_request({"capabilities": {}}, "missing") is None
    with pytest.raises(ValueError, match="does not declare a health probe"):
        _health_request(
            {
                "id": "acme_books",
                "health": {"request": {"method": "GET", "path": "ping"}},
            },
            "acme_books",
        )


def test_channel_mode_comes_from_the_package(monkeypatch):
    """A stored channel mode wins. An older snapshot uses the admitted mode."""
    from app.agentive.services.connector_packages import _channel_mode

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {"capabilities": {"channel": {"mode": "webhook"}}}
            if package_id == "acme"
            else None
        ),
    )
    stored = {
        "id": "acme_hooks",
        "capabilities": {"channel": {"mode": "managed-text-v1"}},
    }
    assert _channel_mode(stored, "acme") == "managed-text-v1"
    older = {"capabilities": {"channel": {"ingress": {}}}}
    assert _channel_mode(older, "acme") == "webhook"
    with pytest.raises(ValueError, match="does not declare a channel mode"):
        _channel_mode({"capabilities": {}}, "missing")
    with pytest.raises(ValueError, match="does not declare a channel mode"):
        _channel_mode(
            {"id": "acme_hooks", "capabilities": {"channel": {"mode": "email"}}},
            "acme_hooks",
        )


def test_webhook_send_request_comes_from_the_package(monkeypatch):
    """A stored outbound request wins. An older snapshot uses the admitted one."""
    from app.agentive.services.connector_packages import _webhook_send_request

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {
                "capabilities": {
                    "channel": {
                        "send": {"request": {"method": "POST", "path": "/admitted"}}
                    }
                }
            }
            if package_id == "acme"
            else None
        ),
    )
    stored = {
        "id": "acme_hooks",
        "capabilities": {
            "channel": {"send": {"request": {"method": "POST", "path": "/hooks"}}}
        },
    }
    assert _webhook_send_request(stored, "acme")["path"] == "/hooks"
    older = {"capabilities": {"channel": {"mode": "webhook"}}}
    assert _webhook_send_request(older, "acme")["path"] == "/admitted"
    with pytest.raises(ValueError, match="does not declare an outbound message"):
        _webhook_send_request({"capabilities": {}}, "missing")
    with pytest.raises(ValueError, match="does not declare an outbound message"):
        _webhook_send_request(
            {
                "capabilities": {
                    "channel": {
                        "send": {"request": {"method": "GET", "path": "/hooks"}}
                    }
                }
            },
            "acme_hooks",
        )


def test_webhook_scheme_comes_from_the_package(monkeypatch):
    """A stored signature scheme wins. An older snapshot uses the admitted one."""
    from app.agentive.services.connector_packages import _webhook_verify_scheme

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {
                "capabilities": {
                    "channel": {"ingress": {"verify": {"scheme": "hmac_sha256"}}}
                }
            }
            if package_id == "acme"
            else None
        ),
    )
    stored = {
        "id": "acme_hooks",
        "capabilities": {"channel": {"ingress": {"verify": {"scheme": "hmac_sha256"}}}},
    }
    assert _webhook_verify_scheme(stored, "acme") == "hmac_sha256"
    older = {
        "capabilities": {"channel": {"ingress": {"verify": {"header": "X-Signature"}}}}
    }
    assert _webhook_verify_scheme(older, "acme") == "hmac_sha256"
    with pytest.raises(ValueError, match="does not declare a webhook scheme"):
        _webhook_verify_scheme({"capabilities": {}}, "missing")
    with pytest.raises(ValueError, match="does not declare a webhook scheme"):
        _webhook_verify_scheme(
            {"capabilities": {"channel": {"ingress": {"verify": {"scheme": "none"}}}}},
            "acme_hooks",
        )


def test_webhook_secret_field_comes_from_the_package(monkeypatch):
    """A stored secret field wins. An older snapshot uses the admitted name."""
    from app.agentive.services.connector_packages import _webhook_verify_secret

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {
                "capabilities": {
                    "channel": {"ingress": {"verify": {"secret": "admitted_secret"}}}
                }
            }
            if package_id == "acme"
            else None
        ),
    )
    stored = {
        "id": "acme_hooks",
        "capabilities": {"channel": {"ingress": {"verify": {"secret": "hook_secret"}}}},
    }
    assert _webhook_verify_secret(stored, "acme") == "hook_secret"
    older = {
        "capabilities": {"channel": {"ingress": {"verify": {"header": "X-Signature"}}}}
    }
    assert _webhook_verify_secret(older, "acme") == "admitted_secret"
    with pytest.raises(ValueError, match="does not declare a webhook secret"):
        _webhook_verify_secret({"capabilities": {}}, "missing")
    with pytest.raises(ValueError, match="does not declare a webhook secret"):
        _webhook_verify_secret(
            {"capabilities": {"channel": {"ingress": {"verify": {"secret": "  "}}}}},
            "acme_hooks",
        )


def test_webhook_header_comes_from_the_package(monkeypatch):
    """A stored signature header wins. An older snapshot uses the admitted name."""
    from app.agentive.services.connector_packages import _webhook_verify_header

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {
                "capabilities": {
                    "channel": {
                        "ingress": {"verify": {"header": "X-Admitted-Signature"}}
                    }
                }
            }
            if package_id == "acme"
            else None
        ),
    )
    stored = {
        "id": "acme_hooks",
        "capabilities": {
            "channel": {"ingress": {"verify": {"header": "X-Acme-Signature"}}}
        },
    }
    assert _webhook_verify_header(stored, "acme") == "X-Acme-Signature"
    older = {
        "capabilities": {"channel": {"ingress": {"verify": {"scheme": "hmac_sha256"}}}}
    }
    assert _webhook_verify_header(older, "acme") == "X-Admitted-Signature"
    with pytest.raises(ValueError, match="does not declare a webhook header"):
        _webhook_verify_header({"capabilities": {}}, "missing")
    with pytest.raises(ValueError, match="does not declare a webhook header"):
        _webhook_verify_header(
            {"capabilities": {"channel": {"ingress": {"verify": {"header": "  "}}}}},
            "acme_hooks",
        )


def test_webhook_text_comes_from_the_package(monkeypatch):
    """A stored message path wins. An older snapshot uses the admitted path."""
    from app.agentive.services.connector_packages import _webhook_text

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {"capabilities": {"channel": {"ingress": {"text": "$.admitted"}}}}
            if package_id == "acme"
            else None
        ),
    )
    stored = {
        "id": "acme_hooks",
        "capabilities": {"channel": {"ingress": {"text": "$.body"}}},
    }
    assert _webhook_text(stored, "acme") == "$.body"
    older = {"capabilities": {"channel": {"ingress": {"sender": "$.from"}}}}
    assert _webhook_text(older, "acme") == "$.admitted"
    with pytest.raises(ValueError, match="does not declare a webhook message"):
        _webhook_text({"capabilities": {}}, "missing")
    with pytest.raises(ValueError, match="does not declare a webhook message"):
        _webhook_text(
            {"capabilities": {"channel": {"ingress": {"text": "body"}}}},
            "acme_hooks",
        )


def test_webhook_sender_comes_from_the_package(monkeypatch):
    """A stored sender path wins. An older snapshot uses the admitted path."""
    from app.agentive.services.connector_packages import _webhook_sender

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {"capabilities": {"channel": {"ingress": {"sender": "$.admitted"}}}}
            if package_id == "acme"
            else None
        ),
    )
    stored = {
        "id": "acme_hooks",
        "capabilities": {"channel": {"ingress": {"sender": "$.from"}}},
    }
    assert _webhook_sender(stored, "acme") == "$.from"
    older = {"capabilities": {"channel": {"ingress": {"text": "$.body"}}}}
    assert _webhook_sender(older, "acme") == "$.admitted"
    with pytest.raises(ValueError, match="does not declare a webhook sender"):
        _webhook_sender({"capabilities": {}}, "missing")
    with pytest.raises(ValueError, match="does not declare a webhook sender"):
        _webhook_sender(
            {"capabilities": {"channel": {"ingress": {"sender": "from"}}}},
            "acme_hooks",
        )


def test_webhook_event_id_comes_from_the_package(monkeypatch):
    """A stored event-id path wins. An older snapshot uses the admitted path."""
    from app.agentive.services.connector_packages import _webhook_event_id

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {"capabilities": {"channel": {"ingress": {"event_id": "$.admitted"}}}}
            if package_id == "acme"
            else None
        ),
    )
    stored = {
        "id": "acme_hooks",
        "capabilities": {"channel": {"ingress": {"event_id": "$.id"}}},
    }
    assert _webhook_event_id(stored, "acme") == "$.id"
    older = {"capabilities": {"channel": {"ingress": {"sender": "$.from"}}}}
    assert _webhook_event_id(older, "acme") == "$.admitted"
    assert _webhook_event_id({"capabilities": {}}, "missing") is None
    with pytest.raises(ValueError, match="does not declare a webhook event id"):
        _webhook_event_id(
            {"capabilities": {"channel": {"ingress": {"event_id": "id"}}}},
            "acme_hooks",
        )


def test_webhook_timestamp_path_comes_from_the_package(monkeypatch):
    """A stored timestamp path wins. An older snapshot uses the admitted path."""
    from app.agentive.services.connector_packages import _webhook_timestamp

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {"capabilities": {"channel": {"ingress": {"timestamp": "$.admitted"}}}}
            if package_id == "acme"
            else None
        ),
    )
    stored = {
        "id": "acme_hooks",
        "capabilities": {"channel": {"ingress": {"timestamp": "$.when"}}},
    }
    assert _webhook_timestamp(stored, "acme") == "$.when"
    older = {"capabilities": {"channel": {"ingress": {"sender": "$.from"}}}}
    assert _webhook_timestamp(older, "acme") == "$.admitted"
    assert _webhook_timestamp({"capabilities": {}}, "missing") is None
    with pytest.raises(ValueError, match="does not declare a webhook timestamp"):
        _webhook_timestamp(
            {"capabilities": {"channel": {"ingress": {"timestamp": "when"}}}},
            "acme_hooks",
        )


def test_webhook_replay_window_comes_from_the_package(monkeypatch):
    """A stored replay window wins. An older snapshot uses the admitted package."""
    from app.agentive.services.connector_packages import _webhook_skew

    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {
                "capabilities": {
                    "channel": {"ingress": {"verify": {"max_skew_seconds": 120}}}
                }
            }
            if package_id == "acme"
            else None
        ),
    )
    stored = {
        "id": "acme_hooks",
        "capabilities": {"channel": {"ingress": {"verify": {"max_skew_seconds": 60}}}},
    }
    assert _webhook_skew(stored, "acme") == 60
    older = {
        "capabilities": {"channel": {"ingress": {"verify": {"scheme": "hmac_sha256"}}}}
    }
    assert _webhook_skew(older, "acme") == 120
    assert _webhook_skew({"capabilities": {}}, "missing") == 300
    with pytest.raises(ValueError, match="does not declare a replay window"):
        _webhook_skew(
            {
                "capabilities": {
                    "channel": {"ingress": {"verify": {"max_skew_seconds": 5}}}
                }
            },
            "acme_hooks",
        )


def test_webhook_timestamp_outside_the_window_is_refused():
    """A declared timestamp far from now is refused. The value stays out of the error."""
    import time

    from app.agentive.services.connector_packages import _require_fresh_webhook
    from app.api.errors import BadRequestError

    ingress = {"timestamp": "$.ts"}
    verify = {"max_skew_seconds": 300}
    with pytest.raises(BadRequestError, match="replay window") as raised:
        _require_fresh_webhook({"ts": int(time.time()) - 10_000}, ingress, verify)
    assert "10000" not in str(raised.value)
    _require_fresh_webhook({"ts": int(time.time())}, ingress, verify)
    _require_fresh_webhook({"text": "hello"}, {}, {})


@pytest.mark.asyncio
async def test_package_request_uses_the_guarded_redirect_client(monkeypatch):
    """A declarative request follows redirects only through the guarded client."""
    from contextlib import nullcontext

    import httpx

    from app.connectors.package_runtime import perform_request

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    seen: dict = {}

    def factory(**kwargs):
        seen["follow_redirects"] = kwargs.get("follow_redirects")
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout")
        )

    async def allow(_url: str) -> None:
        return None

    monkeypatch.setattr("app.connectors.package_runtime.guarded_async_client", factory)
    monkeypatch.setattr(
        "app.connectors.package_runtime.validate_outbound_http_url", allow
    )
    monkeypatch.setattr(
        "app.connectors.package_runtime.pin_public_dns", lambda _url: nullcontext()
    )
    result = await perform_request(
        {"http": {"base_url": "https://acme.example"}, "auth": {"type": "none"}},
        {"method": "GET", "path": "/ping"},
        {},
        {},
    )
    assert result == {"ok": True}
    assert seen["follow_redirects"] is True


@pytest.mark.asyncio
async def test_token_calls_use_the_guarded_client(monkeypatch):
    """Token exchange follows redirects through the guarded client."""
    from contextlib import nullcontext

    import httpx

    from app.connectors.package_runtime import post_oauth_token

    follows: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "token"})

    def factory(**kwargs):
        follows.append(kwargs.get("follow_redirects") is True)
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout")
        )

    async def allow(_url: str) -> None:
        return None

    monkeypatch.setattr("app.connectors.package_runtime.guarded_async_client", factory)
    monkeypatch.setattr(
        "app.connectors.package_runtime.validate_outbound_http_url", allow
    )
    monkeypatch.setattr(
        "app.connectors.package_runtime.pin_public_dns", lambda _url: nullcontext()
    )
    await post_oauth_token(
        {
            "token_url": "https://oauth.example/token",
            "client_authentication": "client_secret_post",
        },
        {"grant_type": "authorization_code"},
        client_id="id",
        client_secret="sec",
    )
    assert follows == [True]


@pytest.mark.asyncio
async def test_redirect_guard_refuses_before_pinning(monkeypatch):
    """A redirect host is refused before any address for it is pinned."""
    import httpx

    from app.api.errors import BadRequestError
    from app.services.url_safety import GuardedAsyncClient

    client = GuardedAsyncClient(follow_redirects=False)
    pinned: list[str] = []

    def refuse(url: str) -> None:
        if "evil.example" in str(url):
            raise BadRequestError(message="blocked redirect")

    def pin(url: str, *, required: bool = False) -> None:
        pinned.append(str(url))

    monkeypatch.setattr(
        "app.services.url_safety.validate_outbound_http_url_sync", refuse
    )
    monkeypatch.setattr(client._pin_scope, "pin_url", pin)
    response = httpx.Response(
        302,
        headers={"location": "https://evil.example/steal"},
        request=httpx.Request("GET", "https://acme.example/ping"),
    )
    with pytest.raises(BadRequestError, match="blocked redirect"):
        await client._guard_redirect(response)
    assert pinned == []
    await client.aclose()


@pytest.mark.asyncio
async def test_request_does_not_return_or_redirect_injected_credentials():
    """Keep an OAuth token out of redirected requests and reflected results."""
    import httpx

    from app.connectors.package_runtime import perform_request

    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.headers["Authorization"] == "Bearer fixture-access-token"
        if request.url.path == "/redirect":
            return httpx.Response(
                302, headers={"Location": "https://93.184.216.34/result"}
            )
        return httpx.Response(
            200,
            json={"echo": "Bearer fixture-access-token"},
            headers={"X-Echo": "fixture-access-token"},
        )

    manifest = {
        "auth": {"type": "oauth2"},
        "http": {"base_url": "https://93.184.216.34"},
    }
    secrets = {"access_token": "fixture-access-token"}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), follow_redirects=True
    ) as client:
        with pytest.raises(httpx.HTTPStatusError, match="302"):
            await perform_request(
                manifest,
                {"method": "GET", "path": "/redirect"},
                {},
                secrets,
                client=client,
            )
        body, headers = await perform_request(
            manifest,
            {"method": "GET", "path": "/result"},
            {},
            secrets,
            client=client,
            with_headers=True,
        )
    assert calls == ["/redirect", "/result"]
    assert body == {"echo": "Bearer [redacted]"}
    assert headers["x-echo"] == "[redacted]"
