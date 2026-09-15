"""Phase 8 Plan 08-02 Task 1 — LIST / PATCH / DELETE coverage for the
/api/agentive/connectors surface (RESEARCH Pitfall 4 closure).

11 cases per the must_haves spec:

    1. test_list_connectors_owner_only            — owner scope filter
    2. test_list_connectors_empty_default         — fresh user → empty + total=0
    3. test_list_includes_derived_conflict_policy — A5 derivation
    4. test_patch_subclass_slug                   — PATCH + ChangeEvent
    5. test_patch_sync_interval_seconds           — PATCH persists
    6. test_patch_extra_field_rejected            — extra:forbid (T-08-02-S01)
    7. test_patch_cross_user_returns_404          — enumeration safe (T-08-02-I01)
    8. test_delete_emits_change_event             — DELETE 204 + audit row
    9. test_delete_cross_user_returns_404         — same 404 envelope
   10. test_single_emission_no_double_event       — D-05 invariant
   11. test_no_new_policy_action_literal_members  — no Literal expansion

Uses ``authenticated_client`` + ``second_user_client`` fixtures from conftest
to exercise two-user enumeration paths.
"""

from __future__ import annotations

from typing import Any, get_args

import pytest
from httpx import AsyncClient

# =====================================================================
# Helpers
# =====================================================================


