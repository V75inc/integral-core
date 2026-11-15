"""Cross-App declarations and target content share one permission-aware reader."""

from types import SimpleNamespace

import pytest

from app.schemas.policy import Subject
from app.services import relation_runtime

pytestmark = [pytest.mark.unit, pytest.mark.smoke, pytest.mark.asyncio]


@pytest.mark.parametrize(
    "declaration, explicit_selector, expected",
    [
        (None, "title", "Default title"),
        (None, "custom_fields.display", "Fixture display"),
        ({}, "custom_fields.display", "Fixture display"),
        ({"label_field": "custom_fields.display"}, "title", "Fixture display"),
        ({"label_field": None}, "title", "Default title"),
    ],
)
async def test_authorized_projection_preserves_declared_and_explicit_selectors(
    monkeypatch, declaration, explicit_selector, expected
):
    """Render custom selectors without changing legacy title/default behavior."""
    target = SimpleNamespace(
        id="n.Entry.target",
        track_id="n.Track.target",
        title="Default title",
        custom_fields={"display": "Fixture display"},
    )
    admissions = []

    async def get_entry(node_id):
        assert node_id == target.id
        return target

    async def get_app(node_id):
        assert node_id == "n.App.target"
        return SimpleNamespace(name="Sample target")

    async def evaluate(*, subject, action, resource):
        admissions.append((subject.id, action, resource.id, resource.scope))
        return SimpleNamespace(allowed=True)

    monkeypatch.setattr(relation_runtime, "Entry", SimpleNamespace(get=get_entry))
    monkeypatch.setattr(relation_runtime, "App", SimpleNamespace(get=get_app))
    monkeypatch.setattr(relation_runtime, "policy_evaluate", evaluate)
    result = await relation_runtime.read_cross_app_label(
        viewer=Subject(kind="human", id="n.User.viewer"),
        relation_field_key="related",
        target_entry_id=target.id,
        target_app_id="n.App.target",
        label_field=explicit_selector,
        relation_spec=declaration,
    )

    assert result["kind"] == "relation"
    assert result["label"] == expected
    assert result["target_entry_id"] == target.id
    assert result["target_app_id"] == "n.App.target"
    assert admissions == [
        ("n.User.viewer", "entry.read", target.id, "track:n.Track.target")
    ]


@pytest.mark.parametrize("target_exists", [True, False])
async def test_restricted_projection_never_reads_selector_or_target_content(
    monkeypatch, target_exists
):
    """Denied and missing targets return only the strict restricted stub."""

    class UnreadableDeclaration(dict):
        def get(self, *args, **kwargs):
            raise AssertionError("Selector was interpreted before admission")

    class PrivateEntry:
        id = "n.Entry.private"
        track_id = "n.Track.private"

        @property
        def title(self):
            raise AssertionError("Denied target title was read")

        @property
        def custom_fields(self):
            raise AssertionError("Denied target fields were read")

    admissions = []

    async def get_entry(node_id):
        assert node_id == PrivateEntry.id
        return PrivateEntry() if target_exists else None

    async def get_app(node_id):
        raise AssertionError("Restricted target App metadata was read")

    async def evaluate(*, subject, action, resource):
        admissions.append((subject.id, action, resource.id))
        return SimpleNamespace(allowed=False)

    monkeypatch.setattr(relation_runtime, "Entry", SimpleNamespace(get=get_entry))
    monkeypatch.setattr(relation_runtime, "App", SimpleNamespace(get=get_app))
    monkeypatch.setattr(relation_runtime, "policy_evaluate", evaluate)
    result = await relation_runtime.read_cross_app_label(
        viewer=Subject(kind="human", id="n.User.viewer"),
        relation_field_key="related",
        target_entry_id=PrivateEntry.id,
        target_app_id="n.App.private",
        relation_spec=UnreadableDeclaration(label_field="custom_fields.private"),
    )

    assert result == {
        "kind": "restricted_relation",
        "relation_field_key": "related",
        "target_resource_kind": "entry",
    }
    assert admissions == (
        [("n.User.viewer", "entry.read", PrivateEntry.id)] if target_exists else []
    )
