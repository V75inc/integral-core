"""HTTP consent, private delivery and canonical invitation admission contracts."""

import pytest
from jvspatial.core.context import get_default_context

from app.models.edges import HAS_NOTIFICATION, IS_MEMBER_OF
from app.models.nodes import Invitation, Notification
from app.services.app_graph import create_notification
from app.services.permissions import get_user_node
from tests.test_invitation_mailbox_proof import invitee_account
from tests.test_resource_location import graph

pytestmark = pytest.mark.smoke


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["accept", "decline"])
async def test_canonical_notification_response_uses_invitation_not_metadata(
    client, authenticated_client, monkeypatch, action
):
    caller, issued, workspace_id, code = await invitee_account(
        client, authenticated_client, monkeypatch
    )
    async with caller:
        assert (
            await caller.post("/api/auth/verify-email", json={"code": code})
        ).status_code == 200
        me = (await caller.get("/api/auth/me")).json()["user"]
        recipient = await get_user_node(me["id"])
        invitation = await Invitation.find_one({"id": issued["invitation"]["id"]})
        assert invitation.status == "pending"
        notice = await create_notification(
            user_id=recipient.id,
            type="invitation",
            content="Synthetic canonical delivery",
            metadata={
                "payload": {
                    "invitation_id": invitation.id,
                    "invitation_revision": invitation.invitation_revision,
                    "workspace_id": "forged-other-workspace",
                    "role": "owner",
                }
            },
        )
        request = {
            "action": action,
            "invitation_revision": invitation.invitation_revision,
        }
        response = await caller.post(
            "/api/notifications/" + notice.id + "/respond", json=request
        )
        assert response.status_code == 200, response.text
        current = await Invitation.find_one({"id": invitation.id})
        assert current.status == ("accepted" if action == "accept" else "declined")
        database = get_default_context().database
        rows = await database.find(
            "edge",
            {
                "source": recipient.id,
                "target": workspace_id,
                "entity": IS_MEMBER_OF._entity_name(),
            },
            limit=2,
        )
        if action == "accept":
            assert len(rows) == 1
            assert rows[0]["context"]["role"] == "admin"
        else:
            assert rows == []
        grant_image = rows
        assert (await Notification.find_one({"id": notice.id})).read is True
        retry = await caller.post(
            "/api/notifications/" + notice.id + "/respond", json=request
        )
        assert retry.status_code == 200, retry.text
        assert (
            await database.find(
                "edge",
                {
                    "source": recipient.id,
                    "target": workspace_id,
                    "entity": IS_MEMBER_OF._entity_name(),
                },
                limit=2,
            )
            == grant_image
        )
        assert (
            await Invitation.find_one({"id": invitation.id})
        ).invitation_revision == current.invitation_revision


@pytest.mark.asyncio
async def test_pointerless_notification_cannot_grant_or_mark_accepted(
    authenticated_client,
):
    me = (await authenticated_client.get("/api/auth/me")).json()["user"]
    recipient = await get_user_node(me["id"])
    _, workspace, _, _, _ = await graph()
    workspace.kind = "organization"
    await workspace.save()
    notice = await create_notification(
        user_id=recipient.id,
        type="invite",
        content="Synthetic legacy delivery",
        metadata={"workspace_id": workspace.id, "role": "admin"},
    )
    response = await authenticated_client.post(
        "/api/notifications/" + notice.id + "/respond",
        json={"action": "accept", "invitation_revision": 0},
    )
    assert response.status_code in {400, 409}, response.text
    rows = await get_default_context().database.find(
        "edge",
        {
            "source": recipient.id,
            "target": workspace.id,
            "entity": IS_MEMBER_OF._entity_name(),
        },
        limit=2,
    )
    assert rows == []
    current = await Notification.find_one({"id": notice.id})
    assert current.read is False
    assert "response" not in current.metadata


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"action": None, "invitation_revision": 0},
        {"action": "ACCEPT", "invitation_revision": 0},
        {"action": True, "invitation_revision": 0},
        {"action": "accept", "invitation_revision": True},
        {"action": "accept", "invitation_revision": "0"},
        {"action": "accept", "invitation_revision": -1},
        {"action": "accept", "invitation_revision": 0, "role": "owner"},
    ],
)
async def test_notification_response_rejects_malformed_rights_before_effects(
    authenticated_client, payload
):
    me = (await authenticated_client.get("/api/auth/me")).json()["user"]
    recipient = await get_user_node(me["id"])
    _, workspace, _, _, _ = await graph()
    notice = await create_notification(
        user_id=recipient.id,
        type="invite",
        content="Synthetic malformed input target",
        metadata={"workspace_id": workspace.id, "role": "admin"},
    )
    response = await authenticated_client.post(
        "/api/notifications/" + notice.id + "/respond", json=payload
    )
    assert response.status_code == 400, response.text
    assert (await Notification.find_one({"id": notice.id})).read is False
    assert (
        await get_default_context().database.find(
            "edge",
            {
                "source": recipient.id,
                "target": workspace.id,
                "entity": IS_MEMBER_OF._entity_name(),
            },
            limit=2,
        )
        == []
    )


