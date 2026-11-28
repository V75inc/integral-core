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


def review_channel(kind: str, *, voice_active: bool, exact_voice_review: bool) -> str:
    """One presentation policy, independent of model/provider or tool name.

    The server's review projection, never a model claim, establishes whether
    this exact effect can be knowingly approved by voice.
    """
    if not voice_active:
        return "chat"
    if kind in VOICE_REVIEW_CAPABILITIES and exact_voice_review:
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
