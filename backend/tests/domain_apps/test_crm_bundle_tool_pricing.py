"""Sales bundle tool — compute_proposal_pricing (replaces app/services/pricing.py).

Phase 30 Wave C Task C2 — verifies the ported tool reads through
ToolContext (not direct app.services.* imports) and matches the
behaviour of the legacy pricing service: above-margin, under-margin,
and fallback-cost paths.

Phase 31 (DR-31-01 §4) — the tool moved with its bound
``proposal_pricing`` precompute hook to the **sales** bundle when
``crm-plus-pm-suite`` was decomposed.
"""

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_pricing_above_target_yields_under_margin_false():
    from app.profiles.sales.tools.pricing import compute_proposal_pricing
    from app.services.hooks.registry import ToolContext

    ctx = ToolContext(user_id="u", workspace_id="w", scope="entry:p")

    class _StubEntry:
        def __init__(self, cf):
            self.custom_fields = cf

    scoping = _StubEntry(
        {
            "role_effort": [
                {"role_key": "senior_engineer", "hours": 120},
                {"role_key": "engineer", "hours": 200},
            ]
        }
    )
    rubric = _StubEntry(
        {
            "target_margin_pct": 30,
            "overhead_factor": 1.0,
            "rubric_lines": [
                {
                    "role_key": "senior_engineer",
                    "bill_rate": 185,
                    "employee_id": "emp_s",
                },
                {"role_key": "engineer", "bill_rate": 140, "employee_id": "emp_e"},
            ],
        }
    )
    ctx.get_entry = AsyncMock(
        side_effect=lambda eid: scoping if eid == "scope-1" else rubric
    )
    ctx.get_employee_compensation = AsyncMock(
        side_effect=lambda eid: {"emp_s": 110 * 2080, "emp_e": 82 * 2080}.get(eid)
    )

    result = await compute_proposal_pricing(
        {"scoping_document_id": "scope-1", "pricing_rubric_id": "rubric-1"},
        ctx,
    )
    assert result["total_price"] == 120 * 185 + 200 * 140
    assert result["total_cost"] == 120 * 110 + 200 * 82
    assert result["projected_margin_pct"] > 30
    assert result["under_margin"] is False
    assert result["cost_estimated_lines"] == []


@pytest.mark.asyncio
async def test_pricing_under_target_sets_under_margin_true():
    from app.profiles.sales.tools.pricing import compute_proposal_pricing
    from app.services.hooks.registry import ToolContext

    ctx = ToolContext(user_id="u", workspace_id="w", scope="entry:p")

    class _Stub:
        def __init__(self, cf):
            self.custom_fields = cf

    scoping = _Stub(
        {
            "role_effort": [
                {"role_key": "senior_engineer", "hours": 120},
                {"role_key": "engineer", "hours": 200},
            ]
        }
    )
    rubric = _Stub(
        {
            "target_margin_pct": 30,
            "overhead_factor": 1.0,
            "rubric_lines": [
                # Half the senior bill_rate → margin collapses below target.
                {
                    "role_key": "senior_engineer",
                    "bill_rate": 92,
                    "employee_id": "emp_s",
                },
                {"role_key": "engineer", "bill_rate": 140, "employee_id": "emp_e"},
            ],
        }
    )
    ctx.get_entry = AsyncMock(side_effect=lambda eid: scoping if eid == "s" else rubric)
    ctx.get_employee_compensation = AsyncMock(
        side_effect=lambda eid: {"emp_s": 110 * 2080, "emp_e": 82 * 2080}.get(eid)
    )

    result = await compute_proposal_pricing(
        {"scoping_document_id": "s", "pricing_rubric_id": "r"}, ctx
    )
    assert result["under_margin"] is True


@pytest.mark.asyncio
async def test_pricing_falls_back_to_fallback_cost_when_no_compensation():
    from app.profiles.sales.tools.pricing import compute_proposal_pricing
    from app.services.hooks.registry import ToolContext

    ctx = ToolContext(user_id="u", workspace_id="w", scope="entry:p")

    class _Stub:
        def __init__(self, cf):
            self.custom_fields = cf

    scoping = _Stub({"role_effort": [{"role_key": "engineer", "hours": 100}]})
    rubric = _Stub(
        {
            "target_margin_pct": 25,
            "overhead_factor": 1.0,
            "rubric_lines": [
                {
                    "role_key": "engineer",
                    "bill_rate": 100,
                    "employee_id": "missing",
                    "fallback_cost": 60,
                },
            ],
        }
    )
    ctx.get_entry = AsyncMock(side_effect=lambda eid: scoping if eid == "s" else rubric)
    ctx.get_employee_compensation = AsyncMock(return_value=None)

    result = await compute_proposal_pricing(
        {"scoping_document_id": "s", "pricing_rubric_id": "r"}, ctx
    )
    assert result["total_cost"] == 100 * 60
    assert "engineer" in result.get("cost_estimated_lines", [])
