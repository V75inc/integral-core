"""I-GRAPH-01 node attachment regression suite (Phase 10.5 consolidated).

Covers direct wiring helpers and backfill scripts for Notifications,
ShareLinks, UploadSessions, Conflicts, Approvals, AgentConfigs,
ConversationContexts, ChannelIdentities, governance Policies, and
ContentProfile drafts. Slow suite — excluded from default ``pytest -q``.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.agentive.edges import (
    HAS_AGENT_CONFIG,
    HAS_CHANNEL_IDENTITY,
    HAS_ORG_AGENT,
    HAS_SYSTEM_AGENT,
)
from app.agentive.nodes import AgentConfig, ChannelIdentity, ConversationContext
from app.agentive.services.agent_registry_node import wire_agent_config_attachment_edge
from app.agentive.services.conversation_context import (
    get_or_create_conversation_context,
)
from app.models.edges import (
    COLLABORATES_ON,
    CONTAINS,
    HAS_APPROVAL,
    HAS_APPROVAL_DECISION,
    HAS_CONFLICT,
    HAS_DRAFT_PROFILE,
    HAS_GOVERNANCE_POLICY,
    HAS_NOTIFICATION,
    HAS_SHARE_LINK,
    HAS_UPLOAD_SESSION,
    OWNS,
)
from app.models.nodes import (
    APP_NODE_ID,
    App,
    Approval,
    Conflict,
    ContentProfile,
    Entry,
    EntryType,
    IntegralApp,
    Notification,
    Policy,
    ShareLink,
    Track,
    UploadSession,
    User,
)
from app.services.app_graph import (
    catalog_app,
    catalog_track,
    catalog_user,
    create_notification,
    link_notification,
)
from app.services.connectors.conflict_records import create_conflict_record
from app.services.content_profile_atomic_swap import discard_draft, fork_draft
from app.services.personal_workspace import ensure_personal_workspace
from app.services.policy_registry import (
    materialize_governance_policies_for_content_profile,
)
from app.services.share_links import mint_share_link

# ===== NOTIFICATION =====


async def _notification_make_user(user_id: str) -> User:
    user = await User.create(user_id=user_id, display_name=f"Test {user_id}")
    await catalog_user(user)
    return user


# ---------------------------------------------------------------------------
# create_notification — canonical helper wires HAS_NOTIFICATION
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_notification_wires_has_notification_edge():
    """The canonical helper persists the Notification AND wires the edge."""
    user = await _notification_make_user("u_helper_test")

    notif = await create_notification(
        user_id=user.id,
        type="system",
        content="hello",
        read=False,
    )
    assert notif.id

    # Walk User —HAS_NOTIFICATION→ Notification; the freshly minted notif
    # must surface in the result set.
    linked = await user.nodes(edge=[HAS_NOTIFICATION], node=["Notification"])
    linked_ids = {n.id for n in linked}
    assert (
        notif.id in linked_ids
    ), f"create_notification did not wire HAS_NOTIFICATION; user neighbors={linked_ids}"


@pytest.mark.asyncio
async def test_create_notification_is_idempotent_on_repeat_link():
    """Calling create_notification twice for the same user wires two edges
    (one per Notification node) — not duplicate edges for one node.
    """
    user = await _notification_make_user("u_idempotent")

    n1 = await create_notification(user_id=user.id, type="system", content="a")
    n2 = await create_notification(user_id=user.id, type="system", content="b")

    linked = await user.nodes(edge=[HAS_NOTIFICATION], node=["Notification"])
    linked_ids = {n.id for n in linked}
    assert n1.id in linked_ids
    assert n2.id in linked_ids


@pytest.mark.asyncio
async def test_create_notification_refuses_missing_user():
    """If the recipient User node doesn't exist (e.g. synthetic service id),
    the helper refuses to persist an unwired Notification — it rolls back
    the row and raises ``ValueError`` per I-GRAPH-01 (every Notification
    MUST wire User —HAS_NOTIFICATION→ Notification). This tightens the
    pre-Plan-10.5-01 best-effort semantics of
    ``sharing._emit_share_notification``.
    """
    with pytest.raises(ValueError, match="user node missing"):
        await create_notification(
            user_id="u_does_not_exist",
            type="system",
            content="orphan probe",
            read=False,
        )

    # Rollback must leave no orphan Notification carrying the synthetic id.
    orphans = await Notification.find({"context.user_id": "u_does_not_exist"})
    assert orphans == [], f"refused create left orphan Notification(s): {orphans}"


# ---------------------------------------------------------------------------
# Refactored call sites: sharing._emit_share_notification + router
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_share_notification_path_wires_edge():
    """``services.sharing._emit_share_notification`` now routes through
    ``create_notification`` — recipient.HAS_NOTIFICATION must include
    the share notif."""
    from app.services import sharing

    user = await _notification_make_user("u_share_recipient")

    # Internal helper is private but stable surface for this regression.
    await sharing._emit_share_notification(
        recipient_user_id=user.id,
        kind="share.granted",
        title="A shared X with you",
        body="Click to view.",
        resource_type="app",
        resource_id="app_id_placeholder",
    )

    linked = await user.nodes(edge=[HAS_NOTIFICATION], node=["Notification"])
    assert (
        len(linked) >= 1
    ), f"share notification produced no HAS_NOTIFICATION edge; got {linked}"
    # The created notification should carry the share kind.
    kinds = {getattr(n, "type", None) for n in linked}
    assert "share.granted" in kinds


@pytest.mark.asyncio
async def test_router_dispatch_wires_edge():
    """``notification_router.dispatch`` now routes through
    ``create_notification`` — the resulting Notification must be
    HAS_NOTIFICATION-linked to the recipient User."""
    from app.services import notification_router

    user = await _notification_make_user("u_router_test")

    result = await notification_router.dispatch(
        user_id=user.id,
        kind="system",
        payload={"content": "from router"},
        actor_id=user.user_id,
        actor_kind="human",
        channels=["in_app"],
    )
    notification_id = result.get("notification_id")
    assert notification_id, f"router dispatch produced no notification_id: {result}"

    linked = await user.nodes(edge=[HAS_NOTIFICATION], node=["Notification"])
    linked_ids = {n.id for n in linked}
    assert notification_id in linked_ids, (
        f"router-created Notification {notification_id} not in "
        f"User.HAS_NOTIFICATION neighbors: {linked_ids}"
    )


# ---------------------------------------------------------------------------
# link_notification is idempotent (regression — pre-existing contract)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_link_notification_is_idempotent():
    """Repeated link_notification calls do not produce duplicate edges."""
    user = await _notification_make_user("u_link_idem")
    notif = await Notification.create(
        user_id=user.id,
        type="system",
        content="x",
        read=False,  # noqa: I-GRAPH-01-test-exempt
    )
    await link_notification(user, notif)
    await link_notification(user, notif)
    await link_notification(user, notif)

    linked = await user.nodes(edge=[HAS_NOTIFICATION], node=["Notification"])
    # Exactly one edge to this notification.
    matching = [n for n in linked if n.id == notif.id]
    assert (
        len(matching) == 1
    ), f"link_notification not idempotent — got {len(matching)} matches"


# ===== SHARE_LINK =====


async def _share_link_setup_owner_with_app_and_track() -> (
    tuple[User, App, Track, Entry]
):
    """Create owner User + Workspace + App + Track + Entry, all wired."""
    from datetime import datetime

    from app.models.edges import CONTAINS, OWNS

    now = datetime.now().isoformat()
    owner = await User.create(
        user_id="u_share_link_test_owner",
        display_name="Owner",
        created_at=now,
        updated_at=now,
    )
    await catalog_user(owner)
    workspace = await ensure_personal_workspace(owner)

    app = await App.create(
        name="Test App",
        owner_user_id=owner.id,
        workspace_id=workspace.id,
        visibility="private",
        created_at=now,
        updated_at=now,
    )
    await catalog_app(app)
    await owner.connect(app, edge=OWNS, granted_at=now)

    track = await Track.create(
        title="Test Track",
        owner_id=owner.id,
        workspace_id=workspace.id,
        created_at=now,
        updated_at=now,
    )
    await catalog_track(track)
    await app.connect(track, edge=CONTAINS, added_at=now)
    await owner.connect(track, edge=OWNS, granted_at=now)

    et = await EntryType.create(
        name="Post",
        track_id=track.id,
        is_template=False,
        created_at=now,
        updated_at=now,
    )
    entry = await Entry.create(
        title="Test Entry",
        type_id=et.id,
        track_id=track.id,
        author_id=owner.id,
        status="active",
        created_at=now,
        updated_at=now,
    )
    await track.connect(entry, edge=CONTAINS, added_at=now)
    return owner, app, track, entry


# ---------------------------------------------------------------------------
# mint_share_link wires HAS_SHARE_LINK for every resource type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "resource_kind",
    ["app", "track", "entry"],
)
@pytest.mark.asyncio
async def test_mint_share_link_wires_has_share_link(resource_kind: str):
    """For app / track / entry, the minted link is reachable via
    ``<resource>.nodes(edge=[HAS_SHARE_LINK])``.

    Entry-level minting requires DIRECT ownership (inherited owner caps
    to editor — see permissions.resolve_role rule 5); the fixture wires
    a direct COLLABORATES_ON{role=owner} for the entry case.
    """
    from datetime import datetime

    from app.models.edges import COLLABORATES_ON

    owner, app, track, entry = await _share_link_setup_owner_with_app_and_track()
    resource_map = {"app": app, "track": track, "entry": entry}
    resource = resource_map[resource_kind]

    if resource_kind == "entry":
        await owner.connect(
            entry,
            edge=COLLABORATES_ON,
            role="owner",
            invited_at=datetime.now().isoformat(),
        )

    result = await mint_share_link(
        actor_user_id=owner.id,
        resource_type=resource_kind,
        resource_id=resource.id,
        role="viewer",
    )
    link_id = result["share_link"]["id"]
    assert link_id

    linked = await resource.nodes(edge=[HAS_SHARE_LINK], node=["ShareLink"])
    linked_ids = {n.id for n in linked}
    assert link_id in linked_ids, (
        f"{resource_kind} resource did not wire HAS_SHARE_LINK; "
        f"neighbors={linked_ids}"
    )


# ---------------------------------------------------------------------------
# Backfill script repairs pre-Plan-10.5-02 orphans
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_share_link_edges_repairs_orphan():
    """Persist a bare ShareLink (no edge), run backfill, assert wired."""
    from scripts.backfill_share_link_edges import backfill_share_link_edges

    owner, app, _track, _entry = await _share_link_setup_owner_with_app_and_track()

    bare_link = await ShareLink.create(
        resource_type="app",
        resource_id=app.id,
        workspace_id=app.workspace_id,
        role="viewer",
        token_hash="abc",
        created_by=owner.id,
        revoked_at=None,
        redemptions=0,
    )
    assert bare_link.id

    stats = await backfill_share_link_edges(dry_run=False)
    assert stats["wired"] >= 1

    linked = await app.nodes(edge=[HAS_SHARE_LINK], node=["ShareLink"])
    linked_ids = {n.id for n in linked}
    assert bare_link.id in linked_ids


@pytest.mark.asyncio
async def test_backfill_share_link_edges_idempotent():
    """Run backfill twice — second pass produces zero new wires."""
    from scripts.backfill_share_link_edges import backfill_share_link_edges

    owner, app, _track, _entry = await _share_link_setup_owner_with_app_and_track()
    await ShareLink.create(
        resource_type="app",
        resource_id=app.id,
        workspace_id=app.workspace_id,
        role="viewer",
        token_hash="def",
        created_by=owner.id,
        revoked_at=None,
        redemptions=0,
    )

    s1 = await backfill_share_link_edges(dry_run=False)
    s2 = await backfill_share_link_edges(dry_run=False)
    assert s2["wired"] == 0, f"second-pass wired non-zero: {s2}"
    assert s2["already_wired"] >= s1["wired"]


# ===== UPLOAD_SESSION =====


async def _upload_session_setup_entry() -> tuple[User, Track, Entry]:
    from datetime import datetime

    from app.models.edges import CONTAINS, OWNS

    now = datetime.now().isoformat()
    owner = await User.create(
        user_id="u_upload_session_test",
        display_name="Owner",
        created_at=now,
        updated_at=now,
    )
    await catalog_user(owner)
    workspace = await ensure_personal_workspace(owner)

    track = await Track.create(
        title="Track",
        owner_id=owner.id,
        workspace_id=workspace.id,
        created_at=now,
        updated_at=now,
    )
    await catalog_track(track)
    await owner.connect(track, edge=OWNS, granted_at=now)

    et = await EntryType.create(name="Post", track_id=track.id, is_template=False)
    entry = await Entry.create(
        title="E",
        type_id=et.id,
        track_id=track.id,
        author_id=owner.id,
        status="active",
        created_at=now,
        updated_at=now,
    )
    await track.connect(entry, edge=CONTAINS, added_at=now)
    return owner, track, entry


@pytest.mark.asyncio
async def test_start_session_wires_has_upload_session(monkeypatch):
    """``start_session`` wires Entry -HAS_UPLOAD_SESSION-> Session."""
    from app.services import chunked_upload

    # Enable feature flag for this test.
    monkeypatch.setattr(chunked_upload.settings, "CHUNKED_UPLOAD_ENABLED", True)
    owner, _track, entry = await _upload_session_setup_entry()

    sess = await chunked_upload.start_session(
        entry=entry,
        user_id=owner.id,
        filename="x.bin",
        mime_type="application/octet-stream",
        total_bytes=1024,
    )
    assert sess.id

    linked = await entry.nodes(edge=[HAS_UPLOAD_SESSION], node=["UploadSession"])
    linked_ids = {n.id for n in linked}
    assert (
        sess.id in linked_ids
    ), f"start_session did not wire HAS_UPLOAD_SESSION; neighbors={linked_ids}"


@pytest.mark.asyncio
async def test_backfill_upload_session_edges_repairs_orphan():
    from scripts.backfill_upload_session_edges import backfill_upload_session_edges

    _owner, _track, entry = await _upload_session_setup_entry()
    bare = await UploadSession.create(
        entry_id=entry.id,
        uploaded_by="u",
        filename="x.bin",
        mime_type="application/octet-stream",
        total_bytes=512,
        chunk_size=256,
        status="pending",
    )
    assert bare.id

    stats = await backfill_upload_session_edges(dry_run=False)
    assert stats["wired"] >= 1

    linked = await entry.nodes(edge=[HAS_UPLOAD_SESSION], node=["UploadSession"])
    linked_ids = {n.id for n in linked}
    assert bare.id in linked_ids


@pytest.mark.asyncio
async def test_backfill_upload_session_edges_skips_terminal():
    """Sessions in {complete, expired, cancelled} are skipped (will be deleted soon)."""
    from scripts.backfill_upload_session_edges import backfill_upload_session_edges

    _owner, _track, entry = await _upload_session_setup_entry()
    await UploadSession.create(
        entry_id=entry.id,
        uploaded_by="u",
        filename="done.bin",
        mime_type="application/octet-stream",
        total_bytes=128,
        chunk_size=128,
        status="complete",
    )
    stats = await backfill_upload_session_edges(dry_run=False)
    assert stats["skipped"] >= 1


# ===== CONFLICT =====


async def _conflict_setup_entry() -> Entry:
    from datetime import datetime

    from app.models.edges import CONTAINS

    now = datetime.now().isoformat()
    owner = await User.create(
        user_id="u_conflict_test", display_name="O", created_at=now, updated_at=now
    )
    await catalog_user(owner)
    workspace = await ensure_personal_workspace(owner)
    track = await Track.create(
        title="T",
        workspace_id=workspace.id,
        owner_id=owner.id,
        created_at=now,
        updated_at=now,
    )
    await catalog_track(track)
    et = await EntryType.create(name="P", track_id=track.id, is_template=False)
    entry = await Entry.create(
        title="E",
        type_id=et.id,
        track_id=track.id,
        author_id=owner.id,
        status="active",
        created_at=now,
        updated_at=now,
    )
    await track.connect(entry, edge=CONTAINS, added_at=now)
    return entry


@pytest.mark.asyncio
async def test_create_conflict_record_wires_has_conflict():
    """``create_conflict_record`` wires Entry -HAS_CONFLICT-> Conflict."""
    entry = await _conflict_setup_entry()
    conflict = await create_conflict_record(
        connector_id="conn_test",
        entry=entry,
        external_snapshot={"title": "from external"},
    )
    assert conflict.id
    linked = await entry.nodes(edge=[HAS_CONFLICT], node=["Conflict"])
    linked_ids = {n.id for n in linked}
    assert conflict.id in linked_ids


@pytest.mark.asyncio
async def test_backfill_conflict_edges_repairs_orphan():
    from scripts.backfill_conflict_edges import backfill_conflict_edges

    entry = await _conflict_setup_entry()
    bare = Conflict(connector_id="c1", entry_id=entry.id, status="open")
    await bare.save()

    stats = await backfill_conflict_edges(dry_run=False)
    assert stats["wired"] >= 1
    linked = await entry.nodes(edge=[HAS_CONFLICT], node=["Conflict"])
    assert bare.id in {n.id for n in linked}


@pytest.mark.asyncio
async def test_backfill_conflict_edges_idempotent():
    from scripts.backfill_conflict_edges import backfill_conflict_edges

    entry = await _conflict_setup_entry()
    bare = Conflict(connector_id="c1", entry_id=entry.id, status="open")
    await bare.save()

    await backfill_conflict_edges(dry_run=False)
    s2 = await backfill_conflict_edges(dry_run=False)
    assert s2["wired"] == 0


# ===== APPROVAL =====


@pytest.mark.asyncio
async def test_policy_has_approval_edge_wires_via_backfill():
    """Direct unit test of backfill (the create path is exercised via the
    full policy_engine.evaluate flow which is heavily integration-shaped;
    backfill covers the same wire idempotently for the unit case)."""
    from scripts.backfill_approval_edges import backfill_approval_edges

    # Build a Policy and a bare pending Approval pointing at it.
    pol = await Policy.create(
        subject_kind="agent",
        subject_id="agt_test",
        scope="*",
        actions=["entry.create"],
        is_active=True,
    )
    ap = await Approval.create(
        actor_kind="agent",
        actor_id="agt_test",
        action="entry.create",
        resource_kind="entry",
        resource_id="",
        policy_id=pol.id,
        status="pending",
        created_at="2026-05-20T00:00:00+00:00",
        expires_at="2026-05-27T00:00:00+00:00",
    )
    stats = await backfill_approval_edges(dry_run=False)
    assert stats["approval_wired"] >= 1

    linked = await pol.nodes(edge=[HAS_APPROVAL], node=["Approval"])
    assert ap.id in {n.id for n in linked}


@pytest.mark.asyncio
async def test_user_has_approval_decision_wired_via_backfill():
    """Approved Approval with decider_id should wire User -HAS_APPROVAL_DECISION-> Approval."""
    from datetime import datetime

    from scripts.backfill_approval_edges import backfill_approval_edges

    decider = await User.create(
        user_id="u_decider", display_name="D", created_at=datetime.now().isoformat()
    )
    await catalog_user(decider)
    pol = await Policy.create(
        subject_kind="agent",
        subject_id="agt_d",
        scope="*",
        actions=["entry.create"],
        is_active=True,
    )
    ap = await Approval.create(
        actor_kind="agent",
        actor_id="agt_d",
        action="entry.create",
        resource_kind="entry",
        resource_id="",
        policy_id=pol.id,
        status="approved",
        created_at="2026-05-20T00:00:00+00:00",
        expires_at="2026-05-27T00:00:00+00:00",
        decided_at="2026-05-20T01:00:00+00:00",
        decider_id=decider.id,
    )

    await backfill_approval_edges(dry_run=False)

    linked = await decider.nodes(edge=[HAS_APPROVAL_DECISION], node=["Approval"])
    assert ap.id in {n.id for n in linked}


@pytest.mark.asyncio
async def test_backfill_approval_edges_idempotent():
    from scripts.backfill_approval_edges import backfill_approval_edges

    pol = await Policy.create(
        subject_kind="agent",
        subject_id="agt_i",
        scope="*",
        actions=["entry.create"],
        is_active=True,
    )
    await Approval.create(
        actor_kind="agent",
        actor_id="agt_i",
        action="entry.create",
        resource_kind="entry",
        resource_id="",
        policy_id=pol.id,
        status="pending",
        created_at="2026-05-20T00:00:00+00:00",
        expires_at="2026-05-27T00:00:00+00:00",
    )
    await backfill_approval_edges(dry_run=False)
    s2 = await backfill_approval_edges(dry_run=False)
    assert s2["approval_wired"] == 0


# ===== AGENT_CONFIG =====


async def _agent_config_user(uid: str) -> User:
    now = datetime.now().isoformat()
    u = await User.create(user_id=uid, display_name="U", created_at=now, updated_at=now)
    await catalog_user(u)
    return u


@pytest.mark.asyncio
async def test_personal_agent_wires_has_agent_config():
    user = await _agent_config_user("u_personal_agent")
    cfg = await AgentConfig.create(
        user_id=user.id, scope="personal", agent_type="jvagent", is_active=True
    )
    wired = await wire_agent_config_attachment_edge(cfg)
    assert wired is True

    linked = await user.nodes(edge=[HAS_AGENT_CONFIG], node=["AgentConfig"])
    assert cfg.id in {n.id for n in linked}


@pytest.mark.asyncio
async def test_org_facing_agent_wires_has_org_agent():
    user = await _agent_config_user("u_org_agent")
    workspace = await ensure_personal_workspace(user)
    cfg = await AgentConfig.create(
        user_id="",
        scope="org_facing",
        agent_type="jvagent",
        workspace_id=workspace.id,
        is_active=True,
    )
    wired = await wire_agent_config_attachment_edge(cfg)
    assert wired is True

    linked = await workspace.nodes(edge=[HAS_ORG_AGENT], node=["AgentConfig"])
    assert cfg.id in {n.id for n in linked}


@pytest.mark.asyncio
async def test_system_agent_wires_has_system_agent():
    cfg = await AgentConfig.create(
        user_id="", scope="system", agent_type="jvagent", is_active=True
    )
    wired = await wire_agent_config_attachment_edge(cfg)
    assert wired is True

    integral_app = await IntegralApp.get(APP_NODE_ID)
    assert integral_app is not None
    linked = await integral_app.nodes(edge=[HAS_SYSTEM_AGENT], node=["AgentConfig"])
    assert cfg.id in {n.id for n in linked}


@pytest.mark.asyncio
async def test_app_bundled_agent_wires_contains():
    user = await _agent_config_user("u_app_bundled")
    workspace = await ensure_personal_workspace(user)
    app = await App.create(
        name="A",
        workspace_id=workspace.id,
        owner_user_id=user.id,
        created_at=datetime.now().isoformat(),
    )
    await catalog_app(app)

    cfg = await AgentConfig.create(
        user_id="",
        scope="org_facing",  # any scope; app_id takes precedence
        agent_type="jvagent",
        workspace_id=workspace.id,
        app_id=app.id,
        is_active=True,
    )
    wired = await wire_agent_config_attachment_edge(cfg)
    assert wired is True

    linked = await app.nodes(edge=[CONTAINS], node=["AgentConfig"])
    assert cfg.id in {n.id for n in linked}


@pytest.mark.asyncio
async def test_ensure_helper_idempotent():
    user = await _agent_config_user("u_agent_idem")
    cfg = await AgentConfig.create(
        user_id=user.id, scope="personal", agent_type="jvagent", is_active=True
    )
    assert (await wire_agent_config_attachment_edge(cfg)) is True
    assert (await wire_agent_config_attachment_edge(cfg)) is False  # second pass: no-op


@pytest.mark.asyncio
async def test_backfill_agent_config_edges_repairs_orphan():
    from scripts.backfill_agent_config_edges import backfill_agent_config_edges

    user = await _agent_config_user("u_backfill_agent")
    cfg = await AgentConfig.create(
        user_id=user.id, scope="personal", agent_type="jvagent", is_active=True
    )
    # Bare row — no edge wired.
    stats = await backfill_agent_config_edges(dry_run=False)
    assert stats["wired"] >= 1

    linked = await user.nodes(edge=[HAS_AGENT_CONFIG], node=["AgentConfig"])
    assert cfg.id in {n.id for n in linked}


# ===== CONVERSATION_CONTEXT =====


async def _conversation_context_user(uid: str) -> User:
    now = datetime.now().isoformat()
    u = await User.create(user_id=uid, display_name="U", created_at=now, updated_at=now)
    await catalog_user(u)
    return u


async def _conversation_context_agent_for(user: User) -> AgentConfig:
    cfg = await AgentConfig.create(
        user_id=user.id, scope="personal", agent_type="jvagent", is_active=True
    )
    await wire_agent_config_attachment_edge(cfg)
    return cfg


@pytest.mark.asyncio
async def test_context_wires_under_agent_config_when_resolvable():
    """When agent_config_id resolves, the context hangs under the AgentConfig."""
    user = await _conversation_context_user("u_ctx_agent")
    cfg = await _conversation_context_agent_for(user)

    ctx = await get_or_create_conversation_context(
        user_id=user.id,
        agent_type="jvagent",
        agent_conversation_id="conv1",
        agent_config_id=cfg.id,
    )
    assert ctx.id

    linked = await cfg.nodes(edge=[CONTAINS], node=["ConversationContext"])
    assert ctx.id in {n.id for n in linked}


@pytest.mark.asyncio
async def test_context_falls_back_to_user_when_no_agent_config():
    """When no agent_config_id, the context falls back to the User parent."""
    user = await _conversation_context_user("u_ctx_user_only")

    ctx = await get_or_create_conversation_context(
        user_id=user.id,
        agent_type="claude_code",
        agent_conversation_id="conv2",
    )
    assert ctx.id

    linked = await user.nodes(edge=[CONTAINS], node=["ConversationContext"])
    assert ctx.id in {n.id for n in linked}


@pytest.mark.asyncio
async def test_backfill_conversation_context_edges_repairs_orphan():
    from scripts.backfill_conversation_context_edges import (
        backfill_conversation_context_edges,
    )

    user = await _conversation_context_user("u_ctx_backfill")
    cfg = await _conversation_context_agent_for(user)
    bare = await ConversationContext.create(
        agent_type="jvagent",
        agent_conversation_id="bare1",
        user_id=user.id,
        agent_config_id=cfg.id,
    )
    assert bare.id

    stats = await backfill_conversation_context_edges(dry_run=False)
    assert stats["wired"] >= 1

    linked = await cfg.nodes(edge=[CONTAINS], node=["ConversationContext"])
    assert bare.id in {n.id for n in linked}


@pytest.mark.asyncio
async def test_backfill_conversation_context_edges_idempotent():
    from scripts.backfill_conversation_context_edges import (
        backfill_conversation_context_edges,
    )

    user = await _conversation_context_user("u_ctx_idem")
    cfg = await _conversation_context_agent_for(user)
    await get_or_create_conversation_context(
        user_id=user.id,
        agent_type="jvagent",
        agent_conversation_id="idem1",
        agent_config_id=cfg.id,
    )
    await backfill_conversation_context_edges(dry_run=False)
    s2 = await backfill_conversation_context_edges(dry_run=False)
    assert s2["wired"] == 0


# ===== CHANNEL_IDENTITY =====


async def _channel_identity_make_user(uid: str) -> User:
    now = datetime.now().isoformat()
    u = await User.create(user_id=uid, display_name="U", created_at=now, updated_at=now)
    await catalog_user(u)
    return u


@pytest.mark.asyncio
async def test_backfill_channel_identity_edges_repairs_orphan():
    from scripts.backfill_channel_identity_edges import (
        backfill_channel_identity_edges,
    )

    user = await _channel_identity_make_user("u_ci_test")
    bare = await ChannelIdentity.create(
        user_id=user.id,
        channel="email",
        channel_user_id="test@example.com",
    )
    assert bare.id

    stats = await backfill_channel_identity_edges(dry_run=False)
    assert stats["wired"] >= 1

    linked = await user.nodes(edge=[HAS_CHANNEL_IDENTITY], node=["ChannelIdentity"])
    assert bare.id in {n.id for n in linked}


@pytest.mark.asyncio
async def test_backfill_channel_identity_edges_idempotent():
    from scripts.backfill_channel_identity_edges import (
        backfill_channel_identity_edges,
    )

    user = await _channel_identity_make_user("u_ci_idem")
    await ChannelIdentity.create(
        user_id=user.id, channel="email", channel_user_id="x@y.z"
    )
    await backfill_channel_identity_edges(dry_run=False)
    s2 = await backfill_channel_identity_edges(dry_run=False)
    assert s2["wired"] == 0


@pytest.mark.asyncio
async def test_initiate_link_wires_edge(monkeypatch):
    """``services.channel_identity.initiate_link`` was already wiring
    HAS_CHANNEL_IDENTITY pre-Plan-10.5-08 — confirm it still does so we
    don't regress while draining the allowlist."""
    from app.agentive.services.channel_identity import initiate_link

    user = await _channel_identity_make_user("u_ci_init")
    res = await initiate_link(
        user_id=user.id,
        channel="email",
        channel_user_id="alpha@beta.com",
    )
    assert res["identity_id"]

    linked = await user.nodes(edge=[HAS_CHANNEL_IDENTITY], node=["ChannelIdentity"])
    linked_ids = {n.id for n in linked}
    assert res["identity_id"] in linked_ids


