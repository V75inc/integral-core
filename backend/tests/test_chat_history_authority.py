"""Current workspace admission applies to full and revision transcript reads."""

import pytest

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.smoke,
    pytest.mark.usefixtures("standalone_chat_provider"),
]


@pytest.mark.parametrize("provider", ["integral_native", "test-provider"])
async def test_revocation_denies_history_without_losing_it(
    authenticated_client, second_user_client, second_user, provider
):
    owner, member = authenticated_client, second_user_client
    created = await owner.post(
        "/api/workspaces",
        json={"name": "Chat history privacy", "workspace_type": "collaborative"},
    )
    assert created.status_code == 200, created.text
    wid = created.json()["workspace"]["id"]
    headers = {"X-Integral-Scope": "ws:" + wid}
    added = await owner.post(
        f"/api/workspaces/{wid}/members",
        json={"member_user_id": second_user.id, "role": "member"},
    )
    assert added.status_code == 200, added.text
    created = await member.post(
        "/api/chat/threads",
        headers=headers,
        json={
            "provider_id": provider,
            "agent_id": "aiva",
            "title": "Retained private history",
        },
    )
    assert created.status_code == 200, created.text
    tid = created.json()["id"]
    path = f"/api/chat/threads/{tid}"
    saved = await member.post(
        path + "/system-message",
        headers=headers,
        json={"text": "Synthetic workspace history retained across revocation"},
    )
    assert saved.status_code == 200, saved.text
    original = await member.get(path, headers=headers)
    assert original.status_code == 200, original.text
    original = original.json()
    # A workspace owner cannot read another user's conversation.
    assert (await owner.get(path, headers=headers)).status_code == 404
    removed = await owner.delete(f"/api/workspaces/{wid}/members/{second_user.id}")
    assert removed.status_code == 200, removed.text
    # Omitted headers and empty/matching revision parameters cannot bypass revoke.
    for scope in (headers, {}):
        for params in (
            {},
            {"known_revision": ""},
            {"known_revision": original["updated_at"]},
        ):
            refused = await member.get(path, headers=scope, params=params)
            assert refused.status_code == 403, (scope, params, refused.status_code)
            assert "Synthetic workspace history" not in refused.text
    restored = await owner.post(
        f"/api/workspaces/{wid}/members",
        json={"member_user_id": second_user.id, "role": "member"},
    )
    assert restored.status_code == 200, restored.text
    readback = await member.get(path, headers=headers)
    assert readback.status_code == 200, readback.text
    assert readback.json() == original
    # Scope mismatch must not serve an owned conversation from another scope.
    other = await member.post("/api/workspaces", json={"name": "Other chat scope"})
    assert other.status_code == 200, other.text
    mismatch = await member.get(
        path, headers={"X-Integral-Scope": "ws:" + other.json()["workspace"]["id"]}
    )
    assert mismatch.status_code == 409, mismatch.text
    unchanged = await member.get(
        path, headers=headers, params={"known_revision": original["updated_at"]}
    )
    assert unchanged.json() == {"unchanged": True}


async def test_legacy_unstamped_thread_recovers_canonical_personal_scope(
    authenticated_client,
):
    from app.models.nodes import ChatThread

    client = authenticated_client
    created = await client.post(
        "/api/chat/threads", json={"provider_id": "test-provider", "agent_id": "aiva"}
    )
    assert created.status_code == 200, created.text
    tid = created.json()["id"]
    # Representative retained pre-workspace-stamping row, in a disposable store.
    thread = await ChatThread.find_one({"id": tid})
    thread.workspace_id = ""
    await thread.save()
    response = await client.get(f"/api/chat/threads/{tid}")
    assert response.status_code == 200, response.text
    assert response.json()["id"] == tid


