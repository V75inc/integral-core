"""Calendar view manifest validation — date_field must exist on an entry type."""

import pytest

from app.exceptions import ContentProfileValidationError
from app.services.content_profile_compile import (
    _validate_calendar_view_mappings,
    compile_canonical_manifest,
)


def test_validate_calendar_view_mappings_rejects_unknown_date_field():
    tier = {
        "entry_types": [
            {"key": "post", "name": "Post", "fields": []},
        ],
        "views": [
            {
                "key": "content_calendar",
                "view_type": "calendar",
                "calendar_mapping": {"dateField": "custom_fields.publish_date"},
            }
        ],
    }
    with pytest.raises(ContentProfileValidationError, match="publish_date"):
        _validate_calendar_view_mappings(tier, where="track")


def test_validate_calendar_view_mappings_accepts_declared_date_field():
    tier = {
        "entry_types": [
            {
                "key": "content_piece",
                "name": "ContentPiece",
                "fields": [{"key": "publish_date", "type": "date"}],
            }
        ],
        "views": [
            {
                "key": "content_calendar",
                "view_type": "calendar",
                "calendar_mapping": {"dateField": "custom_fields.publish_date"},
            }
        ],
    }
    _validate_calendar_view_mappings(tier, where="track")


def test_compile_track_manifest_includes_calendar_validation():
    manifest = {
        "content_profile_schema_version": 2,
        "scope": "track",
        "package": {"name": "Cal", "version": "1.0.0"},
        "track": {
            "entry_types": [{"key": "post", "name": "Post", "fields": []}],
            "views": [
                {
                    "key": "cal",
                    "view_type": "calendar",
                    "calendar_mapping": {"dateField": "custom_fields.publish_date"},
                }
            ],
            "taxonomy": {"tag_groups": []},
        },
    }
    with pytest.raises(ContentProfileValidationError, match="publish_date"):
        compile_canonical_manifest(manifest=manifest)
