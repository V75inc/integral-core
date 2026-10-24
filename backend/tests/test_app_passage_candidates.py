"""App search identity, graph ownership and candidate budgets on real graphs."""

import pytest

from app.models.edges import CONTAINS, HAS_OPERATIONAL_MODEL, IS_OF_TYPE, OWNS
from app.models.nodes import Entry, EntryType, OperationalModel, Track
from app.services.app_graph import catalog_app, catalog_track
from app.services.hooks.registry import ToolContext
from app.services.retrieval.app_search import _candidate_ids, _SearchUnavailable
from tests.test_resource_location import graph

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


def context(user, workspace, app):
    ctx = ToolContext(user_id=user.id, workspace_id=workspace.id, scope="tool")
    ctx.app_id = app.id
    return ctx


async def add_track(user, workspace, app, **kwargs):
    track = await Track.create(workspace_id=workspace.id, **kwargs)
    await catalog_track(track)
    await app.connect(track, edge=CONTAINS)
    await user.connect(track, edge=OWNS)
    return track


async def add_entry(track, **kwargs):
    entry = await Entry.create(track_id=track.id, **kwargs)
    await track.connect(entry, edge=CONTAINS)
    return entry


@pytest.mark.parametrize("key", ["contacts", "contact-book"])
async def test_stable_key_survives_rename_and_ignores_colliding_display_title(key):
    user, workspace, app, track, entry = await graph()
    track.template_id = key
    track.title = "Renamed address book"
    await track.save()
    other = await add_track(
        user, workspace, app, title="Contacts", template_id="archive"
    )
    await add_entry(other, title="Wrong Track")
    ctx = context(user, workspace, app)
    assert await _candidate_ids(ctx, key, None, None) == ([entry.id], False)
    assert await _candidate_ids(ctx, track.id, None, [entry.id]) == ([entry.id], False)
    with pytest.raises(_SearchUnavailable, match="track_not_found"):
        await _candidate_ids(ctx, "renamed_address_book", None, None)


async def test_legacy_titles_are_only_a_fallback_and_ambiguity_fails_closed():
    user, workspace, app, track, entry = await graph()
    track.title = "Address book"
    await track.save()
    ctx = context(user, workspace, app)
    assert await _candidate_ids(ctx, "address_book", None, None) == ([entry.id], False)
    await add_track(user, workspace, app, title="Address-book")
    with pytest.raises(_SearchUnavailable, match="ambiguous_track"):
        await _candidate_ids(ctx, "address_book", None, None)
    assert await _candidate_ids(ctx, track.id, None, None) == ([entry.id], False)


async def test_stable_ad_hoc_identity_is_independent_of_display_title():
    user, workspace, app, track, entry = await graph()
    track.relation_type_key = "Address Book"
    track.title = "New name"
    await track.save()
    assert await _candidate_ids(
        context(user, workspace, app), "Address Book", None, None
    ) == ([entry.id], False)


async def test_distinct_declared_keys_are_not_combined_by_slug_normalization():
    user, workspace, app, track, entry = await graph()
    track.template_id = "address-book"
    await track.save()
    other = await add_track(
        user, workspace, app, title="Same display name", template_id="address_book"
    )
    other_entry = await add_entry(other)
    ctx = context(user, workspace, app)
    assert await _candidate_ids(ctx, "address-book", None, None) == ([entry.id], False)
    assert await _candidate_ids(ctx, "address_book", None, None) == (
        [other_entry.id],
        False,
    )


@pytest.mark.postgres
async def test_narrow_search_works_with_more_than_500_unrelated_tracks(monkeypatch):
    user, workspace, app, track, entry = await graph()
    track.template_id = "contacts"
    await track.save()
    for index in range(501):
        await add_track(
            user, workspace, app, title=f"Other {index}", template_id=f"other_{index}"
        )
    ctx = context(user, workspace, app)
    seen = []
    original = app.__class__.nodes_page

    async def page(self, **kwargs):
        seen.append(kwargs["node"])
        return await original(self, **kwargs)

    monkeypatch.setattr(app.__class__, "nodes_page", page)
    assert await _candidate_ids(ctx, "contacts", None, [entry.id]) == (
        [entry.id],
        False,
    )
    assert track.id in str(seen[0])
    assert await _candidate_ids(ctx, "contacts", None, None) == ([entry.id], False)