# ===== GOVERNANCE_POLICY =====


async def _governance_policy_cp_with_anchor_field() -> ContentProfile:
    """ContentProfile manifest with one relation field targeting track."""
    now = datetime.now().isoformat()
    return await ContentProfile.create(
        name="Profile",
        version="1.0.0",
        manifest={
            "content_profile_schema_version": 2,
            "scope": "track",
            "track": {
                "entry_types": [
                    {
                        "key": "project",
                        "name": "Project",
                        "fields": [
                            {
                                "key": "details",
                                "type": "relation",
                                "relation": {
                                    "target": "track",
                                    "governance": {"cascade": "hard"},
                                },
                            }
                        ],
                    }
                ],
                "views": [],
                "taxonomy": {"tag_groups": []},
            },
            "package": {},
            "migrations": [],
        },
        scope="track",
        library_package=False,
        status="published",
        created_at=now,
        updated_at=now,
        version_number=1,
    )


@pytest.mark.asyncio
async def test_governance_policy_wires_has_governance_policy_edge():
    """``materialize_governance_policies_for_content_profile`` wires the edge."""
    cp = await _governance_policy_cp_with_anchor_field()

    policies = await materialize_governance_policies_for_content_profile(
        cp.id, cp.manifest
    )
    assert len(policies) >= 1

    linked = await cp.nodes(edge=[HAS_GOVERNANCE_POLICY], node=["Policy"])
    linked_ids = {n.id for n in linked}
    for pol in policies:
        assert pol.id in linked_ids, (
            f"governance Policy {pol.id} not wired via HAS_GOVERNANCE_POLICY; "
            f"linked={linked_ids}"
        )


