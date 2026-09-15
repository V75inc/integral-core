"""Hook resolver — match-block semantics + ambiguity behavior."""

import pytest

from app.services.hooks.registry import (
    clear_workspace_registrations,
    register_workspace_hooks,
)
from app.services.hooks.resolver import find_matching_bindings


def setup_ws(ws_id: str, bindings: list) -> None:
    clear_workspace_registrations(ws_id)
    register_workspace_hooks(ws_id, "bundle-x", bindings)


def test_single_match_returns_one():
    ws = "n.Workspace.r1"
    setup_ws(
        ws,
        [
            {
                "point": "entry.transform",
                "key": "to_opp",
                "match": {"source_entry_type": "proposal"},
                "mode": "declarative",
                "declarative": {},
            },
            {
                "point": "entry.transform",
                "key": "to_other",
                "match": {"source_entry_type": "scope"},
                "mode": "declarative",
                "declarative": {},
            },
        ],
    )
    matches = find_matching_bindings(
        ws, "entry.transform", {"source_entry_type": "proposal"}
    )
    assert len(matches) == 1
    assert matches[0]["key"] == "to_opp"


def test_no_match_returns_empty():
    ws = "n.Workspace.r2"
    setup_ws(
        ws,
        [
            {
                "point": "entry.transform",
                "key": "k",
                "match": {"source_entry_type": "x"},
                "mode": "declarative",
                "declarative": {},
            }
        ],
    )
    matches = find_matching_bindings(ws, "entry.transform", {"source_entry_type": "y"})
    assert matches == []


def test_missing_match_key_acts_as_wildcard():
    ws = "n.Workspace.r3"
    setup_ws(
        ws,
        [
            {
                "point": "entry.transform",
                "key": "wild",
                "match": {},
                "mode": "declarative",
                "declarative": {},
            }
        ],
    )
    matches = find_matching_bindings(
        ws, "entry.transform", {"source_entry_type": "anything"}
    )
    assert len(matches) == 1


def test_multi_match_ordered_by_registration():
    ws = "n.Workspace.r4"
    setup_ws(
        ws,
        [
            {
                "point": "entry.transform",
                "key": "first",
                "match": {},
                "mode": "declarative",
                "declarative": {},
            },
            {
                "point": "entry.transform",
                "key": "second",
                "match": {},
                "mode": "declarative",
                "declarative": {},
            },
        ],
    )
    matches = find_matching_bindings(ws, "entry.transform", {})
    assert [m["key"] for m in matches] == ["first", "second"]


def test_and_combine_all_match_keys():
    ws = "n.Workspace.r5"
    setup_ws(
        ws,
        [
            {
                "point": "entry.transform",
                "key": "narrow",
                "match": {"source_entry_type": "p", "target_track_type": "o"},
                "mode": "declarative",
                "declarative": {},
            },
        ],
    )
    # Payload supplies BOTH match keys.
    assert (
        len(
            find_matching_bindings(
                ws,
                "entry.transform",
                {"source_entry_type": "p", "target_track_type": "o"},
            )
        )
        == 1
    )
    # Payload supplies only ONE — should NOT match (other key absent in payload = no match if binding constrains it).
    assert (
        find_matching_bindings(ws, "entry.transform", {"source_entry_type": "p"}) == []
    )
    # Payload supplies neither — no match.
    assert find_matching_bindings(ws, "entry.transform", {}) == []


def test_explicit_hook_key_disambiguates():
    ws = "n.Workspace.r6"
    setup_ws(
        ws,
        [
            {
                "point": "entry.transform",
                "key": "a",
                "match": {},
                "mode": "declarative",
                "declarative": {},
            },
            {
                "point": "entry.transform",
                "key": "b",
                "match": {},
                "mode": "declarative",
                "declarative": {},
            },
        ],
    )
    matches = find_matching_bindings(ws, "entry.transform", {}, explicit_hook_key="b")
    assert len(matches) == 1
    assert matches[0]["key"] == "b"


def test_track_type_hyphen_underscore_and_customer_projects_alias():
    ws = "n.Workspace.r7"
    setup_ws(
        ws,
        [
            {
                "point": "entry.transform",
                "key": "opportunity_to_project",
                "match": {
                    "source_entry_type": "opportunity",
                    "target_track_type": "projects",
                    "target_entry_type": "project",
                },
                "mode": "declarative",
                "declarative": {},
            },
        ],
    )
    # Title slug of "Customer Projects" vs template key "projects".
    assert (
        len(
            find_matching_bindings(
                ws,
                "entry.transform",
                {
                    "source_entry_type": "opportunity",
                    "target_track_type": "customer_projects",
                    "target_entry_type": "project",
                },
            )
        )
        == 1
    )
    assert (
        len(
            find_matching_bindings(
                ws,
                "entry.transform",
                {
                    "source_entry_type": "opportunity",
                    "target_track_type": "customer-projects",
                    "target_entry_type": "project",
                },
            )
        )
        == 1
    )


def test_explicit_hook_key_ignores_target_track_type_drift():
    ws = "n.Workspace.r8"
    setup_ws(
        ws,
        [
            {
                "point": "entry.transform",
                "key": "opportunity_to_project",
                "match": {
                    "source_entry_type": "opportunity",
                    "target_track_type": "projects",
                    "target_entry_type": "project",
                },
                "mode": "declarative",
                "declarative": {},
            },
        ],
    )
    # Soft match: only source_entry_type required when hook_key is set.
    matches = find_matching_bindings(
        ws,
        "entry.transform",
        {
            "source_entry_type": "opportunity",
            "target_track_type": "internal_projects",
        },
        explicit_hook_key="opportunity_to_project",
    )
    assert len(matches) == 1
    assert (
        find_matching_bindings(
            ws,
            "entry.transform",
            {"source_entry_type": "bid"},
            explicit_hook_key="opportunity_to_project",
        )
        == []
    )
