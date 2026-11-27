"""Exercise spoken list revisions, focus and the existing durable boundary."""

from unittest.mock import AsyncMock

import pytest

from app.agentive.tooling import catalogue, dispatch
from app.services import native_voice_navigation as nav


@pytest.mark.asyncio
async def test_navigation_pages_recheck_numbered_choice(monkeypatch):
    spec = catalogue.build_voice_quick_read_catalogue()["integral_list_apps"]
    first = {
        "apps": [{"id": "a", "name": "Same"}, {"id": "b", "name": "Same"}],
        "next_cursor": "opaque",
    }
    second = {"apps": [{"id": "c", "name": "Next"}]}
    rows = {None: first, "opaque": second}

    async def read(
        name, args, *, principal_id, scope, voice_read_offer, voice_read_cursor=None
    ):
        assert (principal_id, scope, voice_read_offer) == ("human", "workspace", spec)
        assert name == spec.name and args == {}
        return dispatch.ToolResult(data=rows[voice_read_cursor])

    monkeypatch.setattr(dispatch, "dispatch_tool", read)
    verify = AsyncMock(return_value=True)
    monkeypatch.setattr(nav, "verified_destination", verify)
    state = nav.VoiceNavigation()

    async def choose(action, ordinal=0):
        return await state.select(
            {"action": action, "ordinal": ordinal},
            principal="human",
            workspace="workspace",
        )

    assert (await choose("apps")).spoken_facts() == {
        "collection": "apps",
        "names": ("Same", "Same"),
        "next_page_available": True,
    }
    assert "Selected app Same" in await choose("select", 2)
    assert state.focus.id == "b"
    assert (await choose("next")).labels == ("Next",)
    assert (await choose("previous")).labels == ("Same", "Same")
    rows[None] = {"apps": list(reversed(first["apps"]))}
    assert "list changed" in await choose("select", 1)
    assert state.focus.id == "b"
    verify.return_value = False
    assert "No App or Track selected" in await choose("where")
    assert state.focus is None


@pytest.mark.asyncio
async def test_navigation_expired_list_and_scope_revocation(monkeypatch):
    spec = catalogue.build_voice_quick_read_catalogue()["integral_list_tracks"]
    target = nav.VoiceDestination("track", "track", "Tasks")
    page = nav.VoicePage(spec, (target,), ("Tasks",))
    state = nav.VoiceNavigation()
    state.accept_page(page)
    state.expires = 0
    verify = AsyncMock(return_value=False)
    monkeypatch.setattr(nav, "verified_destination", verify)
    read = AsyncMock(return_value=page)
    monkeypatch.setattr(nav, "read_page", read)
    args = {"principal": "human", "workspace": "workspace"}
    request = {"action": "select", "ordinal": 1}
    assert "no longer current" in await state.select(request, **args)
    read.assert_not_awaited()
    state.accept_page(page)
    assert "no longer available" in await state.select(request, **args)
    assert state.focus is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "ordinal",
        "named",
        "listed_name",
        "reordered",
        "revoked",
        "expired",
        "unspecified",
        "no_page",
    ],
)
async def test_open_voice_page_uses_bound_browser_and_last_spoken_choices(
    case, monkeypatch
):
    import json

    from app.services import native_voice
    from tests.test_native_voice import Browser, Provider
    from tests.test_native_voice import request as provider_request

    scope = native_voice.VoiceScope(
        "caller",
        "workspace",
        "",
        "thread",
        page_context=None if case == "no_page" else {"url": "/agent"},
    )
    relay = native_voice.VoiceRelay(Browser(), Provider(), scope, check=AsyncMock())
    first = nav.VoiceDestination("app", "alpha", "Field Notes")
    second = nav.VoiceDestination("app", "beta", "Reading Room")
    spec = catalogue.build_voice_quick_read_catalogue()["integral_list_apps"]
    page = nav.VoicePage(spec, (first, second), ("Field Notes", "Reading Room"))
    relay.navigation.accept_page(page)
    relay.last_spoken_page = page
    relay.navigation.focus = first
    if case == "expired":
        relay.navigation.expires = 0
    refresh = AsyncMock(
        return_value=(
            nav.VoicePage(spec, (second, first), ("Reading Room", "Field Notes"))
            if case == "reordered"
            else page
        )
    )
    monkeypatch.setattr(nav, "refresh_page", refresh)
    monkeypatch.setattr(
        nav, "verified_destination", AsyncMock(return_value=case != "revoked")
    )
    relay.ready = True
    relay.response_generation = "response"
    relay._speak_result = AsyncMock()
    relay._start_handoff = (
        AsyncMock()
    )  # isolate navigation routing; actual voice gate tested separately
    relay._work = AsyncMock()
    frame = provider_request("call", "response", "unused")
    destination = {
        "named": "Different app",
        "listed_name": "reading room",
        "unspecified": "",
    }.get(case, 2)
    frame["response"]["output"][0].update(
        name=native_voice.OPEN_PAGE, arguments=json.dumps({"destination": destination})
    )
    await relay._completed_response(frame)
    await relay.live_read
    if case in ("ordinal", "named", "listed_name"):
        await relay.pending
        assert relay._work.await_args.kwargs["open_only"] is True
        call = relay._work.await_args.args
        assert call[0] == "call"
        assert ("Reading Room" if case != "named" else "Different app") in call[1]
        assert call[3] == (second if case != "named" else None)
        assert relay.navigation.focus == first
        relay._speak_result.assert_not_awaited()
    else:
        relay._work.assert_not_awaited()
        assert relay._speak_result.await_args.kwargs["result"]
    if case in ("named", "expired", "unspecified", "no_page"):
        refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_saved_reply_paging_and_repeat_are_call_local():
    from app.services import native_voice
    from app.services.chat_threads import append_message, create_thread
    from tests.test_access_perimeter_regressions import rooted_graph
    from tests.test_native_voice import Browser, Provider

    owner, _, workspace, _, _, _ = await rooted_graph()
    thread = await create_thread(
        user_id=owner.id, workspace_id=workspace.id, provider_id="integral_native"
    )
    message = (
        "First section. [Garden notes](/apps/n.WorkspaceApp.123456789012345678901234). "
        + "alpha " * 95
        + " Second section. "
        + "beta " * 95
        + " Final section."
    )
    saved = await append_message(
        thread=thread,
        role="assistant",
        parts=[
            {"type": "text", "text": message},
            {"type": "reasoning", "text": "HIDDEN"},
        ],
    )
    relay = native_voice.VoiceRelay(
        Browser(),
        Provider(),
        native_voice.VoiceScope(owner.id, workspace.id, "", thread.id),
    )
    first = await relay._read_chat_content("latest_reply")
    assert "First section" in first and "Second section" not in first
    assert (
        "Garden notes" in first and "/apps/" not in first and "123456789" not in first
    )
    relay._function_output = AsyncMock()
    relay._speak_result = AsyncMock()
    await relay._read_live_tool(
        "first", native_voice.CHECK_CHAT, "latest_reply", relay.generation
    )
    assert "view more_reply" in relay._function_output.await_args.args[1]
    assert "First section" not in relay._function_output.await_args.args[1]
    assert relay._speak_result.await_args.kwargs["read_back"] is True
    first_cursor = relay.chat_read_cursor
    second = await relay._read_chat_content("more_reply")
    assert "Second section" in second and "HIDDEN" not in second
    assert relay.chat_read_cursor.start == first_cursor.end
    assert await relay._read_chat_content("repeat_reply") == second
    another = native_voice.VoiceRelay(Browser(), Provider(), relay.scope)
    assert "read the latest saved reply first" in await another._read_chat_content(
        "more_reply"
    )
    # A newer answer must not replace the one the caller just heard.
    from time import monotonic

    await append_message(
        thread=thread,
        role="assistant",
        parts=[{"type": "text", "text": "Newer unrelated reply."}],
    )
    another.last_spoken_reply = (saved.id, monotonic() + 180)
    first_detail = await another._read_chat_content("more_reply")
    assert "First section" in first_detail and "Newer unrelated" not in first_detail
    assert "Second section" in await another._read_chat_content("more_reply")
    another.chat_read_cursor = None
    another.last_spoken_reply = ("missing", monotonic() + 180)
    assert "couldn’t verify" in await another._read_chat_content("more_reply")