@pytest.mark.parametrize(
    ("provider", "unstamped"),
    [
        pytest.param("integral_native", False, id="integral_native"),
        pytest.param("test-provider", False, id="test-provider"),
        pytest.param("test-provider", True, id="legacy-unstamped"),
    ],
)
async def test_revocation_during_capture_withholds_the_transcript(
    authenticated_client,
    second_user_client,
    second_user,
    monkeypatch,
    provider,
    unstamped,
):
    from app.services import chat_threads

    owner, member = authenticated_client, second_user_client
    created = await owner.post(
        "/api/workspaces",
        json={"name": "Chat read admission", "workspace_type": "collaborative"},
    )
    assert created.status_code == 200, created.text
    wid = created.json()["workspace"]["id"]
    headers = {"X-Integral-Scope": "ws:" + wid}
    added = await owner.post(
        f"/api/workspaces/{wid}/members",
        json={"member_user_id": second_user.id, "role": "member"},
    )
    assert added.status_code == 200, added.text
    created = await member.post(
        "/api/chat/threads",
        headers=headers,
        json={"provider_id": provider, "agent_id": "aiva"},
    )
    assert created.status_code == 200, created.text
    tid = created.json()["id"]
    saved = await member.post(
        f"/api/chat/threads/{tid}/system-message",
        headers=headers,
        json={"text": "Withhold this captured private transcript"},
    )
    assert saved.status_code == 200, saved.text
    if unstamped:
        from app.models.nodes import ChatThread

        thread = await ChatThread.find_one({"id": tid})
        thread.workspace_id = ""
        await thread.save()
    original_list = chat_threads.list_messages

    async def capture_then_revoke(thread):
        messages = await original_list(thread)
        removed = await owner.delete(f"/api/workspaces/{wid}/members/{second_user.id}")
        assert removed.status_code == 200, removed.text
        return messages

    monkeypatch.setattr(chat_threads, "list_messages", capture_then_revoke)
    refused = await member.get(f"/api/chat/threads/{tid}", headers=headers)
    assert refused.status_code == 403, refused.text
    assert "Withhold this captured private transcript" not in refused.text


async def test_revision_changed_during_admission_returns_the_new_transcript(
    authenticated_client, monkeypatch
):
    from app.api import ai_chat

    client = authenticated_client
    created = await client.post(
        "/api/chat/threads", json={"provider_id": "test-provider", "agent_id": "aiva"}
    )
    assert created.status_code == 200, created.text
    path = f"/api/chat/threads/{created.json()['id']}"
    before = await client.get(path)
    assert before.status_code == 200, before.text
    revision = before.json()["updated_at"]
    original_admit = ai_chat._admit_chat_history_read
    changed = False

    async def admit_after_update(*args):
        nonlocal changed
        if not changed:
            changed = True
            saved = await client.post(
                path + "/system-message",
                json={"text": "Committed after the initial revision check"},
            )
            assert saved.status_code == 200, saved.text
        return await original_admit(*args)

    monkeypatch.setattr(ai_chat, "_admit_chat_history_read", admit_after_update)
    readback = await client.get(path, params={"known_revision": revision})
    assert readback.status_code == 200, readback.text
    payload = readback.json()
    assert not payload.get("unchanged")
    assert payload["updated_at"] != revision
    assert any(
        part.get("text") == "Committed after the initial revision check"
        for message in payload["messages"]
        for part in message["parts"]
    )


async def _rooted_member_history(owner, member, member_id, provider="test-provider"):
    from app.models.edges import CATALOGS
    from app.models.nodes import ChatThread

    created = await owner.post(
        "/api/workspaces",
        json={"name": "Canonical history location", "workspace_type": "collaborative"},
    )
    assert created.status_code == 200, created.text
    wid = created.json()["workspace"]["id"]
    headers = {"X-Integral-Scope": "ws:" + wid}
    added = await owner.post(
        f"/api/workspaces/{wid}/members",
        json={"member_user_id": member_id, "role": "member"},
    )
    assert added.status_code == 200, added.text
    created = await member.post(
        "/api/chat/threads",
        headers=headers,
        json={"provider_id": provider, "agent_id": "aiva"},
    )
    assert created.status_code == 200, created.text
    tid = created.json()["id"]
    saved = await member.post(
        f"/api/chat/threads/{tid}/system-message",
        headers=headers,
        json={"text": "Synthetic canonically scoped private history"},
    )
    assert saved.status_code == 200, saved.text
    thread = await ChatThread.find_one({"id": tid})
    branches = await thread.nodes(
        edge=[CATALOGS], node=["ChatThreads"], direction="in", limit=2
    )
    assert len(branches) == 1
    return wid, tid, headers, thread, branches[0], saved.json()["id"]


