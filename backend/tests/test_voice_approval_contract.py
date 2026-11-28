"""The presentation policy is independent of the underlying approval mechanism."""

from app.agentive.tooling.manifest import load_manifest
from app.services.voice_approval_contract import (
    VOICE_REVIEW_CAPABILITIES,
    review_channel,
)


def test_all_manifest_staged_operations_have_a_channel_and_require_exact_voice_projection():
    manifest = load_manifest()
    kinds = {spec.staging_kind for spec in manifest.values() if spec.staging_kind}
    assert "routine_task_create" in kinds
    for kind in kinds:
        assert (
            review_channel(kind, voice_active=False, exact_voice_review=True) == "chat"
        )
        assert (
            review_channel(kind, voice_active=True, exact_voice_review=False)
            == "visual_review_required"
        )
        assert review_channel(kind, voice_active=True, exact_voice_review=True) == (
            "voice" if kind in VOICE_REVIEW_CAPABILITIES else "visual_review_required"
        )
    assert (
        review_channel(
            "unknown_future_operation", voice_active=True, exact_voice_review=True
        )
        == "visual_review_required"
    )


def test_native_voice_registry_points_at_exact_broker_capabilities():
    manifest = load_manifest()
    for kind, capability in VOICE_REVIEW_CAPABILITIES.items():
        # Commit-batch is the existing staging-exempt tool that closes a
        # previously staged batch. Its canonical proposal still binds batch.
        if kind == "batch":
            assert capability == "integral_commit_batch"
            assert manifest[capability].staging_kind is None
        else:
            assert manifest[capability].staging_kind == kind


import hashlib
import json
from types import SimpleNamespace
from datetime import datetime, timezone, timedelta
import pytest
from app.agentive.services.approval_decisions import proposal_from_staged
from app.services.native_voice_review import VoiceReviewReadout
from app.services.voice_approval_contract import (
    confirmed_voice_review,
    permits_voice_review,
)


@pytest.mark.asyncio
async def test_scalar_voice_decision_requires_bound_exact_proposal_and_rejects_payload_drift():
    item = SimpleNamespace(
        kind="update_entry",
        token="proposal",
        user_id="caller",
        workspace_id="workspace",
        session_id="thread",
        summary="Rename entry",
        diff_human="Rename from A to B",
        idempotency_key=None,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        payload={"entry_id": "entry", "title": "B", "expected_record_revision": 1},
    )
    proposal = proposal_from_staged(item)
    digest = hashlib.sha256(
        json.dumps(
            {"proposal": proposal.model_dump(), "payload": item.payload},
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()
    review = VoiceReviewReadout(
        "rename A to B",
        "work",
        "run",
        "proposal",
        "entry",
        digest,
        fields=(("title", "Title"),),
    )
    assert not permits_voice_review(item, proposal)
    with confirmed_voice_review(review):
        assert permits_voice_review(item, proposal)
        item.payload["title"] = "C"
        assert not permits_voice_review(item, proposal_from_staged(item))
    item.payload["title"] = "B"
    assert not permits_voice_review(item, proposal)