@pytest.mark.asyncio
async def test_governance_policy_subject_kind_is_system():
    """Sanity check — these policies remain ``subject_kind="system"``;
    HAS_GOVERNANCE_POLICY is the ONLY graph-attachment route."""
    cp = await _governance_policy_cp_with_anchor_field()
    policies = await materialize_governance_policies_for_content_profile(
        cp.id, cp.manifest
    )
    for pol in policies:
        assert pol.subject_kind == "system"
        assert pol.subject_id == f"governance:{cp.id}"


# ===== CONTENT_PROFILE_DRAFT =====


async def _content_profile_draft_published_cp() -> ContentProfile:
    from datetime import datetime

    now = datetime.now().isoformat()
    return await ContentProfile.create(
        name="Profile",
        version="1.0.0",
        manifest={
            "content_profile_schema_version": 2,
            "scope": "track",
            "track": {"entry_types": [], "views": [], "taxonomy": {"tag_groups": []}},
            "package": {},
            "migrations": [],
        },
        scope="track",
        library_package=False,
        status="published",
        created_at=now,
        updated_at=now,
        version_number=1,
    )


@pytest.mark.asyncio
async def test_fork_draft_wires_has_draft_profile():
    """``fork_draft`` wires published -HAS_DRAFT_PROFILE-> draft."""
    pub = await _content_profile_draft_published_cp()
    draft = await fork_draft(published=pub, actor_id="agent1")
    assert draft.id and draft.status == "draft"

    linked = await pub.nodes(edge=[HAS_DRAFT_PROFILE], node=["ContentProfile"])
    linked_ids = {n.id for n in linked}
    assert (
        draft.id in linked_ids
    ), f"fork_draft did not wire HAS_DRAFT_PROFILE; neighbors={linked_ids}"


