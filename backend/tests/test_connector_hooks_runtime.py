"""Phase 30.5 — connector.dedup / connector.auto_link runtime dispatch."""

from __future__ import annotations

import pytest

from app.models.edges import CONTAINS, IS_MEMBER_OF, IS_OF_TYPE, OWNS, REFERENCES
from app.models.nodes import Entry, EntryType, Track, User, Workspace
from app.utils.time import utc_now_iso


async def _workspace() -> Workspace:
    now = utc_now_iso()
    return await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Connector Hooks WS",
        name_fold="connector hooks ws",
        created_at=now,
        updated_at=now,
    )


async def _connector_owner(ws: Workspace, label: str) -> User:
    """A real principal for the ToolContext.

    In production ``_run_connector_hooks`` passes ``connector.owner`` — a User
    node id — down into ``ToolContext``, and the tool facade resolves that id
    against the graph on every candidate it considers
    (``find_entries_in_track_type`` gates each track AND each entry through
    ``resolve_role``). A bare string like ``"u_owner"`` resolves to nothing, so
    the ACL filter drops every candidate and the hook silently links nothing.
    These fixtures therefore need a real, workspace-scoped user, which is also
    what makes the assertions meaningful: the link happens *through* the ACL,
    not around it.
    """
    user = await User.create(user_id=f"conn_{label}", display_name=f"Owner {label}")
    await user.connect(ws, edge=IS_MEMBER_OF, role="owner")
    return user


async def _contact_track(ws: Workspace, owner: User) -> tuple[Track, EntryType]:
    now = utc_now_iso()
    track = await Track.create(
        title="Contacts",
        owner_id=owner.id,
        workspace_id=ws.id,
    )
    await ws.connect(track, edge=CONTAINS)
    await owner.connect(track, edge=OWNS)
    et = await EntryType.create(
        name="Contact",
        track_id=track.id,
        is_template=False,
        created_at=now,
        updated_at=now,
    )
    return track, et


async def _contact(
    *,
    track: Track,
    entry_type: EntryType,
    email: str,
    owner: User,
    title: str = "Contact",
) -> Entry:
    now = utc_now_iso()
    entry = await Entry.create(
        title=title,
        body="",
        track_id=track.id,
        author_id=owner.id,
        custom_fields={"email": email},
    )
    await track.connect(entry, edge=CONTAINS, added_at=now)
    await entry.connect(entry_type, edge=IS_OF_TYPE, assigned_at=now)
    return entry


def _register_crm_connector_hooks(workspace_id: str) -> None:
    from app.services.hooks.registry import (
        register_workspace_hooks,
        register_workspace_tools,
    )

    register_workspace_tools(
        workspace_id,
        "crm",
        [
            {
                "key": "normalize_email_for_match",
                "handler_ref": "app.profiles.crm.tools.email_match:normalize",
                "parameters_schema": {},
                "output_schema": {},
            }
        ],
    )
    register_workspace_hooks(
        workspace_id,
        "crm",
        [
            {
                "point": "connector.dedup",
                "key": "qb_customer_to_contact",
                "match": {
                    "connector_slug": "quickbooks",
                    "source_entry_type": "qb_customer",
                },
                "mode": "declarative",
                "declarative": {
                    "target_track_type": "contacts",
                    "target_entry_type": "contact",
                    "match_field_pairs": [
                        {
                            "source": "custom_fields.email",
                            "target": "custom_fields.email",
                            "normalize": "lower_strip",
                        }
                    ],
                    "action_on_match": "link_references",
                    "references_field_key": "crm_account",
                    "link_field": "crm_link_status",
                },
            },
            {
                "point": "connector.auto_link",
                "key": "gmail_thread_to_contact",
                "match": {
                    "connector_slug": "gmail",
                    "source_entry_type": "email_thread",
                },
                "mode": "tool",
                "tool": "normalize_email_for_match",
                "tool_input": {
                    "target_track_type": "contacts",
                    "target_match_field": "custom_fields.email",
                },
            },
        ],
    )