@pytest.mark.asyncio
async def test_notification_query_action_cannot_supply_missing_json_consent(
    authenticated_client,
):
    me = (await authenticated_client.get("/api/auth/me")).json()["user"]
    recipient = await get_user_node(me["id"])
    _, workspace, _, _, _ = await graph()
    notice = await create_notification(
        user_id=recipient.id,
        type="invite",
        content="Synthetic query input target",
        metadata={"workspace_id": workspace.id, "role": "admin"},
    )
    response = await authenticated_client.post(
        "/api/notifications/"
        + notice.id
        + "/respond?action=accept&invitation_revision=0"
    )
    assert response.status_code == 400, response.text
    assert (await Notification.find_one({"id": notice.id})).read is False


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["accept", "decline"])
async def test_notification_canonical_pointer_requires_current_mailbox(
    client, authenticated_client, monkeypatch, action
):
    caller, issued, workspace_id, _ = await invitee_account(
        client, authenticated_client, monkeypatch
    )
    async with caller:
        me = (await caller.get("/api/auth/me")).json()["user"]
        recipient = await get_user_node(me["id"])
        invitation = await Invitation.find_one({"id": issued["invitation"]["id"]})
        notice = await create_notification(
            user_id=recipient.id,
            type="invitation",
            content="Synthetic unverified delivery",
            metadata={
                "payload": {
                    "invitation_id": invitation.id,
                    "invitation_revision": invitation.invitation_revision,
                }
            },
        )
        response = await caller.post(
            "/api/notifications/" + notice.id + "/respond",
            json={
                "action": action,
                "invitation_revision": invitation.invitation_revision,
            },
        )
        assert response.status_code == 403, response.text
        assert (await Invitation.find_one({"id": invitation.id})).status == "pending"
        assert (await Notification.find_one({"id": notice.id})).read is False
        assert (
            await get_default_context().database.find(
                "edge",
                {
                    "source": recipient.id,
                    "target": workspace_id,
                    "entity": IS_MEMBER_OF._entity_name(),
                },
                limit=2,
            )
            == []
        )


@pytest.mark.asyncio
async def test_notification_stale_proposal_requires_renewed_consent(
    client, authenticated_client, monkeypatch
):
    caller, issued, workspace_id, code = await invitee_account(
        client, authenticated_client, monkeypatch
    )
    async with caller:
        assert (
            await caller.post("/api/auth/verify-email", json={"code": code})
        ).status_code == 200
        me = (await caller.get("/api/auth/me")).json()["user"]
        recipient = await get_user_node(me["id"])
        invitation = await Invitation.find_one({"id": issued["invitation"]["id"]})
        displayed_revision = invitation.invitation_revision
        notice = await create_notification(
            user_id=recipient.id,
            type="invitation",
            content="Synthetic stale delivery",
            metadata={
                "payload": {
                    "invitation_id": invitation.id,
                    "invitation_revision": displayed_revision,
                }
            },
        )
        # Synthetic independent proposal mutation: tests admission after the
        # actual current revision differs from the delivered proposal.
        invitation.invitation_revision += 1
        await invitation.save()
        response = await caller.post(
            "/api/notifications/" + notice.id + "/respond",
            json={"action": "accept", "invitation_revision": displayed_revision},
        )
        assert response.status_code == 409, response.text
        assert (await Invitation.find_one({"id": invitation.id})).status == "pending"
        assert (await Notification.find_one({"id": notice.id})).read is False
        assert (
            await get_default_context().database.find(
                "edge",
                {
                    "source": recipient.id,
                    "target": workspace_id,
                    "entity": IS_MEMBER_OF._entity_name(),
                },
                limit=2,
            )
            == []
        )


@pytest.mark.asyncio
async def test_foreign_notification_recipient_cannot_respond(
    client, authenticated_client, monkeypatch
):
    caller, issued, _, _ = await invitee_account(
        client, authenticated_client, monkeypatch
    )
    async with caller:
        me = (await caller.get("/api/auth/me")).json()["user"]
        recipient = await get_user_node(me["id"])
        invitation = await Invitation.find_one({"id": issued["invitation"]["id"]})
        notice = await create_notification(
            user_id=recipient.id,
            type="invitation",
            content="Private delivery",
            metadata={
                "payload": {
                    "invitation_id": invitation.id,
                    "invitation_revision": invitation.invitation_revision,
                }
            },
        )
        response = await authenticated_client.post(
            "/api/notifications/" + notice.id + "/respond",
            json={
                "action": "accept",
                "invitation_revision": invitation.invitation_revision,
            },
        )
        assert response.status_code == 404, response.text
        assert "Private delivery" not in response.text
        assert (await Invitation.find_one({"id": invitation.id})).status == "pending"
        assert (await Notification.find_one({"id": notice.id})).read is False


