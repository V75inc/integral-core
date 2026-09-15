"""Phase 18 — provenance contract for QuickBooks-materialized entries.

Confirms I-CON-01 holds end-to-end through ``sync_one_connector``:

- ``provenance.source = "connector"`` (ActorKind Literal member — never
  the free-string ``"connector:..."`` form).
- ``provenance.source_id = "{connector_id}:{external_id}"`` — runtime
  writes the split shape; the connector contributes only the external_id.
- ``custom_fields._source_system = "quickbooks"`` + ``_source_version``
  (QB SyncToken) + ``_last_synced_at`` fast-path scalars per plan §5.2.

Uses a self-contained pipeline: drives `sync_one_connector` with a fake
SyncConnector that yields the QuickBooks fixtures, against a fresh
Workspace + Finance install + IS_CONNECTED_TO wiring. The full Intuit
OAuth flow is not exercised here — that lives in
`test_quickbooks_oauth_endpoints.py`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List

import pytest

from app.agentive.connectors.quickbooks import QuickBooksConnector
from app.agentive.services.connector_registry_node import create_connector
from app.models.edges import CONTAINS, IS_CONNECTED_TO
from app.models.nodes import App, ContentProfile, Entry, Track, User, Workspace
from app.services.app_graph import catalog_user, ensure_integral_app_graph
from app.services.app_lifecycle import install_app
from app.services.connectors import (
    ExternalRecord,
    SyncConnector,
    register_sync_connector,
)
from app.services.connectors.registry import reset_sync_registry
from app.services.connectors.sync_runtime import sync_one_connector
from app.services.content_profile_loader import load_library_profiles_with_issues
from app.utils.time import utc_now_iso
from tests.domain_apps.fixtures.quickbooks_mock import REALM_ID, load


@pytest.fixture(autouse=True)
def _clean_registry():
    """Ensure the SyncConnector registry is fresh per test."""
    yield
    reset_sync_registry()


async def _bootstrap() -> Dict[str, Any]:
    await ensure_integral_app_graph()
    now = utc_now_iso()
    workspace = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="WS Prov",
        name_fold="ws prov",
        created_at=now,
        updated_at=now,
    )
    owner = await User.create(
        user_id="auth-prov", display_name="Prov Owner", created_at=now
    )
    await catalog_user(owner)
    # Install Finance App.
    specs, _ = load_library_profiles_with_issues(profiles_root=Path("app/profiles"))
    spec = next(s for s in specs if s.slug == "finance")
    manifest = dict(spec.manifest)
    pkg = dict(manifest.get("package") or {})
    pkg["version"] = spec.version or "1.0.0"
    manifest["package"] = pkg
    lib = await ContentProfile.create(
        name=manifest["package"]["name"],
        scope="app",
        manifest=manifest,
        library_package=True,
        version="1.0.0",
        created_at=now,
        updated_at=now,
    )
    install = await install_app(
        workspace_id=workspace.id, library_cp_id=lib.id, actor_id=owner.id
    )
    app = await App.get(install["app_id"])
    tracks = await app.nodes(edge=[CONTAINS], node=["Track"])
    track_by_title = {getattr(t, "title", ""): t for t in tracks}
    return {
        "workspace": workspace,
        "owner": owner,
        "app": app,
        "tracks": track_by_title,
    }


async def _create_qb_connector(fx: Dict[str, Any]) -> Any:
    """Create a connector + wire IS_CONNECTED_TO to all five Finance tracks."""
    c = await create_connector(
        owner=fx["owner"].id,
        auth_state={
            "realm_id": REALM_ID,
            "access_token": "tok-a",
            "refresh_token": "tok-r",
            "access_token_expires_at": (
                datetime.now(timezone.utc).replace(year=2099).isoformat()
            ),
            "environment": "sandbox",
        },
    )
    c.subclass_slug = "test_quickbooks_static"
    await c.save()
    for title in ("Invoices", "Expenses", "Customers", "Vendors", "Bills"):
        await c.connect(
            fx["tracks"][title], edge=IS_CONNECTED_TO, mapping_profile_yaml=""
        )
    return c


@pytest.mark.asyncio
async def test_synced_finance_entry_carries_provenance_split_shape_and_scalars():
    """I-CON-01 + -QB §5.2 — synced QB Invoice carries split-shape provenance
    + the ``_source_system`` / ``_source_version`` / ``_last_synced_at`` scalars.

    Registers a static fake SyncConnector under a unique test slug that yields
    a single Invoice from the QuickBooks fixtures and delegates to_entry to
    the real QuickBooksConnector. Drives sync_one_connector and inspects the
    materialized Entry.
    """
    invoice_payload = load("invoice_query")["QueryResponse"]["Invoice"][0]
    sync_token = invoice_payload["SyncToken"]
    external_id = invoice_payload["Id"]

    @register_sync_connector("test_quickbooks_static")
    class _StaticQBConnector(SyncConnector):
        slug = "test_quickbooks_static"
        conflict_policy = "last_write_wins"

        def __init__(self):
            # Delegate to_entry to the real QuickBooksConnector so the
            # provenance scalars are identical to production sync.
            self._qb = QuickBooksConnector()

        async def sync_pull(self, *, connector):
            payload = dict(invoice_payload)
            payload["_qb_entity"] = "Invoice"
            payload["_realm_id"] = REALM_ID
            yield ExternalRecord(
                external_id=str(external_id),
                payload=payload,
                updated_at=(payload.get("MetaData") or {}).get("LastUpdatedTime"),
            )

        def idempotency_key_for(self, record):
            return self._qb.idempotency_key_for(record)

        def to_entry(self, record):
            return self._qb.to_entry(record)

    fx = await _bootstrap()
    connector = await _create_qb_connector(fx)
    stats = await sync_one_connector(connector)
    assert stats.get("created", 0) == 1

    # The Invoice landed on the Invoices track (multi-track routing).
    invoices_track = fx["tracks"]["Invoices"]
    entries = await Entry.find({"track_id": invoices_track.id})
    assert len(entries) == 1
    entry = entries[0]

    # Provenance split shape (I-CON-01).
    prov = entry.provenance
    assert prov is not None
    assert prov.source == "connector"  # ActorKind Literal member
    assert prov.source_id == f"{connector.id}:{external_id}"
    assert prov.confidence == 1.0

    # Fast-path scalars per plan §5.2.
    cf = entry.custom_fields or {}
    assert cf["_source_system"] == "quickbooks"
    assert cf["_source_version"] == sync_token
    assert cf["_qb_entity"] == "Invoice"
    assert cf["_realm_id"] == REALM_ID
    assert cf.get("_last_synced_at")  # non-empty ISO timestamp