@pytest.mark.asyncio
async def test_dedup_hook_links_qb_customer_to_contact():
    from app.services.hooks.connector_runtime import run_connector_post_materialize
    from app.services.hooks.registry import clear_workspace_registrations

    ws = await _workspace()
    owner = await _connector_owner(ws, "dedup")
    _register_crm_connector_hooks(ws.id)
    track, et = await _contact_track(ws, owner)
    contact = await _contact(
        track=track,
        entry_type=et,
        email="billing@acme.example",
        owner=owner,
        title="Acme",
    )
    now = utc_now_iso()
    qb_track = await Track.create(
        title="Finance Customers",
        owner_id=owner.id,
        workspace_id=ws.id,
    )
    await owner.connect(qb_track, edge=OWNS)
    qb_customer = await Entry.create(
        title="Acme QB",
        body="",
        track_id=qb_track.id,
        author_id=owner.id,
        custom_fields={"email": "billing@acme.example"},
    )
    await qb_track.connect(qb_customer, edge=CONTAINS, added_at=now)

    await run_connector_post_materialize(
        entry=qb_customer,
        workspace_id=ws.id,
        connector_slug="quickbooks",
        entry_type_key="qb_customer",
        actor_user_id=owner.id,
    )

    cf = qb_customer.custom_fields or {}
    assert cf.get("crm_link_status") == "linked"
    targets = await qb_customer.nodes(
        edge=["REFERENCES"], direction="out", node=["Entry"]
    )
    assert any(t.id == contact.id for t in targets)
    clear_workspace_registrations(ws.id)


@pytest.mark.asyncio
async def test_auto_link_hook_wires_thread_to_contact():
    from app.services.hooks.connector_runtime import run_connector_post_materialize
    from app.services.hooks.registry import clear_workspace_registrations

    ws = await _workspace()
    owner = await _connector_owner(ws, "autolink")
    _register_crm_connector_hooks(ws.id)
    track, et = await _contact_track(ws, owner)
    contact = await _contact(
        track=track,
        entry_type=et,
        email="maya@contoso.example",
        owner=owner,
        title="Maya",
    )
    now = utc_now_iso()
    comm_track = await Track.create(
        title="Communications",
        owner_id=owner.id,
        workspace_id=ws.id,
    )
    await owner.connect(comm_track, edge=OWNS)
    thread = await Entry.create(
        title="Thread",
        body="",
        track_id=comm_track.id,
        author_id=owner.id,
        custom_fields={"participant_emails": ["maya@contoso.example"]},
    )
    await comm_track.connect(thread, edge=CONTAINS, added_at=now)

    await run_connector_post_materialize(
        entry=thread,
        workspace_id=ws.id,
        connector_slug="gmail",
        entry_type_key="email_thread",
        actor_user_id=owner.id,
    )

    ctx = await thread.get_context()
    edges = await ctx.find_edges_between(thread.id, contact.id, edge_class=REFERENCES)
    assert edges
    assert any(
        (getattr(e, "field_key", "") or "") == "related_communications" for e in edges
    )
    clear_workspace_registrations(ws.id)


@pytest.mark.asyncio
async def test_auto_link_links_nothing_when_the_actor_cannot_see_the_targets():
    """The ACL half of the same path, and why it fails quietly.

    ``ToolContext.find_entries_in_track_type`` resolves the actor's role on
    every candidate track and entry, so a connector whose owner has no access
    to the contacts track matches nothing — the hook completes, wires zero
    edges, and looks exactly like "auto_link is broken".

    Pinned because the distinction is invisible at the call site: the test
    above and this one differ only in whether the actor can read the target.
    """
    from app.services.hooks.connector_runtime import run_connector_post_materialize
    from app.services.hooks.registry import clear_workspace_registrations

    ws = await _workspace()
    owner = await _connector_owner(ws, "autolink_visible")
    stranger = await User.create(user_id="conn_stranger", display_name="Stranger")
    _register_crm_connector_hooks(ws.id)
    track, et = await _contact_track(ws, owner)
    contact = await _contact(
        track=track,
        entry_type=et,
        email="maya@contoso.example",
        owner=owner,
        title="Maya",
    )
    now = utc_now_iso()
    comm_track = await Track.create(
        title="Communications",
        owner_id=owner.id,
        workspace_id=ws.id,
    )
    await owner.connect(comm_track, edge=OWNS)
    thread = await Entry.create(
        title="Thread",
        body="",
        track_id=comm_track.id,
        author_id=owner.id,
        custom_fields={"participant_emails": ["maya@contoso.example"]},
    )
    await comm_track.connect(thread, edge=CONTAINS, added_at=now)

    await run_connector_post_materialize(
        entry=thread,
        workspace_id=ws.id,
        connector_slug="gmail",
        entry_type_key="email_thread",
        actor_user_id=stranger.id,
    )

    ctx = await thread.get_context()
    edges = await ctx.find_edges_between(thread.id, contact.id, edge_class=REFERENCES)
    assert not edges, (
        "auto_link wired a REFERENCES edge to an entry the connector's owner "
        "cannot read — the tool facade's ACL gate is not holding"
    )
    clear_workspace_registrations(ws.id)
