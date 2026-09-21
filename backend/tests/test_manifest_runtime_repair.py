"""Runtime repair for duplicate keys in persisted operational-model manifests."""

import pytest

from app.exceptions import BadRequestError
from app.services.operational_model_runtime import (
    compile_canonical_manifest,
    repair_stored_manifest_for_compile,
)


def test_repair_collapses_duplicate_track_entry_types():
    manifest = {
        "operational_model_schema_version": 2,
        "scope": "track",
        "track": {
            "entry_types": [
                {"key": "content_piece", "name": "Piece A"},
                {"key": "content_piece", "name": "Piece B"},
            ],
            "views": [{"key": "feed", "type": "feed"}],
            "taxonomy": {"tag_groups": []},
        },
    }
    with pytest.raises(BadRequestError, match="track.entry_types"):
        compile_canonical_manifest(manifest=manifest)

    repaired = repair_stored_manifest_for_compile(manifest)
    out = compile_canonical_manifest(manifest=repaired)
    keys = [e["key"] for e in out["track"]["entry_types"]]
    assert keys == ["content_piece"]
    assert out["track"]["entry_types"][0]["name"] == "Piece B"