@pytest.mark.asyncio
@pytest.mark.parametrize("disposition", ["revoked", "expired"])
async def test_notification_current_terminal_state_prevents_grant(
    client, authenticated_client, monkeypatch, disposition
):
    caller, issued, workspace_id, code = await invitee_account(
        client, authenticated_client, monkeypatch
    )
    async with caller:
        assert (
            await caller.post("/api/auth/verify-email", json={"code": code})
        ).status_code == 200
        me = (await caller.get("/api/auth/me")).json()["user"]
        recipient = await get_user_node(me["id"])
        invitation = await Invitation.find_one({"id": issued["invitation"]["id"]})
        notice = await create_notification(
            user_id=recipient.id,
            type="invitation",
            content="Synthetic terminal delivery",
            metadata={
                "payload": {
                    "invitation_id": invitation.id,
                    "invitation_revision": invitation.invitation_revision,
                }
            },
        )
        if disposition == "revoked":
            response = await authenticated_client.delete(
                f"/api/workspaces/{workspace_id}/invitations/{invitation.id}"
            )
            assert response.status_code == 200, response.text
        else:
            invitation.expires_at = "2000-01-01T00:00:00+00:00"
            await invitation.save()
        response = await caller.post(
            "/api/notifications/" + notice.id + "/respond",
            json={"action": "accept", "invitation_revision": 0},
        )
        assert response.status_code == 409, response.text
        assert (await Notification.find_one({"id": notice.id})).read is False
        assert (
            await get_default_context().database.find(
                "edge",
                {
                    "source": recipient.id,
                    "target": workspace_id,
                    "entity": IS_MEMBER_OF._entity_name(),
                },
                limit=2,
            )
            == []
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("topology", ["missing", "foreign", "duplicate"])
async def test_notification_recipient_topology_rejects_ambiguity_before_grant(
    client, authenticated_client, monkeypatch, topology
):
    import uuid
    from copy import deepcopy

    caller, issued, workspace_id, code = await invitee_account(
        client, authenticated_client, monkeypatch
    )
    async with caller:
        assert (
            await caller.post("/api/auth/verify-email", json={"code": code})
        ).status_code == 200
        recipient = await get_user_node(
            (await caller.get("/api/auth/me")).json()["user"]["id"]
        )
        invitation = await Invitation.find_one({"id": issued["invitation"]["id"]})
        notice = await create_notification(
            user_id=recipient.id,
            type="invitation",
            content="Synthetic corrupted delivery",
            metadata={
                "payload": {
                    "invitation_id": invitation.id,
                    "invitation_revision": invitation.invitation_revision,
                }
            },
        )
        database = get_default_context().database
        rows = await database.find(
            "edge",
            {"target": notice.id, "entity": HAS_NOTIFICATION._entity_name()},
            limit=2,
        )
        assert len(rows) == 1
        relation = deepcopy(rows[0])
        if topology == "missing":
            await database.delete("edge", relation["id"])
        elif topology == "foreign":
            owner = await get_user_node(
                (await authenticated_client.get("/api/auth/me")).json()["user"]["id"]
            )
            relation["source"] = owner.id
            await database.save("edge", relation)
        else:
            relation["id"] = relation["id"].rsplit(".", 1)[0] + "." + uuid.uuid4().hex
            from app.services.app_operations.transaction_scope import (
                graph_transaction_available,
            )

            if graph_transaction_available(database):
                # PostgreSQL prevents this corrupt legacy image at storage.
                # Assert that prevention; do not weaken its unique index.
                with pytest.raises(Exception, match="edge_source_target_entity_uniq"):
                    await database.save("edge", relation)
                assert (
                    await Invitation.find_one({"id": invitation.id})
                ).status == "pending"
                assert (await Notification.find_one({"id": notice.id})).read is False
                assert (
                    await database.find(
                        "edge",
                        {
                            "source": recipient.id,
                            "target": workspace_id,
                            "entity": IS_MEMBER_OF._entity_name(),
                        },
                        limit=2,
                    )
                    == []
                )
                return
            await database.save("edge", relation)
            assert (
                len(
                    await database.find(
                        "edge",
                        {
                            "target": notice.id,
                            "entity": HAS_NOTIFICATION._entity_name(),
                        },
                        limit=3,
                    )
                )
                == 2
            )
        response = await caller.post(
            "/api/notifications/" + notice.id + "/respond",
            json={
                "action": "accept",
                "invitation_revision": invitation.invitation_revision,
            },
        )
        assert response.status_code == 503, response.text
        assert (await Invitation.find_one({"id": invitation.id})).status == "pending"
        assert (await Notification.find_one({"id": notice.id})).read is False
        assert (
            await database.find(
                "edge",
                {
                    "source": recipient.id,
                    "target": workspace_id,
                    "entity": IS_MEMBER_OF._entity_name(),
                },
                limit=2,
            )
            == []
        )
