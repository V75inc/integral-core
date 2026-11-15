"""Candidate impact must use the declared identity of each populated track."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.exceptions import BadRequestError
from app.services.operational_model_diff import (
    _candidate_track_tier,
    compute_entry_impact,
)

pytestmark = pytest.mark.smoke


def _tier(key, entry_key):
    return {
        "key": key,
        "name": key.replace("-", " ").title(),
        "description": "Contains disposable records used to verify safe candidate resolution.",
        "entry_types": [
            {
                "key": entry_key,
                "name": "Detail Note" if entry_key == "detail_note" else "Record",
                "fields": [{"key": "body", "type": "text"}],
            }
        ],
        "views": [],
    }


def _manifest():
    return {
        "operational_model_schema_version": 2,
        "scope": "app",
        "package": {
            "slug": "fixture-records",
            "name": "Fixture Records",
            "version": "1.0.1",
        },
        "app": {
            "tracks": [_tier("records", "record")],
            "track_templates": [_tier("record-details", "detail_note")],
        },
    }


@pytest.mark.parametrize("has_prescribed_tracks", [True, False])
def test_candidate_resolves_unchanged_anchor_template(has_prescribed_tracks):
    candidate = _manifest()
    if not has_prescribed_tracks:
        candidate["app"]["tracks"] = []
    before = deepcopy(candidate)
    track = SimpleNamespace(
        template_id="record-details", title="Notes for a renamed record"
    )
    tier = _candidate_track_tier(candidate, track)
    assert tier["key"] == "record-details"
    assert [item["key"] for item in tier["entry_types"]] == ["detail_note"]
    assert candidate == before


def test_candidate_resolves_hyphenated_prescribed_identity_before_title():
    candidate = _manifest()
    candidate["app"]["tracks"].append(_tier("saved-records", "saved_record"))
    tier = _candidate_track_tier(
        candidate, SimpleNamespace(template_id="saved-records", title="Records")
    )
    assert tier["key"] == "saved-records"


def test_candidate_missing_explicit_identity_never_uses_another_track_schema():
    candidate = _manifest()
    candidate["app"]["tracks"][0]["entry_types"] = deepcopy(
        candidate["app"]["track_templates"][0]["entry_types"]
    )
    candidate["app"]["track_templates"] = []
    assert (
        _candidate_track_tier(
            candidate, SimpleNamespace(template_id="record-details", title="Records")
        )
        == {}
    )


def test_candidate_does_not_treat_a_normalized_alias_as_the_declared_identity():
    assert (
        _candidate_track_tier(
            _manifest(), SimpleNamespace(template_id="record_details", title="Renamed")
        )
        == {}
    )


def test_candidate_exact_identity_wins_over_a_normalized_collision():
    candidate = _manifest()
    candidate["app"]["tracks"].append(_tier("record_details", "different_record"))
    tier = _candidate_track_tier(
        candidate, SimpleNamespace(template_id="record-details", title="Records")
    )
    assert [item["key"] for item in tier["entry_types"]] == ["detail_note"]


def test_candidate_does_not_guess_between_normalized_identities():
    candidate = _manifest()
    candidate["app"]["tracks"].append(_tier("record_details", "different_record"))
    assert (
        _candidate_track_tier(
            candidate, SimpleNamespace(template_id="Record details", title="Records")
        )
        == {}
    )


def test_candidate_rejects_the_same_explicit_identity_in_both_registries():
    candidate = _manifest()
    candidate["app"]["tracks"].append(_tier("record-details", "different_record"))
    with pytest.raises(BadRequestError):
        _candidate_track_tier(
            candidate, SimpleNamespace(template_id="record-details", title="Records")
        )


def test_candidate_keeps_legacy_unbound_prescribed_track_fallback():
    assert (
        _candidate_track_tier(
            _manifest(), SimpleNamespace(template_id="", title="Legacy list")
        )["key"]
        == "records"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("template_removed", [False, True])
async def test_impact_validates_the_template_and_still_refuses_actual_removal(
    monkeypatch, template_removed
):
    import app.services.operational_model_entry_fields as fields_module
    from app.models.nodes import EntryType

    candidate = _manifest()
    if template_removed:
        # An unrelated track can contain the same type. It must not mask removal.
        candidate["app"]["tracks"][0]["entry_types"] = deepcopy(
            candidate["app"]["track_templates"][0]["entry_types"]
        )
        candidate["app"]["track_templates"] = []
    entry = SimpleNamespace(
        id="fixture-note",
        type_id="fixture-type",
        custom_fields={"body": "Preserve this value"},
    )
    before = deepcopy(entry.custom_fields)
    track = SimpleNamespace(
        id="fixture-track",
        template_id="record-details",
        title="Records",
        nodes=AsyncMock(return_value=[entry]),
    )
    entry_type = SimpleNamespace(
        name="Renamed Note", form_schema={"_manifest_entry_type_key": "detail_note"}
    )
    monkeypatch.setattr(EntryType, "get", AsyncMock(return_value=entry_type))
    validate = AsyncMock()
    monkeypatch.setattr(
        fields_module, "validate_and_materialize_entry_custom_fields", validate
    )
    impact = await compute_entry_impact(track=track, candidate_manifest=candidate)
    assert impact["total"] == 1
    assert impact["would_fail_validation"] == int(template_removed)
    assert impact["would_need_migration"] == 0
    assert entry.custom_fields == before
    if template_removed:
        validate.assert_not_awaited()
        assert impact["sample_failure_reasons"] == [
            {
                "entry_id": entry.id,
                "reason": "entry_type 'detail_note' removed from candidate",
            }
        ]
    else:
        validate.assert_awaited_once()
        assert validate.call_args.kwargs["materialize"] is False
        assert validate.call_args.kwargs["runtime_tier"]["key"] == "record-details"
