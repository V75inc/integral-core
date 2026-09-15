"""Plan-status rollup Walker.

Rolling a plan's status up from the items it links to is a multi-hop graph
cascade — jvspatial Pillar 3 reserves that for a ``Walker`` rather than
procedural recursion. Spawned at a ``plan`` Entry, this walker follows the
plan's outbound ``REFERENCES`` edges (the ``linked_items`` relation) to the
linked entries and accumulates the open-item count and an at-risk verdict.

The result feeds cached ``open_count`` / ``at_risk`` mirrors via the
``plan_rollup`` precompute (bundle tool ``rollup_status`` → ``ctx.rollup_plan``
→ this walker). The mirrors are sanctioned denormalization; the edges remain the
source of truth, refreshed on the precompute. Lives in the substrate because
bundle tools may not import ``app.models`` / jvspatial (ToolContext facade rule);
mirrors the existing ``cross_app_resolver`` walker.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Tuple

from jvspatial.core import Walker
from jvspatial.core.decorators import on_visit

from app.models.edges import REFERENCES
from app.models.nodes import Entry

# Linked-item statuses that count as resolved (not open).
_DONE_STATUSES = {"done", "closed", "complete", "completed", "cancelled", "resolved"}
# Linked-item statuses that make the plan at-risk.
_BLOCKED_STATUSES = {"blocked", "at_risk"}


def classify_item(status: str) -> Tuple[bool, bool]:
    """(is_open, is_blocked) for a linked item's status."""
    s = (status or "").strip().lower()
    is_blocked = s in _BLOCKED_STATUSES
    is_open = bool(s) and s not in _DONE_STATUSES
    return is_open, is_blocked


def plan_overdue(custom_fields: Dict[str, Any]) -> bool:
    """A non-done plan past its target_date is at risk."""
    if str(custom_fields.get("status") or "").strip().lower() == "done":
        return False
    raw = custom_fields.get("target_date")
    if not raw:
        return False
    try:
        return date.fromisoformat(str(raw)[:10]) < date.today()
    except ValueError:
        return False


class PlanRollupWalker(Walker):
    """Accumulate open_count + at_risk for a plan from its linked items."""

    start_id: str = ""
    open_count: int = 0
    at_risk: bool = False

    @on_visit(Entry)
    async def on_entry(self, here: Entry) -> None:
        custom_fields = getattr(here, "custom_fields", {}) or {}
        if here.id == self.start_id:
            # The plan itself: overdue → at risk; then walk to its linked items.
            if plan_overdue(custom_fields):
                self.at_risk = True
            linked = await here.nodes(
                edge=[REFERENCES], direction="out", node=["Entry"]
            )
            if linked:
                await self.visit(linked)
            return
        # A linked item: count it if open, flag the plan if it's blocked.
        is_open, is_blocked = classify_item(str(custom_fields.get("status") or ""))
        if is_open:
            self.open_count += 1
        if is_blocked:
            self.at_risk = True


async def roll_up_plan(plan_id: str) -> Dict[str, Any]:
    """Spawn the rollup walker at a plan and return ``{open_count, at_risk}``."""
    plan = await Entry.get(plan_id)
    if plan is None:
        return {"open_count": 0, "at_risk": False}
    walker = PlanRollupWalker(start_id=plan_id)
    await walker.spawn(plan)
    return {"open_count": int(walker.open_count), "at_risk": bool(walker.at_risk)}


__all__ = ["PlanRollupWalker", "roll_up_plan", "classify_item", "plan_overdue"]
