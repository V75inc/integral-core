"""One decision adapter for independently verified verbal staged reviews.

The approval token, operation, policy and executor remain common to all
channels. Each supported verbal projection must preserve the effect's exact
material details and binding. A new or unreviewable effect cannot silently
inherit voice authorization from another one.
"""

import asyncio
import hashlib
import json
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar

from app.agentive.services.approval_policy import ApprovalEffectClass

# The same contract indexes the canonical broker capability and the verbal
# decision adapter. Presence is necessary, not sufficient: the adapter must
# actually construct an exact, authorized review of this particular payload.
_bound_review = ContextVar("bound_voice_review", default=None)

VOICE_REVIEW_CAPABILITIES = {
    "create_entry": "integral_create_entry",
    "delete_entry": "integral_delete_entry",
    "update_entry": "integral_update_entry",
    "batch": "integral_commit_batch",
    "routine_task_create": "integral_schedule_task",
    "update_app": "integral_update_app",
    "create_dashboard": "integral_create_dashboard",
    "update_dashboard": "integral_update_dashboard",
}


# Explicit coverage inventory. Adding a staging kind requires an intentional
# design choice and tests; the fallback below is for unrecognized runtime
# data, not permission to omit a catalog entry.
IMPLEMENTED_VOICE_KINDS = frozenset(VOICE_REVIEW_CAPABILITIES) - {"create_dashboard"}
ORDINARY_REVIEW_PENDING = frozenset(
    {
        "create_app",
        "create_track",
        "update_track",
        "save_view",
        "create_dashboard",
        "delete_view",
        "delete_dashboard",
        "add_entry_tag",
        "remove_entry_tag",
        "create_tag",
        "update_tag",
        "link_entries",
        "transform_entry",
        "add_comment",
        "edit_comment",
        "delete_comment",
        "routine_task_update",
        "routine_task_cancel",
    }
)
DOMAIN_REVIEW_PENDING = frozenset(
    {
        "invoke_app_operation",
        "call_workspace_tool",
        "bulk_move_entries",
        "bulk_delete_entries",
        "file_content",
        "attach_file",
        "attach_uploaded_file",
        "attach_uploaded_image",
        "merge_tags",
        "merge_tracks",
        "split_track",
        "configure_search_index",
        "reindex_track",
        "register_track_template",
        "propose_profile_revision",
        "discard_profile_draft",
        "draft_new_profile",
        "author_operational_model",
        "modify_operational_model.*",
        "resolve_conflict",
        "trigger_sync",
        "routine_task_purge",
        "delete_track",
        "delete_app",
        "author_skill",
        "update_skill",
        "delete_skill",
        "publish_profile_draft",
        "apply_library_operational_model",
    }
)
ACCESS_REVIEW_PENDING = frozenset(
    {
        "share",
        "remove_collaborator",
        "set_exclusion",
        "remove_exclusion",
        "mint_share_link",
        "revoke_share_link",
        "invite",
    }
)


def review_coverage(kind: str) -> str:
    if kind.startswith("modify_operational_model."):
        kind = "modify_operational_model.*"
    if kind in IMPLEMENTED_VOICE_KINDS:
        return "bounded_voice_projection"
    if kind in ORDINARY_REVIEW_PENDING:
        return "ordinary_projection_pending"
    if kind in DOMAIN_REVIEW_PENDING:
        return "domain_projection_pending"
    if kind in ACCESS_REVIEW_PENDING:
        return "access_projection_pending"
    return "unknown"


def review_channel(kind: str, *, voice_active: bool, exact_voice_review: bool) -> str:
    """One presentation policy, independent of model/provider or tool name.

    The server's review projection, never a model claim, establishes whether
    this exact effect can be knowingly approved by voice.
    """
    if not voice_active:
        return "chat"
    if review_coverage(kind) == "bounded_voice_projection" and exact_voice_review:
        return "voice"
    return "visual_review_required"


@contextmanager
def confirmed_voice_review(review):
    """Enter the precise projection fences for one independently captured decision."""
    from app.services.native_voice_batch import confirmed_batch
    from app.services.native_voice_creation import confirmed_creation
    from app.services.native_voice_deletion import confirmed_deletion
    from app.services.native_voice_presentation import confirmed_presentation
    from app.services.native_voice_reminder import confirmed_reminder
    from app.services.native_voice_routine import confirmed_routine

    token = _bound_review.set((asyncio.current_task(), review))
    try:
        with ExitStack() as stack:
            for adapter in (
                confirmed_batch,
                confirmed_creation,
                confirmed_deletion,
                confirmed_presentation,
                confirmed_reminder,
                confirmed_routine,
            ):
                stack.enter_context(adapter(review))
            yield
    finally:
        _bound_review.reset(token)


def _permits_exact_update(staged, proposal):
    bound = _bound_review.get()
    if not bound or bound[0] is not asyncio.current_task():
        return False
    review = bound[1]
    if review.proposal_id != proposal.proposal_id or not review.fields:
        return False
    try:
        encoded = json.dumps(
            {"proposal": proposal.model_dump(), "payload": staged.payload},
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError):
        return False
    return hashlib.sha256(encoded).hexdigest() == review.fingerprint


def permits_voice_deletion(staged):
    from app.services.native_voice_deletion import permits_deletion

    return permits_deletion(staged)


def permits_voice_review(staged, proposal):
    """Single policy entry point; retain existing exact per-effect constraints."""
    from app.services.native_voice_batch import permits_batch
    from app.services.native_voice_creation import permits_creation
    from app.services.native_voice_presentation import permits_presentation
    from app.services.native_voice_reminder import permits_reminder
    from app.services.native_voice_routine import permits_routine

    return (
        permits_reminder(staged)
        or permits_routine(staged)
        or permits_batch(staged)
        or permits_presentation(staged)
        or permits_voice_deletion(staged)
        or (
            proposal.effect_class == ApprovalEffectClass.PRIVATE_REVERSIBLE
            and (
                (
                    proposal.effect_kind == "update_entry"
                    and _permits_exact_update(staged, proposal)
                )
                or (
                    proposal.effect_kind == "create_entry"
                    and permits_creation(proposal.proposal_id)
                )
            )
        )
    )