@pytest.mark.parametrize("provider", ["integral_native", "test-provider"])
@pytest.mark.parametrize("drop_caches", [False, True])
async def test_unstamped_rooted_history_cannot_be_rescoped_after_revocation(
    authenticated_client, second_user_client, second_user, provider, drop_caches
):
    from jvspatial.core.context import get_default_context

    owner, member = authenticated_client, second_user_client
    wid, tid, headers, thread, branch, _ = await _rooted_member_history(
        owner, member, second_user.id, provider
    )
    database = get_default_context().database
    if drop_caches:
        for node_id in (thread.id, branch.id):
            row = await database.get("node", node_id)
            row.pop("workspace_id", None)
            row["context"].pop("workspace_id", None)
            await database.save("node", row)
    else:
        thread.workspace_id = ""
        await thread.save()
    path = f"/api/chat/threads/{tid}"
    before = await member.get(path, headers=headers)
    assert before.status_code == 200, before.text
    original = before.json()
    personal = await member.get("/api/users/me/scope")
    assert personal.status_code == 200, personal.text
    personal_ids = [
        workspace["id"]
        for workspace in personal.json()["workspaces"]
        if workspace["kind"] == "personal"
    ]
    assert len(personal_ids) == 1
    personal_headers = {"X-Integral-Scope": "ws:" + personal_ids[0]}
    # A different, accessible scope must not recategorize the retained history.
    other = await member.post("/api/workspaces", json={"name": "Other history scope"})
    assert other.status_code == 200, other.text
    other_headers = {"X-Integral-Scope": "ws:" + other.json()["workspace"]["id"]}
    removed = await owner.delete(f"/api/workspaces/{wid}/members/{second_user.id}")
    assert removed.status_code == 200, removed.text
    for scope, expected in (
        ({}, 403),
        (headers, 403),
        (personal_headers, 409),
        (other_headers, 409),
    ):
        for params in (
            {},
            {"known_revision": ""},
            {"known_revision": original["updated_at"]},
        ):
            response = await member.get(path, headers=scope, params=params)
            assert response.status_code == expected, response.text
            assert "Synthetic canonically scoped private history" not in response.text
    restored = await owner.post(
        f"/api/workspaces/{wid}/members",
        json={"member_user_id": second_user.id, "role": "member"},
    )
    assert restored.status_code == 200, restored.text
    readback = await member.get(path)
    assert readback.status_code == 200, readback.text
    assert readback.json() == original
    # Read admission recovers location without rewriting either retained cache.
    for node_id in (thread.id, branch.id) if drop_caches else (thread.id,):
        row = await database.get("node", node_id)
        assert not row["context"].get("workspace_id")
        assert not row.get("workspace_id")


@pytest.mark.parametrize(
    "corruption",
    [
        "thread-cache",
        "registry-cache",
        "missing-catalog-parent",
        "missing-workspace-parent",
        "duplicate-catalog-parent",
        "duplicate-workspace-parent",
        "wrong-registry-type",
        "missing-workspace-catalog",
        "duplicate-workspace-root-parent",
    ],
)
async def test_history_location_conflicts_withhold_existing_data(
    authenticated_client, second_user_client, second_user, corruption
):
    from app.models.edges import CATALOGS, CONTAINS
    from app.models.nodes import WORKSPACES_REGISTRY_ID, ChatMessage, Workspace

    owner, member = authenticated_client, second_user_client
    wid, tid, headers, thread, branch, message_id = await _rooted_member_history(
        owner, member, second_user.id
    )
    other = await member.post(
        "/api/workspaces", json={"name": "Conflicting history scope"}
    )
    assert other.status_code == 200, other.text
    other_ws = await Workspace.find_one({"id": other.json()["workspace"]["id"]})
    other_branches = await other_ws.nodes(
        edge=[CONTAINS], node=["ChatThreads"], direction="out", limit=2
    )
    assert len(other_branches) == 1
    context = await thread.get_context()
    if corruption == "thread-cache":
        thread.workspace_id = other_ws.id
        await thread.save()
    elif corruption == "registry-cache":
        branch.workspace_id = other_ws.id
        await branch.save()
    elif corruption in {"missing-catalog-parent", "wrong-registry-type"}:
        edges = await context.find_edges_between(branch.id, tid, edge_class=CATALOGS)
        assert len(edges) == 1
        await edges[0].delete()
        if corruption == "wrong-registry-type":
            await other_ws.connect(thread, edge=CATALOGS)
    elif corruption == "missing-workspace-parent":
        edges = await context.find_edges_between(wid, branch.id, edge_class=CONTAINS)
        assert len(edges) == 1
        await edges[0].delete()
    elif corruption == "duplicate-catalog-parent":
        await other_branches[0].connect(thread, edge=CATALOGS)
    elif corruption == "duplicate-workspace-parent":
        await other_ws.connect(branch, edge=CONTAINS)
    elif corruption == "missing-workspace-catalog":
        edges = await context.find_edges_between(
            WORKSPACES_REGISTRY_ID, wid, edge_class=CATALOGS
        )
        assert len(edges) == 1
        await edges[0].delete()
    else:
        workspace = await Workspace.find_one({"id": wid})
        await other_ws.connect(workspace, edge=CONTAINS)
    response = await member.get(f"/api/chat/threads/{tid}")
    assert response.status_code == 503, response.text
    assert "Synthetic canonically scoped private history" not in response.text
    retained = await ChatMessage.find_one({"id": message_id})
    assert retained is not None
    assert retained.parts == [
        {"type": "text", "text": "Synthetic canonically scoped private history"}
    ]