@pytest.mark.asyncio
async def test_discard_draft_cascade_deletes_edge():
    """``discard_draft`` removes the draft node — edge cascades."""
    pub = await _content_profile_draft_published_cp()
    draft = await fork_draft(published=pub)
    draft_id = draft.id

    res = await discard_draft(draft=draft)
    assert res["discarded"] is True

    # Draft node gone; edge cascades on delete.
    assert await ContentProfile.get(draft_id) is None
    linked = await pub.nodes(edge=[HAS_DRAFT_PROFILE], node=["ContentProfile"])
    linked_ids = {n.id for n in linked}
    assert draft_id not in linked_ids


@pytest.mark.asyncio
async def test_backfill_content_profile_draft_edges_repairs_orphan():
    """Bare draft (no edge) is wired by the backfill."""
    from scripts.backfill_content_profile_draft_edges import (
        backfill_content_profile_draft_edges,
    )

    pub = await _content_profile_draft_published_cp()
    bare_draft = await ContentProfile.create(
        name="Bare Draft",
        version=pub.version,
        manifest=dict(pub.manifest or {}),
        scope=pub.scope,
        library_package=False,
        status="draft",
        draft_of_id=pub.id,
        parent_version_id=pub.id,
        version_number=int(pub.version_number or 1) + 1,
    )
    assert bare_draft.id

    stats = await backfill_content_profile_draft_edges(dry_run=False)
    assert stats["wired"] >= 1

    linked = await pub.nodes(edge=[HAS_DRAFT_PROFILE], node=["ContentProfile"])
    linked_ids = {n.id for n in linked}
    assert bare_draft.id in linked_ids


@pytest.mark.asyncio
async def test_backfill_content_profile_draft_edges_idempotent():
    from scripts.backfill_content_profile_draft_edges import (
        backfill_content_profile_draft_edges,
    )

    pub = await _content_profile_draft_published_cp()
    await fork_draft(published=pub)
    await backfill_content_profile_draft_edges(dry_run=False)
    s2 = await backfill_content_profile_draft_edges(dry_run=False)
    assert s2["wired"] == 0
