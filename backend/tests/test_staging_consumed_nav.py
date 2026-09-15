"""Consumed-nav extraction for persisted staging approval cards."""

from __future__ import annotations

from app.services.staging_consumed_nav import extract_consumed_nav


def test_extract_consumed_nav_create_track_flat():
    staged = {
        "kind": "create_track",
        "diff_machine": {"op": "create_track"},
    }
    nav = extract_consumed_nav(
        {"id": "n.Track.aaaaaaaaaaaaaaaaaaaaaaaa", "title": "Sprint board"},
        staged,
    )
    assert nav["trackId"] == "n.Track.aaaaaaaaaaaaaaaaaaaaaaaa"
    assert nav["title"] == "Sprint board"
    assert nav["created"] == [
        {
            "kind": "track",
            "id": "n.Track.aaaaaaaaaaaaaaaaaaaaaaaa",
            "title": "Sprint board",
        }
    ]


def test_extract_consumed_nav_create_entry_with_track():
    staged = {
        "kind": "create_entry",
        "diff_machine": {
            "op": "create_entry",
            "track_id": "n.Track.bbbbbbbbbbbbbbbbbbbbbbbb",
        },
    }
    nav = extract_consumed_nav(
        {
            "id": "n.Entry.cccccccccccccccccccccccccc",
            "title": "Follow up",
            "track_id": "n.Track.bbbbbbbbbbbbbbbbbbbbbbbb",
        },
        staged,
    )
    assert nav["entryId"] == "n.Entry.cccccccccccccccccccccccccc"
    assert nav["trackId"] == "n.Track.bbbbbbbbbbbbbbbbbbbbbbbb"


def test_extract_consumed_nav_batch_results():
    staged = {"kind": "batch", "diff_machine": {"op": "batch"}}
    nav = extract_consumed_nav(
        {
            "batched": True,
            "results": [
                {
                    "kind": "create_app",
                    "result": {
                        "id": "n.App.dddddddddddddddddddddddddd",
                        "name": "CRM",
                    },
                },
                {
                    "kind": "create_track",
                    "result": {
                        "id": "n.Track.eeeeeeeeeeeeeeeeeeeeeeee",
                        "title": "Deals",
                    },
                },
            ],
        },
        staged,
    )
    assert nav["appId"] == "n.App.dddddddddddddddddddddddddd"
    assert nav["trackId"] == "n.Track.eeeeeeeeeeeeeeeeeeeeeeee"
    assert len(nav["created"]) == 2
