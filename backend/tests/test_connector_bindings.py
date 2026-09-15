"""Phase 8 Plan 08-02 Task 3 — IS_CONNECTED_TO Track binding coverage
(closes B1 revision gap; REQUIREMENTS L123 SET-02 + ROADMAP L391).

11 cases per the must_haves spec:

    1. test_create_binding_201_and_appears_in_list
    2. test_create_binding_with_mapping_profile_yaml_persists
    3. test_create_binding_idempotent (T-08-02-T03)
    4. test_create_binding_emits_single_connector_update_event (D-05)
    5. test_create_binding_cross_user_connector_404 (T-08-02-I01)
    6. test_create_binding_cross_user_track_404 (T-08-02-E02)
    7. test_create_binding_rejects_extra_field (T-08-02-S01)
    8. test_delete_binding_204_then_gone_from_list
    9. test_delete_binding_emits_connector_update_event_with_pre_state
   10. test_delete_nonexistent_binding_404
   11. test_no_new_policy_action_or_change_event_literal
"""

from __future__ import annotations

from typing import Any, get_args

import pytest
from httpx import AsyncClient

# =====================================================================
# Helpers
# =====================================================================


async def _create_connector(client: AsyncClient, **extra: Any) -> dict:
    """POST /api/agentive/connectors with default jvagent kind."""
    body = {"kind": "jvagent", **extra}
    r = await client.post("/api/agentive/connectors", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()


async def _create_track(client: AsyncClient, title: str = "Test Track") -> dict:
    """POST /api/tracks returning the inner ``track`` payload."""
    r = await client.post("/api/tracks", json={"title": title})
    assert r.status_code in (200, 201), r.text
    body = r.json()
    return body.get("track") or body


async def _audit_events_for_resource(client: AsyncClient, resource_id: str) -> list:
    r = await client.get("/api/audit-log")
    assert r.status_code == 200, r.text
    events = r.json().get("events", [])
    return [e for e in events if e.get("resource_id") == resource_id]


# =====================================================================
# 1. Create binding 201 + appears in list
# =====================================================================


@pytest.mark.asyncio
async def test_create_binding_201_and_appears_in_list(
    authenticated_client: AsyncClient, test_user
):
    """POST creates an IS_CONNECTED_TO edge; subsequent GET surfaces it."""
    c = await _create_connector(authenticated_client)
    t = await _create_track(authenticated_client, title="Bound Track")

    r = await authenticated_client.post(
        f"/api/agentive/connectors/{c['id']}/bindings",
        json={"track_id": t["id"]},
    )
    assert r.status_code in (200, 201), r.text
    binding = r.json()
    assert binding["connector_id"] == c["id"]
    assert binding["track_id"] == t["id"]
    assert binding["mapping_profile_yaml"] == ""
    assert binding["bidirectional"] is False

    r_list = await authenticated_client.get(
        f"/api/agentive/connectors/{c['id']}/bindings"
    )
    assert r_list.status_code == 200, r_list.text
    body_list = r_list.json()
    assert body_list["total"] == 1
    assert any(b["track_id"] == t["id"] for b in body_list["bindings"])


# =====================================================================
# 2. mapping_profile_yaml persists on the edge
# =====================================================================


@pytest.mark.asyncio
async def test_create_binding_with_mapping_profile_yaml_persists(
    authenticated_client: AsyncClient, test_user
):
    """POST with non-empty yaml — GET shows the yaml on the binding row.

    Per CLAUDE.md jvspatial pillar #2: ``mapping_profile_yaml`` lives on the
    IsConnectedTo edge as a typed field, NOT on the Connector or Track node.
    """
    c = await _create_connector(authenticated_client)
    t = await _create_track(authenticated_client)
    yaml = "field_map:\n  title: external_title\n"
    r = await authenticated_client.post(
        f"/api/agentive/connectors/{c['id']}/bindings",
        json={
            "track_id": t["id"],
            "mapping_profile_yaml": yaml,
            "bidirectional": True,
        },
    )
    assert r.status_code in (200, 201), r.text

    r_list = await authenticated_client.get(
        f"/api/agentive/connectors/{c['id']}/bindings"
    )
    rows = r_list.json()["bindings"]
    assert len(rows) == 1
    assert rows[0]["mapping_profile_yaml"] == yaml
    assert rows[0]["bidirectional"] is True


# =====================================================================
# 3. Idempotency (T-08-02-T03)
# =====================================================================


@pytest.mark.asyncio
async def test_create_binding_idempotent(authenticated_client: AsyncClient, test_user):
    """Two POSTs with the same (connector, track) → GET shows EXACTLY 1 binding.

    T-08-02-T03 mitigation: the handler is idempotent — a duplicate POST
    returns the existing edge metadata without creating another edge.
    """
    c = await _create_connector(authenticated_client)
    t = await _create_track(authenticated_client)

    r1 = await authenticated_client.post(
        f"/api/agentive/connectors/{c['id']}/bindings",
        json={"track_id": t["id"]},
    )
    assert r1.status_code in (200, 201), r1.text
    r2 = await authenticated_client.post(
        f"/api/agentive/connectors/{c['id']}/bindings",
        json={"track_id": t["id"]},
    )
    assert r2.status_code in (200, 201), r2.text

    r_list = await authenticated_client.get(
        f"/api/agentive/connectors/{c['id']}/bindings"
    )
    body = r_list.json()
    assert body["total"] == 1, body
    assert len(body["bindings"]) == 1


# =====================================================================
# 4. Single ChangeEvent emission (D-05)
# =====================================================================


@pytest.mark.asyncio
async def test_create_binding_emits_single_connector_update_event(
    authenticated_client: AsyncClient, test_user
):
    """POST emits exactly 1 connector.update ChangeEvent for the connector id.

    D-05 invariant — bind/unbind REUSE the connector.update Literal; the
    audit row's ``after.bindings`` field carries the post-bind track-id list.
    """
    c = await _create_connector(authenticated_client)
    t = await _create_track(authenticated_client)

    r = await authenticated_client.post(
        f"/api/agentive/connectors/{c['id']}/bindings",
        json={"track_id": t["id"]},
    )
    assert r.status_code in (200, 201)

    matches = await _audit_events_for_resource(authenticated_client, c["id"])
    update_events = [e for e in matches if e.get("action") == "connector.update"]
    assert (
        len(update_events) == 1
    ), f"expected exactly 1 connector.update event, got {len(update_events)}"


# =====================================================================
# 5. Cross-user connector 404 (T-08-02-I01)
# =====================================================================


@pytest.mark.asyncio
async def test_create_binding_cross_user_connector_404(
    authenticated_client: AsyncClient,
    test_user,
    second_user_client: AsyncClient,
):
    """User A owns connector; user B POSTs bind → 404 (same envelope, no leak)."""
    c_a = await _create_connector(authenticated_client)
    # user B creates their own track
    t_b = await _create_track(second_user_client, title="B's track")

    r = await second_user_client.post(
        f"/api/agentive/connectors/{c_a['id']}/bindings",
        json={"track_id": t_b["id"]},
    )
    assert r.status_code == 404, r.text


# =====================================================================
# 6. Cross-user track 404 (T-08-02-E02)
# =====================================================================


@pytest.mark.asyncio
async def test_create_binding_cross_user_track_404(
    authenticated_client: AsyncClient,
    test_user,
    second_user_client: AsyncClient,
):
    """User A owns connector; user B owns track; user A POSTs bind → 404.

    T-08-02-E02: ``resolve_role(user_a, "track", track_b) != "owner"`` →
    same 404 envelope (no enumeration leak — we never reveal user B's track).
    """
    c_a = await _create_connector(authenticated_client)
    t_b = await _create_track(second_user_client, title="B's private track")

    r = await authenticated_client.post(
        f"/api/agentive/connectors/{c_a['id']}/bindings",
        json={"track_id": t_b["id"]},
    )
    assert r.status_code == 404, r.text


# =====================================================================
# 7. extra:forbid (T-08-02-S01 spillover)
# =====================================================================


@pytest.mark.asyncio
async def test_create_binding_rejects_extra_field(
    authenticated_client: AsyncClient, test_user
):
    """POST with an unknown field → 400/422 (extra:forbid)."""
    c = await _create_connector(authenticated_client)
    t = await _create_track(authenticated_client)

    r = await authenticated_client.post(
        f"/api/agentive/connectors/{c['id']}/bindings",
        json={"track_id": t["id"], "owner": "spoofed"},
    )
    assert r.status_code in (400, 422), r.text


# =====================================================================
# 8. Delete binding 204 → list shows 0
# =====================================================================


@pytest.mark.asyncio
async def test_delete_binding_204_then_gone_from_list(
    authenticated_client: AsyncClient, test_user
):
    """DELETE binding → 204; subsequent GET → 0 bindings."""
    c = await _create_connector(authenticated_client)
    t = await _create_track(authenticated_client)
    await authenticated_client.post(
        f"/api/agentive/connectors/{c['id']}/bindings",
        json={"track_id": t["id"]},
    )

    r_del = await authenticated_client.delete(
        f"/api/agentive/connectors/{c['id']}/bindings/{t['id']}"
    )
    assert r_del.status_code == 204, r_del.text

    r_list = await authenticated_client.get(
        f"/api/agentive/connectors/{c['id']}/bindings"
    )
    assert r_list.status_code == 200
    assert r_list.json()["total"] == 0


# =====================================================================
# 9. Delete emits ChangeEvent capturing the bindings-list delta
# =====================================================================


@pytest.mark.asyncio
async def test_delete_binding_emits_connector_update_event_with_pre_state(
    authenticated_client: AsyncClient, test_user
):
    """DELETE binding emits connector.update with before.bindings containing
    the unbinding's track id and after.bindings without it."""
    c = await _create_connector(authenticated_client)
    t = await _create_track(authenticated_client)
    await authenticated_client.post(
        f"/api/agentive/connectors/{c['id']}/bindings",
        json={"track_id": t["id"]},
    )

    r_del = await authenticated_client.delete(
        f"/api/agentive/connectors/{c['id']}/bindings/{t['id']}"
    )
    assert r_del.status_code == 204

    matches = await _audit_events_for_resource(authenticated_client, c["id"])
    update_events = [e for e in matches if e.get("action") == "connector.update"]
    # At least 2 connector.update events — one from bind, one from unbind.
    assert len(update_events) >= 2, (
        f"expected ≥2 connector.update events (bind + unbind), got "
        f"{len(update_events)}"
    )

    # Find the unbind event — its before.bindings should include the track id,
    # its after.bindings should NOT. The audit-log surface may return events
    # in either order — scan all and find the row whose after.bindings is
    # empty but before.bindings contained the track id (that is the unbind).
    def _bindings_of(evt: dict, key: str) -> list:
        slot = evt.get(key) or {}
        if not slot and "context" in evt:
            slot = evt.get("context", {}).get(key) or {}
        return list((slot or {}).get("bindings") or [])

    unbind_evts = [
        e
        for e in update_events
        if t["id"] in _bindings_of(e, "before")
        and t["id"] not in _bindings_of(e, "after")
    ]
    assert (
        len(unbind_evts) >= 1
    ), f"unbind ChangeEvent missing — bindings deltas={[(_bindings_of(e, 'before'), _bindings_of(e, 'after')) for e in update_events]!r}"


# =====================================================================
# 10. Delete nonexistent binding → 404
# =====================================================================


@pytest.mark.asyncio
async def test_delete_nonexistent_binding_404(
    authenticated_client: AsyncClient, test_user
):
    """DELETE with no prior POST → 404."""
    c = await _create_connector(authenticated_client)
    t = await _create_track(authenticated_client)
    # NO prior bind.
    r = await authenticated_client.delete(
        f"/api/agentive/connectors/{c['id']}/bindings/{t['id']}"
    )
    assert r.status_code == 404, r.text


# =====================================================================
# 11. Zero new Literal members (ZERO-LITERAL invariant)
# =====================================================================


def test_no_new_policy_action_or_change_event_literal():
    """bind/unbind mutations REUSE connector.update + connector.read.

    No phantom ``connector.bind`` / ``connector.unbind`` / ``binding.create``
    Literal members may exist in either PolicyAction or ChangeEventAction.
    """
    from app.schemas.audit import ChangeEventAction
    from app.schemas.policy import PolicyAction

    pa_members = set(get_args(PolicyAction))
    cea_members = set(get_args(ChangeEventAction))

    # Required reused members must be present.
    assert "connector.update" in pa_members
    assert "connector.read" in pa_members
    assert "connector.update" in cea_members

    # Phantom members the plan EXPLICITLY forbids.
    phantom = {
        "connector.bind",
        "connector.unbind",
        "binding.create",
        "binding.delete",
    }
    leaks = (pa_members | cea_members) & phantom
    assert not leaks, (
        f"Phase 8 Plan 08-02 Task 3 ZERO-LITERAL invariant violated — "
        f"new Literal members leaked: {leaks}"
    )
