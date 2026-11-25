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
        "First section. "
        + "alpha " * 95
        + " Second section. "
        + "beta " * 95
        + " Final section."
    )
    await append_message(
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
    first_cursor = relay.chat_read_cursor
    second = await relay._read_chat_content("more_reply")
    assert "Second section" in second and "HIDDEN" not in second
    assert relay.chat_read_cursor[1] == first_cursor[2]
    assert await relay._read_chat_content("repeat_reply") == second
    another = native_voice.VoiceRelay(Browser(), Provider(), relay.scope)
    assert "read the latest saved reply first" in await another._read_chat_content(
        "more_reply"
    )


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
