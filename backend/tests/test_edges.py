"""Tests for Edge classes."""

import pytest

from app.models.edges import (
    CATALOGS,
    COLLABORATES_ON,
    CONTAINS,
    OWNS,
    TAGGED_WITH,
    USES_TEMPLATE,
)
from app.models.nodes import (
    App,
    Entry,
    Tag,
    Track,
    User,
    View,
)


@pytest.mark.asyncio
async def test_create_owns_edge():
    """Test OWNS edge from User to Track."""
    user = await User.create(user_id="test_user_1", display_name="Owner")
    track = await Track.create(title="Test Track", owner_id=user.id)

    edge = await user.connect(track, edge=OWNS, role="owner")

    assert edge is not None
    assert edge.source == user.id
    assert edge.target == track.id
    assert edge.role == "owner"


@pytest.mark.asyncio
async def test_owns_edge_space():
    """Test OWNS edge from User to App."""
    user = await User.create(user_id="ts_owner_1", display_name="Owner")
    ts = await App.create(name="My App", owner_user_id=user.id)

    edge = await user.connect(ts, edge=OWNS, role="owner")

    assert edge is not None
    assert edge.source == user.id
    assert edge.target == ts.id


@pytest.mark.asyncio
async def test_create_collaborates_on_track_edge():
    """Test COLLABORATES_ON edge from User to Track."""
    user = await User.create(user_id="test_user_2", display_name="Collaborator")
    track = await Track.create(title="Shared Track", owner_id="other_user")

    edge = await user.connect(track, edge=COLLABORATES_ON, role="editor")

    assert edge is not None
    assert edge.role == "editor"


@pytest.mark.asyncio
async def test_collaborates_on_space_edge():
    """Test COLLABORATES_ON edge from User to App."""
    user = await User.create(user_id="ts_collab_1", display_name="Collab")
    ts = await App.create(name="Shared App", owner_user_id="owner_123")

    edge = await user.connect(ts, edge=COLLABORATES_ON, role="viewer")

    assert edge is not None
    assert edge.role == "viewer"


@pytest.mark.asyncio
async def test_contains_track_entry_edge():
    """Test CONTAINS edge from Track to Entry."""
    track = await Track.create(title="Container Track", owner_id="user_123")
    entry = await Entry.create(type_id="", title="An entry", author_id="user_123")

    edge = await track.connect(entry, edge=CONTAINS)

    assert edge is not None
    assert edge.source == track.id
    assert edge.target == entry.id


@pytest.mark.asyncio
async def test_contains_space_track_edge():
    """Test CONTAINS edge from App to Track."""
    ts = await App.create(name="Big App", owner_user_id="user_123")
    track = await Track.create(title="Nested Track", owner_id="user_123")

    edge = await ts.connect(track, edge=CONTAINS)

    assert edge is not None
    assert edge.source == ts.id
    assert edge.target == track.id


@pytest.mark.asyncio
async def test_create_tagged_with_edge():
    """Test TAGGED_WITH edge from Entry to Tag."""
    entry = await Entry.create(type_id="", title="Tagged Entry", author_id="user_123")
    tag = await Tag.create(name="urgent", color="#EF4444", track_id="track_123")

    edge = await entry.connect(tag, edge=TAGGED_WITH, tagged_by="user_123")

    assert edge is not None
    assert edge.tagged_by == "user_123"


@pytest.mark.asyncio
async def test_track_views_registry_catalogs_view():
    """Views registry CATALOGS each View (Track → Views unnamed; Views → View CATALOGS)."""
    from jvspatial.core import Edge

    from app.models.nodes import Views
    from app.services.app_graph import (
        catalog_track,
        catalog_view_under_track,
        get_or_create_views_registry_for_operational_model,
        get_track_attached_operational_model,
    )

    track = await Track.create(title="View Track", owner_id="user_123")
    await catalog_track(track)
    view = await View.create(
        name="Kanban", type="kanban", track_id=track.id, created_by="user_123"
    )

    await catalog_view_under_track(track, view)
    cp = await get_track_attached_operational_model(track)
    vreg = await get_or_create_views_registry_for_operational_model(cp, track=track)
    structural = await cp.nodes(edge=[Edge], node=["Views"])
    assert len(structural) == 1
    assert isinstance(vreg, Views)
    linked = await vreg.nodes(edge=[CATALOGS], node=["View"])
    assert any(v.id == view.id for v in linked)


@pytest.mark.asyncio
async def test_uses_template_edge():
    """Test USES_TEMPLATE edge from Track to template Track."""
    template = await Track.create(title="Template Track", owner_id="user_123")
    derived = await Track.create(
        title="Derived Track", owner_id="user_123", template_id=template.id
    )

    edge = await derived.connect(template, edge=USES_TEMPLATE)

    assert edge is not None
    assert edge.source == derived.id
    assert edge.target == template.id


@pytest.mark.asyncio
async def test_query_edges():
    """Test querying connected nodes via edges."""
    user = await User.create(user_id="test_user_3", display_name="Query User")
    track1 = await Track.create(title="Track 1", owner_id=user.id)
    track2 = await Track.create(title="Track 2", owner_id=user.id)

    await user.connect(track1, edge=OWNS)
    await user.connect(track2, edge=OWNS)

    tracks = await user.nodes(edge=["OWNS"], node=["Track"])
    assert len(tracks) >= 2
