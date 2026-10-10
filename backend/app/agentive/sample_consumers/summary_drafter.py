"""Sample consumer (EVT-03) — drafts a summary Entry on tagged-write trigger.

Per CONTEXT D-11: this module lives in `app/agentive/sample_consumers/`,
opt-in through AGENTIVE_SAMPLE_CONSUMER_ENABLED within the always-on agentive
layer. It is a demonstration consumer, not a substrate routing rule.

Demonstrates the full WS subscribe → event emit → consumer hook → entry write →
ChangeEvent emit loop end-to-end. Resident proactivity (routines, scheduled
skills) builds on the same subscription / hook pattern.

Recursion guard (two layers — defense in depth):
1. The auto-drafted summary Entry is created with `tags=[]`, so the resulting
   `entry.create` ChangeEvent's `after.tags` does NOT contain the trigger tag.
2. `on_change_event` short-circuits when `actor_kind == "agent"` AND
   `actor_id == CONSUMER_ID` — even if some future field carries the trigger
   tag, the consumer never re-triggers itself.
"""

from __future__ import annotations

import logging

from app.models.nodes import Tag, Track
from app.schemas.provenance import Provenance
from app.services.change_event_logger import ChangeEventEnvelope
from app.services.entry_create import create_entry_in_track
from app.services.event_subscription_registry import register_consumer_hook
from app.utils.time import utc_now

logger = logging.getLogger(__name__)

CONSUMER_ID = "sample.summary_drafter"
TRIGGER_TAG = "summarize-me"


async def on_change_event(event: ChangeEventEnvelope) -> None:
    """Receive a ChangeEvent. If it matches the trigger, draft a summary Entry."""
    # Layer-2 recursion guard — ignore our own writes (defense in depth; the
    # primary guard is the empty-tags property of the auto-drafted entry).
    if event.actor_kind == "agent" and event.actor_id == CONSUMER_ID:
        return

    # Trigger only on entry creations (entry.update events even with the
    # trigger tag are intentionally ignored — the consumer is "draft on new
    # tagged write" by design).
    if event.action != "entry.create":
        return

    after = event.after or {}
    tags = after.get("tags") or []

    track_id = after.get("track_id")
    if not track_id:
        logger.warning(
            "summary_drafter: tagged entry has no track_id in event.after — skipping"
        )
        return

    track = await Track.get(track_id)
    if not track:
        logger.warning(
            "summary_drafter: tagged entry track_id %s did not resolve — skipping",
            track_id,
        )
        return

    # Events carry canonical tag identities. Resolve only tags declared on
    # this destination, rather than treating a free-form label as an identity.
    trigger = False
    for tag_id in tags:
        tag = await Tag.get(str(tag_id))
        if tag and tag.track_id == track.id and tag.name_fold == TRIGGER_TAG:
            trigger = True
            break
    if not trigger:
        return

    src_entry_id = after.get("id", "")
    src_title = after.get("title", "untitled")
    await create_entry_in_track(
        track=track,
        user_id=track.owner_id,
        title=f"Summary: {src_title}",
        body=f"[auto-drafted by sample consumer]\n\nSource entry: {src_entry_id}",
        tags=[],  # Recursion guard: the result has no trigger tag.
        provenance=Provenance(
            source="agent",
            source_id=CONSUMER_ID,
            confidence=0.5,
            derived_from=[src_entry_id] if src_entry_id else [],
            synced_at=utc_now(),
        ),
        actor_kind="agent",
        actor_id=CONSUMER_ID,
        workspace_id=track.workspace_id,
    )


def start() -> None:
    """Register the consumer hook with the event subscription registry."""
    register_consumer_hook(on_change_event)
