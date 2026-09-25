"""UiRouteInteractAction — surface Integral page_context to the model.

``page_context`` lands on ``visitor.data`` for ``integral_get_page_context``,
but nothing otherwise puts those facts in the prompt. This InteractAction
mirrors jvagent's messenger ``PageContextInteractAction``: render a short
factual block and contribute it as an **orchestration** parameter (HOW),
never as utterance preamble or SESSION CONTEXT schema.

Thin-harness: facts only — no tool names beyond the existing host tool the
role already documents, no next-step cues.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Union

from jvspatial.core.annotations import attribute

from jvagent.action.interact.base import InteractAction

logger = logging.getLogger(__name__)

_MAX_PATH_CHARS = 200
_MAX_CRUMB_CHARS = 160
_MAX_LABEL_CHARS = 80


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


def format_ui_route_facts(ctx: Any) -> Optional[str]:
    """Render Integral ``page_context`` as compact factual prose.

    Returns ``None`` when nothing useful is present so the turn contributes
    no parameter.
    """
    if not isinstance(ctx, dict) or not ctx:
        return None

    kind = _clip(str(ctx.get("page_kind") or "").strip(), _MAX_LABEL_CHARS)
    path = _clip(
        str(ctx.get("route_path") or ctx.get("url") or "").strip(),
        _MAX_PATH_CHARS,
    )
    crumbs = ""
    raw_crumbs = ctx.get("breadcrumbs")
    if isinstance(raw_crumbs, list) and raw_crumbs:
        labels: List[str] = []
        for item in raw_crumbs[:12]:
            if isinstance(item, dict):
                label = str(item.get("label") or "").strip()
            else:
                label = str(item or "").strip()
            if label:
                labels.append(_clip(label, 40))
        if labels:
            crumbs = _clip(" › ".join(labels), _MAX_CRUMB_CHARS)

    meta = ctx.get("metadata") if isinstance(ctx.get("metadata"), dict) else {}

    def _meta(*keys: str) -> str:
        for key in keys:
            raw = meta.get(key)
            if raw is None:
                continue
            text = _clip(str(raw).strip(), _MAX_LABEL_CHARS)
            if text:
                return text
        return ""

    bits: List[str] = []
    if kind:
        bits.append(f"kind={kind}")
    app_label = _meta("app_title", "app_name", "app")
    if app_label:
        bits.append(f'app="{app_label}"')
    app_id = _clip(str(ctx.get("focused_app_id") or "").strip(), 128)
    if app_id:
        bits.append(f"app_id={app_id}")
    track_label = _meta("track_title", "track_name", "track")
    if track_label:
        bits.append(f'track="{track_label}"')
    track_id = _clip(str(ctx.get("focused_track_id") or "").strip(), 128)
    if track_id:
        bits.append(f"track_id={track_id}")
    view_id = _clip(str(ctx.get("focused_view_id") or "").strip(), 128)
    if view_id:
        bits.append(f"view_id={view_id}")
    entry_label = _meta("entry_title", "entry_name", "entry")
    if entry_label:
        bits.append(f'entry="{entry_label}"')
    entry_id = _clip(str(ctx.get("focused_entry_id") or "").strip(), 128)
    if entry_id:
        bits.append(f"entry_id={entry_id}")
    dash = _clip(str(meta.get("focused_dashboard_id") or "").strip(), 128)
    if dash:
        bits.append(f"dashboard_id={dash}")

    if not bits and not path and not crumbs:
        return None

    lines = [
        "UI focus is optional background — not the default answer scope "
        "(do not answer from the focused App merely because it is on screen).",
        "Current UI:",
    ]
    if bits:
        lines.append("  " + " · ".join(bits))
    if path:
        lines.append(f"  path={path}")
    if crumbs:
        lines.append(f"  crumbs={crumbs}")
    return "\n".join(lines)


class UiRouteInteractAction(InteractAction):
    """Contribute Integral UI route facts as an orchestration parameter."""

    description: str = attribute(
        default=(
            "Surfaces Integral page_context to the orchestrator as optional "
            "UI focus facts."
        ),
        description="Action description",
    )

    weight: int = attribute(
        default=-250,
        description=(
            "Runs before the Orchestrator (-200) so the context is on the "
            "interaction before the model composes."
        ),
    )

    always_execute: bool = attribute(
        default=True,
        description="Always inspect the turn for page_context.",
    )

    async def execute(self, visitor: Any) -> None:
        """Add a factual UI-route parameter when page_context is present."""
        interaction = visitor.interaction
        if not interaction:
            await visitor.unrecord_action_execution()
            return
        try:
            data = getattr(visitor, "data", None) or {}
            ctx = data.get("page_context") if isinstance(data, dict) else None
            facts = format_ui_route_facts(ctx) if ctx else None
            if not facts:
                await visitor.unrecord_action_execution()
                return
            # Orchestration scope: Orchestrator's literal reply path can skip
            # response-scoped params; the agentic loop is where focus matters.
            # Condition keeps this a HOW (when to apply), not a WHAT directive.
            await visitor.add_parameter(
                {
                    "scope": "orchestration",
                    "condition": (
                        "the user refers to the current screen (this/here/this "
                        "app/this track/the board/crumb name) or the ask is "
                        "clearly about that focused resource"
                    ),
                    "response": facts,
                }
            )
        except Exception as exc:  # never break the turn over context
            logger.error("UiRouteInteractAction: %s", exc, exc_info=True)
            await visitor.unrecord_action_execution()

    async def healthcheck(self) -> Union[bool, dict]:
        return True