@pytest.mark.parametrize("model_rooted", [False, True])
async def test_entry_type_filter_precedes_budget_and_ignores_unrelated_schemas(
    model_rooted, monkeypatch
):
    from app.services.walkers import app_passage_candidates

    user, workspace, app, track, entry = await graph()
    track.template_id = "contacts"
    await track.save()
    parent = track
    if model_rooted:
        models = await track.nodes(
            edge=[HAS_OPERATIONAL_MODEL], node=[OperationalModel], limit=2
        )
        if models:
            parent = models[0]
        else:
            parent = await OperationalModel.create(name="Contacts schema")
            await track.connect(parent, edge=HAS_OPERATIONAL_MODEL)
    for index in range(3):
        unrelated = await EntryType.create(
            name=f"Other {index}",
            track_id=track.id,
            form_schema=(
                {"_manifest_entry_type_key": "contact_card"} if index == 0 else {}
            ),
        )
        await parent.connect(unrelated, edge=CONTAINS)
        await add_entry(track, type_id=unrelated.id)
    selected = await EntryType.create(
        name="Renamed type",
        track_id=track.id,
        form_schema={"_manifest_entry_type_key": "contact-card"},
    )
    await parent.connect(selected, edge=CONTAINS)
    entry.type_id = selected.id
    await entry.save()
    await entry.connect(selected, edge=IS_OF_TYPE)
    monkeypatch.setattr(app_passage_candidates, "MAX_CANDIDATES", 2)
    assert await _candidate_ids(
        context(user, workspace, app), "contacts", "contact-card", None
    ) == ([entry.id], False)


async def test_matching_entry_budget_is_honest(monkeypatch):
    from app.services.walkers import app_passage_candidates

    user, workspace, app, track, entry = await graph()
    track.template_id = "contacts"
    await track.save()
    await add_entry(track)
    await add_entry(track)
    monkeypatch.setattr(app_passage_candidates, "MAX_CANDIDATES", 2)
    ids, capped = await _candidate_ids(
        context(user, workspace, app), "contacts", None, None
    )
    assert len(ids) == 2 and capped


async def test_explicit_selection_does_not_leave_owning_app():
    user, workspace, app, track, entry = await graph()
    from app.models.nodes import App

    other = await App.create(name="Other", workspace_id=workspace.id)
    await catalog_app(other)
    await user.connect(other, edge=OWNS)
    outside = await add_track(
        user, workspace, other, title="Contacts", template_id="contacts"
    )
    foreign = await add_entry(outside)
    ctx = context(user, workspace, app)
    assert await _candidate_ids(ctx, outside.id, None, [foreign.id]) == ([], False)


async def test_walker_hook_failure_never_releases_a_partial_selection(monkeypatch):
    user, workspace, app, track, entry = await graph()
    track.template_id = "contacts"
    await track.save()
    original = Track.nodes_page

    async def failed_page(self, **kwargs):
        if self.id == track.id:
            raise RuntimeError("synthetic storage failure")
        return await original(self, **kwargs)

    monkeypatch.setattr(Track, "nodes_page", failed_page)
    with pytest.raises(_SearchUnavailable, match="selection_failed"):
        await _candidate_ids(context(user, workspace, app), "contacts", None, None)


async def test_unreadable_legacy_title_does_not_disclose_ambiguity():
    from app.models.edges import EXCLUDED_FROM

    user, workspace, app, track, entry = await graph()
    track.title = "Address Book"
    await track.save()
    private = await Track.create(title="Address-book", workspace_id=workspace.id)
    await catalog_track(private)
    await app.connect(private, edge=CONTAINS)
    await user.connect(private, edge=EXCLUDED_FROM)
    assert await _candidate_ids(
        context(user, workspace, app), "address_book", None, None
    ) == ([entry.id], False)


async def test_unreadable_stable_track_has_same_result_as_absent_track():
    from app.models.edges import EXCLUDED_FROM

    user, workspace, app, track, entry = await graph()
    private = await Track.create(
        title="Private", template_id="private-key", workspace_id=workspace.id
    )
    await catalog_track(private)
    await app.connect(private, edge=CONTAINS)
    await user.connect(private, edge=EXCLUDED_FROM)
    ctx = context(user, workspace, app)
    for selector in ("private-key", "absent-key", private.id):
        with pytest.raises(_SearchUnavailable, match="track_not_found"):
            await _candidate_ids(ctx, selector, None, None)
