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
