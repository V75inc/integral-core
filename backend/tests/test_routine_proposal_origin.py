"""Proposal provenance is private server state, not user-controlled authority."""

import asyncio

import pytest

from app.agentive.services.proposal_origin import (
    bind_proposal_origin,
    current_proposal_origin,
)
from app.agentive.services.routine_mandate import routine_mandate_fingerprint
from app.agentive.staging_store import decision_intent_fingerprint

pytestmark = [pytest.mark.asyncio, pytest.mark.smoke]


async def test_origin_binding_is_task_and_scope_owned():
    proof = {
        "principal_id": "owner",
        "workspace_id": "workspace",
        "effect_key": "exact",
    }
    assert current_proposal_origin("owner", "workspace") is None
    with bind_proposal_origin(proof):
        own = current_proposal_origin("owner", "workspace")
        assert own == proof
        own["effect_key"] = "changed"
        assert current_proposal_origin("owner", "workspace") == proof
        assert current_proposal_origin("other", "workspace") is None
        assert current_proposal_origin("owner", "other") is None

        async def inherited():
            return current_proposal_origin("owner", "workspace")

        assert await asyncio.create_task(inherited()) is None
    assert current_proposal_origin("owner", "workspace") is None


async def test_proof_changes_intent_but_none_preserves_legacy_identity():
    fields = {"token": "token", "payload": {"title": "Original"}}
    original = decision_intent_fingerprint(fields)
    assert decision_intent_fingerprint({**fields, "origin_proof": None}) == original
    assert (
        decision_intent_fingerprint({**fields, "origin_proof": {"effect_key": "exact"}})
        != original
    )


async def test_mandate_excludes_progress_but_binds_reviewed_authority():
    mandate = {
        "instruction": "Update one record.",
        "write_scope": [
            {"resource_type": "entry", "resource_id": "a"},
            {"resource_type": "track", "resource_id": "b"},
        ],
    }
    original = routine_mandate_fingerprint(mandate)
    assert (
        routine_mandate_fingerprint(
            {
                **mandate,
                "run_count": 10,
                "updated_at": "later",
                "next_run_at": "tomorrow",
            }
        )
        == original
    )
    assert (
        routine_mandate_fingerprint(
            {**mandate, "write_scope": list(reversed(mandate["write_scope"]))}
        )
        == original
    )
    for field, value in {
        "instruction": "Different work",
        "workspace_id": "other",
        "thread_id": "other",
        "agent_id": "other",
        "write_scope": [],
        "max_runs": 9,
    }.items():
        assert routine_mandate_fingerprint({**mandate, field: value}) != original
