"""A package update refreshes a Track's description, but never one a person edited."""

from types import SimpleNamespace

import pytest

from app.services.operational_model_merge import (
    _previous_track_descriptions,
    sync_track_description_from_manifest,
)


def _track(purpose="", written=""):
    return SimpleNamespace(purpose=purpose, purpose_from_manifest=written)


def test_an_unedited_description_is_refreshed_and_recorded():
    t = _track("old text", written="old text")
    assert sync_track_description_from_manifest(t, "new text") is True
    assert (t.purpose, t.purpose_from_manifest) == ("new text", "new text")


def test_a_description_a_person_edited_is_left_alone():
    t = _track("my own words", written="old text")
    assert sync_track_description_from_manifest(t, "new text") is False
    assert t.purpose == "my own words"


def test_a_track_made_before_recording_matches_the_previous_package_text():
    t = _track("package v1 text")  # nothing recorded yet
    assert sync_track_description_from_manifest(t, "package v2 text", "package v1 text")
    assert t.purpose == "package v2 text"


def test_a_track_made_before_recording_with_other_text_is_treated_as_edited():
    t = _track("something else")
    assert not sync_track_description_from_manifest(
        t, "package v2 text", "package v1 text"
    )
    assert t.purpose == "something else"


def test_an_empty_description_is_filled():
    t = _track("")
    assert sync_track_description_from_manifest(t, "new text")
    assert t.purpose == "new text"


def test_no_manifest_description_changes_nothing():
    t = _track("kept", written="kept")
    assert sync_track_description_from_manifest(t, "") is False
    assert t.purpose == "kept"


def test_the_same_text_only_records_it_once():
    t = _track("same", written="")
    assert sync_track_description_from_manifest(t, "same") is True  # now recorded
    assert sync_track_description_from_manifest(t, "same") is False


@pytest.mark.asyncio
async def test_previous_descriptions_come_from_the_base_package_manifest(monkeypatch):
    definition = SimpleNamespace(
        base_package_manifest={
            "app": {"tracks": [{"key": "settings", "description": "v1"}, {"key": "x"}]}
        }
    )

    async def active(app_node, **kwargs):
        return definition

    monkeypatch.setattr(
        "app.services.application_definitions.get_active_application_definition", active
    )
    assert await _previous_track_descriptions(object()) == {"settings": "v1", "x": ""}


@pytest.mark.asyncio
async def test_missing_history_gives_no_previous_descriptions(monkeypatch):
    async def active(app_node, **kwargs):
        raise RuntimeError("no ledger")

    monkeypatch.setattr(
        "app.services.application_definitions.get_active_application_definition", active
    )
    assert await _previous_track_descriptions(object()) == {}
