"""Tests for Node classes."""

import pytest

from app.models.nodes import (
    App,
    Attachment,
    Comment,
    Entry,
    EntryType,
    Tag,
    Track,
    User,
    View,
)


@pytest.mark.asyncio
async def test_create_user_node():
    """Test creating a User."""
    user = await User.create(display_name="Test User")
    assert user.id is not None
    assert user.display_name == "Test User"


@pytest.mark.asyncio
async def test_create_space_node():
    """Test creating a App."""
    ts = await App.create(
        name="Q4 Initiatives",
        owner_user_id="user_123",
        description="All Q4 work",
    )
    assert ts.id is not None
    assert ts.name == "Q4 Initiatives"
    assert ts.owner_user_id == "user_123"


@pytest.mark.asyncio
async def test_create_track_node():
    """Test creating a Track with spec fields."""
    track = await Track.create(
        title="Test Track",
        owner_id="user_123",
        purpose="Testing the track node",
        icon="🧪",
        visibility="private",
    )
    assert track.id is not None
    assert track.title == "Test Track"
    assert track.owner_id == "user_123"
    assert track.purpose == "Testing the track node"
    assert track.icon == "🧪"
    assert track.visibility == "private"


@pytest.mark.asyncio
async def test_create_entry_node():
    """Test creating an Entry with spec fields."""
    entry = await Entry.create(
        type_id="et_123",
        title="Fix login bug",
        author_id="user_123",
        track_id="track_456",
        custom_fields={"severity": "high"},
    )
    assert entry.id is not None
    assert entry.type_id == "et_123"
    assert entry.title == "Fix login bug"
    assert entry.author_id == "user_123"
    assert entry.custom_fields["severity"] == "high"


@pytest.mark.asyncio
async def test_create_tag_node():
    """Test creating a Tag with track_id scope."""
    tag = await Tag.create(
        name="urgent",
        color="#EF4444",
        track_id="track_123",
    )
    assert tag.id is not None
    assert tag.name == "urgent"
    assert tag.color == "#EF4444"
    assert tag.track_id == "track_123"


@pytest.mark.asyncio
async def test_create_comment_node():
    """Test creating a Comment with text field."""
    comment = await Comment.create(
        author_id="user_123",
        text="This is a comment.",
    )
    assert comment.id is not None
    assert comment.text == "This is a comment."
    assert comment.author_id == "user_123"


@pytest.mark.asyncio
async def test_create_entry_type_node():
    """Test creating an EntryType with track_id scope."""
    et = await EntryType.create(
        name="Bug Report",
        icon="🐛",
        track_id="track_123",
        form_schema={"fields": []},
    )
    assert et.id is not None
    assert et.name == "Bug Report"
    assert et.track_id == "track_123"


@pytest.mark.asyncio
async def test_create_attachment_node():
    """Test creating an Attachment with uploaded_by field."""
    att = await Attachment.create(
        filename="screenshot.png",
        mime_type="image/png",
        size=1024,
        storage_key="attachments/entry/screenshot.png",
        uploaded_by="user_123",
    )
    assert att.id is not None
    assert att.filename == "screenshot.png"
    assert att.uploaded_by == "user_123"


@pytest.mark.asyncio
async def test_create_view_node():
    """Test creating a View."""
    view = await View.create(
        name="Dev Board",
        type="kanban",
        track_id="track_123",
        is_default=True,
        created_by="user_123",
    )
    assert view.id is not None
    assert view.name == "Dev Board"
    assert view.type == "kanban"
    assert view.track_id == "track_123"
    assert view.is_default is True


@pytest.mark.asyncio
async def test_node_export():
    """Test exporting a Node to dictionary."""
    user = await User.create(display_name="Export Test")
    exported = await user.export()
    assert isinstance(exported, dict)
    assert "context" in exported
    assert exported["context"]["display_name"] == "Export Test"