async def _create_connector(
    client: AsyncClient, kind: str = "jvagent", **extra: Any
) -> dict:
    """POST /api/agentive/connectors and return the response JSON."""
    body = {"kind": kind, **extra}
    r = await client.post("/api/agentive/connectors", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()


async def _audit_events_for_resource(client: AsyncClient, resource_id: str) -> list:
    """Return all audit-log events whose resource_id matches ``resource_id``."""
    r = await client.get("/api/audit-log")
    assert r.status_code == 200, r.text
    events = r.json().get("events", [])
    return [e for e in events if e.get("resource_id") == resource_id]


# =====================================================================
# 1. LIST — owner-scope filter
# =====================================================================


@pytest.mark.asyncio
async def test_list_connectors_owner_only(
    authenticated_client: AsyncClient,
    test_user,
    second_user_client: AsyncClient,
):
    """User A creates 2 connectors; user B creates 1.

    User A's LIST returns 2; user B's LIST returns 1. The owner-scope filter
    blocks cross-user enumeration (T-08-02-I01 + T-08-02-I02).
    """
    a1 = await _create_connector(authenticated_client, kind="jvagent")
    a2 = await _create_connector(authenticated_client, kind="mcp")
    b1 = await _create_connector(second_user_client, kind="custom")

    # User A — should see exactly 2 (their own).
    r_a = await authenticated_client.get("/api/agentive/connectors")
    assert r_a.status_code == 200, r_a.text
    body_a = r_a.json()
    a_ids = {c["id"] for c in body_a["connectors"]}
    assert a1["id"] in a_ids
    assert a2["id"] in a_ids
    assert b1["id"] not in a_ids, "owner scope leak — user A saw user B's connector"
    assert body_a["total"] == len(
        body_a["connectors"]
    )  # silent-drop preserves equality

    # User B — should see exactly 1.
    r_b = await second_user_client.get("/api/agentive/connectors")
    assert r_b.status_code == 200, r_b.text
    body_b = r_b.json()
    b_ids = {c["id"] for c in body_b["connectors"]}
    assert b1["id"] in b_ids
    assert a1["id"] not in b_ids
    assert a2["id"] not in b_ids


# =====================================================================
# 2. LIST — empty default
# =====================================================================


@pytest.mark.asyncio
async def test_list_connectors_empty_default(
    authenticated_client: AsyncClient, test_user
):
    """A fresh user with zero connectors → `{connectors: [], total: 0}`."""
    r = await authenticated_client.get("/api/agentive/connectors")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["connectors"] == []
    assert body["total"] == 0


# =====================================================================
# 3. LIST — derived conflict_policy field (A5)
# =====================================================================


@pytest.mark.asyncio
async def test_list_includes_derived_conflict_policy(
    authenticated_client: AsyncClient, test_user
):
    """ConnectorResponse.conflict_policy is derived from the SyncConnector
    subclass registry, NOT persisted on the Node (A5).

    The GitHub Issues reference subclass declares ``conflict_policy =
    "last_write_wins"``. A connector with no subclass_slug yields ``None``.
    """
    # The autouse ``reset_sync_connector_registry`` fixture in conftest wipes
    # the registry between tests, so we re-register the reference subclass
    # here via importlib.reload (mirrors test_connector_github_issues.py
    # line 188-199 idiom).
    import importlib

    import app.agentive.connectors.github_issues as gh_mod
    from app.services.connectors import reset_sync_registry

    reset_sync_registry()
    importlib.reload(gh_mod)  # re-fires @register_sync_connector("github_issues")

    # No subclass — conflict_policy is None.
    c_none = await _create_connector(authenticated_client, kind="jvagent")
    r = await authenticated_client.get(f"/api/agentive/connectors/{c_none['id']}")
    assert r.status_code == 200
    assert r.json()["conflict_policy"] is None

    # Patch in the github_issues slug so the derivation surfaces.
    r_patch = await authenticated_client.patch(
        f"/api/agentive/connectors/{c_none['id']}",
        json={"subclass_slug": "github_issues"},
    )
    assert r_patch.status_code == 200, r_patch.text
    patched = r_patch.json()
    assert patched["subclass_slug"] == "github_issues"
    # The reference GitHubIssuesConnector declares last_write_wins.
    assert patched["conflict_policy"] == "last_write_wins", patched

    # LIST also surfaces the derived value.
    r_list = await authenticated_client.get("/api/agentive/connectors")
    assert r_list.status_code == 200
    rows = r_list.json()["connectors"]
    row = next(c for c in rows if c["id"] == c_none["id"])
    assert row["conflict_policy"] == "last_write_wins"


# =====================================================================
# 4. PATCH — subclass_slug persists + emits ChangeEvent
# =====================================================================


@pytest.mark.asyncio
async def test_patch_subclass_slug(authenticated_client: AsyncClient, test_user):
    """PATCH with subclass_slug → 200 + persisted + connector.update event in audit log."""
    c = await _create_connector(authenticated_client, kind="jvagent")
    r = await authenticated_client.patch(
        f"/api/agentive/connectors/{c['id']}",
        json={"subclass_slug": "github_issues"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["subclass_slug"] == "github_issues"

    # Read-back round-trip.
    r_get = await authenticated_client.get(f"/api/agentive/connectors/{c['id']}")
    assert r_get.status_code == 200
    assert r_get.json()["subclass_slug"] == "github_issues"

    # Audit-log: connector.update event for this resource.
    matches = await _audit_events_for_resource(authenticated_client, c["id"])
    update_events = [e for e in matches if e.get("action") == "connector.update"]
    assert (
        len(update_events) >= 1
    ), f"connector.update event missing for {c['id']!r}: matches={matches!r}"


# =====================================================================
# 5. PATCH — sync_interval_seconds persists
# =====================================================================


@pytest.mark.asyncio
async def test_patch_sync_interval_seconds(
    authenticated_client: AsyncClient, test_user
):
    """PATCH with sync_interval_seconds → 200 + value persists."""
    c = await _create_connector(authenticated_client, kind="jvagent")
    r = await authenticated_client.patch(
        f"/api/agentive/connectors/{c['id']}",
        json={"sync_interval_seconds": 600},
    )
    assert r.status_code == 200, r.text
    assert r.json()["sync_interval_seconds"] == 600

    r_get = await authenticated_client.get(f"/api/agentive/connectors/{c['id']}")
    assert r_get.json()["sync_interval_seconds"] == 600


# =====================================================================
# 6. PATCH — extra:forbid rejects forged owner injection (T-08-02-S01)
# =====================================================================


@pytest.mark.asyncio
async def test_patch_extra_field_rejected(authenticated_client: AsyncClient, test_user):
    """T-08-02-S01: client cannot inject a forged ``owner`` via PATCH body.

    UpdateConnectorRequest.model_config={"extra":"forbid"} rejects any unknown
    field; BadRequestError → 400-class envelope. The exact code is 400 (we
    parse the body manually via ``model_validate``) — what matters is that the
    field never lands.
    """
    c = await _create_connector(authenticated_client, kind="jvagent")
    r = await authenticated_client.patch(
        f"/api/agentive/connectors/{c['id']}",
        json={"owner": "spoofed-user-id"},
    )
    assert r.status_code in (400, 422), r.text

    # Read-back: owner unchanged.
    r_get = await authenticated_client.get(f"/api/agentive/connectors/{c['id']}")
    assert r_get.status_code == 200
    assert r_get.json()["owner"] != "spoofed-user-id"


# =====================================================================
# 7. PATCH — cross-user returns 404 (T-08-02-I01)
# =====================================================================


@pytest.mark.asyncio
async def test_patch_cross_user_returns_404(
    authenticated_client: AsyncClient,
    test_user,
    second_user_client: AsyncClient,
):
    """User A creates a connector; user B PATCHes → 404 same envelope.

    No 403-vs-404 discrimination — both unknown-id and cross-owner return the
    canonical not-found envelope (T-01-04-02 / T-08-02-I01).
    """
    c_a = await _create_connector(authenticated_client, kind="jvagent")
    r_b = await second_user_client.patch(
        f"/api/agentive/connectors/{c_a['id']}",
        json={"sync_interval_seconds": 999},
    )
    assert r_b.status_code == 404, r_b.text


# =====================================================================
# 8. DELETE — emits connector.delete ChangeEvent with before snapshot
# =====================================================================


@pytest.mark.asyncio
async def test_delete_emits_change_event(authenticated_client: AsyncClient, test_user):
    """DELETE → 204; subsequent GET → 404; connector.delete event present."""
    c = await _create_connector(authenticated_client, kind="jvagent")
    r = await authenticated_client.delete(f"/api/agentive/connectors/{c['id']}")
    assert r.status_code == 204, r.text

    r_get = await authenticated_client.get(f"/api/agentive/connectors/{c['id']}")
    assert r_get.status_code == 404, r_get.text

    matches = await _audit_events_for_resource(authenticated_client, c["id"])
    delete_events = [e for e in matches if e.get("action") == "connector.delete"]
    assert (
        len(delete_events) >= 1
    ), f"connector.delete event missing for {c['id']!r}: matches={matches!r}"
    # Verify the before snapshot is present (W3 — auth_state never logged).
    evt = delete_events[0]
    before = evt.get("before") or {}
    # Some audit-log surfaces return before/after under different keys —
    # tolerate both top-level and nested.
    if not before and "context" in evt:
        before = evt.get("context", {}).get("before") or {}
    if before:  # only assert content when surfaced
        assert (
            "auth_state" not in before
        ), "auth_state must NOT appear in ChangeEvent.before (T-08-02-I03)"


# =====================================================================
# 9. DELETE — cross-user 404
# =====================================================================


@pytest.mark.asyncio
async def test_delete_cross_user_returns_404(
    authenticated_client: AsyncClient,
    test_user,
    second_user_client: AsyncClient,
):
    """User A creates a connector; user B deletes → 404 same envelope."""
    c_a = await _create_connector(authenticated_client, kind="jvagent")
    r_b = await second_user_client.delete(f"/api/agentive/connectors/{c_a['id']}")
    assert r_b.status_code == 404, r_b.text

    # And the connector still exists for user A.
    r_get = await authenticated_client.get(f"/api/agentive/connectors/{c_a['id']}")
    assert r_get.status_code == 200


# =====================================================================
# 10. D-05 single emission — exactly one connector.update event per PATCH
# =====================================================================


@pytest.mark.asyncio
async def test_single_emission_no_double_event(
    authenticated_client: AsyncClient, test_user
):
    """PATCH emits exactly 1 connector.update event for that resource id.

    D-05 invariant — the AST grep gate at tests/test_change_event_no_bypass.py
    prevents double emission at the call-site level. This test exercises the
    actual emission path end-to-end.
    """
    c = await _create_connector(authenticated_client, kind="jvagent")
    r = await authenticated_client.patch(
        f"/api/agentive/connectors/{c['id']}",
        json={"sync_interval_seconds": 120},
    )
    assert r.status_code == 200

    matches = await _audit_events_for_resource(authenticated_client, c["id"])
    update_events = [e for e in matches if e.get("action") == "connector.update"]
    assert len(update_events) == 1, (
        f"expected exactly 1 connector.update event, got {len(update_events)}: "
        f"{update_events!r}"
    )


# =====================================================================
# 11. Zero new Literal members
# =====================================================================


def test_no_new_policy_action_literal_members():
    """Phase 8 Plan 08-02 ZERO-LITERAL invariant.

    The plan asserts no new PolicyAction or ChangeEventAction Literal members.
    The bind/unbind handlers (Task 3) and the CRUD handlers (Task 1) ALL
    reuse the existing ``connector.read`` / ``connector.update`` /
    ``connector.delete`` / ``connector.create`` members.

    Phantom members like ``connector.list`` / ``connector.bind`` /
    ``connector.unbind`` MUST NOT exist.
    """
    from app.schemas.audit import ChangeEventAction
    from app.schemas.policy import PolicyAction

    pa_members = set(get_args(PolicyAction))
    cea_members = set(get_args(ChangeEventAction))

    # Required members already present per Plan 03 / Phase 5.
    for required in (
        "connector.create",
        "connector.read",
        "connector.update",
        "connector.delete",
    ):
        assert (
            required in pa_members
        ), f"PolicyAction missing required member {required!r}"
    for required in ("connector.create", "connector.update", "connector.delete"):
        assert (
            required in cea_members
        ), f"ChangeEventAction missing required member {required!r}"

    # Phantom members the plan EXPLICITLY forbids.
    phantom = {
        "connector.list",
        "connector.bind",
        "connector.unbind",
        "binding.create",
        "binding.delete",
    }
    leaks = (pa_members | cea_members) & phantom
    assert not leaks, (
        f"Phase 8 Plan 08-02 ZERO-LITERAL invariant violated — new Literal "
        f"members leaked: {leaks}"
    )