def test_directory_chat_copy_preserves_page_and_keeps_labels_literal():
    """A copied page has bounded ordered display names without identifiers or Markdown effects."""
    page = nav.VoicePage(
        None, (), ("Field [A]", "Budget *draft*"), next_cursor="private-cursor"
    )
    assert page.chat_text().splitlines() == [
        r"1. Field \[A\]",
        r"2. Budget \*draft\*",
        "",
        "More items are available on the next page.",
    ]
    assert nav.VoicePage(None, (), ()).chat_text() == "There are no items on this page."


def test_spoken_links_keep_labels_through_readout_and_truncated_peer_projection():
    from types import SimpleNamespace

    from app.services import native_voice

    route = "/tracks/n.Track.7095735a48be4bcd822066c4?entry=n.Entry.f12c3cd77c7347bbacdebeda"
    link = f"[QA Silver Lantern]({route})"
    assert (
        native_voice._speech_ready_result(f"One item: {link}.")
        == "One item: QA Silver Lantern."
    )
    for text in (link, link[:-8] + "…"):
        row = SimpleNamespace(
            source="typed", kind="message", role="assistant", text=text
        )
        assert native_voice.VoiceRelay._peer_readout(row) == "QA Silver Lantern"


def test_directory_search_only_needed_for_additional_installed_reads(monkeypatch):
    from app.agentive.tooling import catalogue
    from app.services.native_voice import FIND_CAPABILITY, NAVIGATE, session_update
    from app.services.native_voice_navigation import needs_directory_discovery

    reads = dict.fromkeys(
        ["integral_list_apps", "integral_list_tracks", "integral_list_workspaces"]
    )
    monkeypatch.setattr(catalogue, "build_voice_quick_read_catalogue", lambda: reads)
    session = session_update("", discover_directories=needs_directory_discovery())[
        "session"
    ]
    names = {tool["name"] for tool in session["tools"]}
    assert NAVIGATE in names and FIND_CAPABILITY not in names
    assert FIND_CAPABILITY not in session["instructions"]
    reads["additional_reviewed_directory"] = object()
    session = session_update("", discover_directories=needs_directory_discovery())[
        "session"
    ]
    assert FIND_CAPABILITY in {tool["name"] for tool in session["tools"]}
