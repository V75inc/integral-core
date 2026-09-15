"""ACC-03 — Authored policy set assertions.

Confirms `backend/scripts/author_default_policies.py` materializes the
three default-deny intent Policies and that they are fetchable through
the canonical Policy lookup surface (Policy.find — same primitive
`/api/policies` GET uses). The Policies record the engineer-role
default-deny intent on:

- ``track_template:project-financials`` (ACC-02 anchored privileged track)
- ``track_template:contracts-legal`` (ACC-02 anchored privileged track)
- ``track_template:payroll-*`` (forward-declared — Phase 17 attaches actual tracks)

The intent Policies are settings-visible declarations (I-SET-01). Per-user
enforcement is the ``EXCLUDED_FROM`` pass in ACC-04 and is exercised
end-to-end by ``test_privileged_track_access.py`` (T12).
"""

from __future__ import annotations

from typing import List

import pytest

from app.models.nodes import Policy
from scripts import author_default_policies as authorize


@pytest.mark.asyncio
async def test_author_policies_creates_three_intent_policies():
    """Running the script creates exactly the three documented intent Policies."""
    stats = await authorize.author_policies(
        dry_run=False, created_by="test-author-policies"
    )
    assert stats["created"] == 3, stats
    assert stats["existing"] == 0
    assert stats["would_create"] == 0


@pytest.mark.asyncio
async def test_author_policies_is_idempotent():
    """Re-running the script does not duplicate Policy rows."""
    first = await authorize.author_policies(
        dry_run=False, created_by="test-author-policies"
    )
    second = await authorize.author_policies(
        dry_run=False, created_by="test-author-policies"
    )
    assert first["created"] == 3
    assert second["created"] == 0
    assert second["existing"] == 3
    rows: List[Policy] = await Policy.find(
        {"context.subject_kind": "human", "context.subject_id": "engineer"}
    )
    scopes = {r.scope for r in rows}
    # Idempotent: exactly the three intent scopes, no duplicates.
    assert scopes == {
        "track_template:project-financials",
        "track_template:contracts-legal",
        "track_template:payroll-*",
    }, scopes
    # No additional engineer-subject policies leaked from re-runs.
    assert len(rows) == 3, [r.scope for r in rows]


@pytest.mark.asyncio
async def test_intent_policies_target_correct_track_template_scopes():
    """Each authored policy's scope encodes the target track-template key."""
    await authorize.author_policies(dry_run=False, created_by="test-author-policies")
    rows = await Policy.find(
        {"context.subject_kind": "human", "context.subject_id": "engineer"}
    )
    by_scope = {r.scope: r for r in rows}

    fin = by_scope.get("track_template:project-financials")
    assert fin is not None, by_scope.keys()
    assert fin.subject_kind == "human"
    assert fin.subject_id == "engineer"
    # Default-deny shape: empty actions list (engine fails-closed on no match).
    assert fin.actions == []
    assert fin.is_active is True

    legal = by_scope.get("track_template:contracts-legal")
    assert legal is not None
    assert legal.actions == []

    payroll = by_scope.get("track_template:payroll-*")
    assert payroll is not None
    assert payroll.actions == []


@pytest.mark.asyncio
async def test_dry_run_does_not_create_policies():
    """--dry-run is a no-op (stats report intent only)."""
    stats = await authorize.author_policies(dry_run=True, created_by="test-dry-run")
    assert stats["would_create"] == 3
    assert stats["created"] == 0
    rows = await Policy.find(
        {"context.subject_kind": "human", "context.subject_id": "engineer"}
    )
    assert rows == []
