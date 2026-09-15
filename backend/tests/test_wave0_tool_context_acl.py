"""ACL coverage for ToolContext (Wave 0 / F0)."""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from app.services.hooks.registry import ToolContext


class _Entry:
    def __init__(self, eid: str, custom_fields: Optional[Dict[str, Any]] = None):
        self.id = eid
        self.custom_fields = custom_fields or {}

    async def nodes(self, **_kwargs):
        return []


@pytest.mark.asyncio
async def test_find_entries_in_track_type_empty_without_tracks(monkeypatch):
    async def fake_tracks(*_a, **_k):
        return []

    monkeypatch.setattr(
        "app.services.hooks.registry.ToolContext._tracks_by_title",
        fake_tracks,
    )
    ctx = ToolContext(user_id="u1", workspace_id="w1", scope="entry:e1")
    assert await ctx.find_entries_in_track_type("Employees") == []


@pytest.mark.asyncio
async def test_get_employee_compensation_denies_without_role(monkeypatch):
    async def fake_resolve_role(_user_id, _rtype, _rid):
        return None

    monkeypatch.setattr(
        "app.services.permissions.resolve_role", staticmethod(fake_resolve_role)
    )

    ctx = ToolContext(user_id="u1", workspace_id="w1", scope="entry:e1")
    assert await ctx.get_employee_compensation("e_emp") is None


@pytest.mark.asyncio
async def test_get_employee_compensation_returns_pay_when_allowed(monkeypatch):
    emp = _Entry("e_emp")
    comp = _Entry("c_ok", {"base_salary": 99_000, "effective_date": "2024-01-01"})

    async def fake_get(eid):
        return emp if eid == "e_emp" else None

    async def fake_nodes(**_kwargs):
        return [comp]

    emp.nodes = fake_nodes  # type: ignore[attr-defined]

    async def fake_resolve_role(_user_id, _rtype, resource_id):
        if resource_id in ("e_emp", "c_ok"):
            return "viewer"
        return None

    monkeypatch.setattr("app.models.nodes.Entry.get", staticmethod(fake_get))
    monkeypatch.setattr(
        "app.services.permissions.resolve_role", staticmethod(fake_resolve_role)
    )

    ctx = ToolContext(user_id="u1", workspace_id="w1", scope="entry:e1")
    assert await ctx.get_employee_compensation("e_emp") == 99_000.0


@pytest.mark.asyncio
async def test_get_employee_compensation_skips_comp_records_without_role(monkeypatch):
    emp = _Entry("e_emp")
    allowed_comp = _Entry(
        "c_ok", {"base_salary": 50_000, "effective_date": "2024-01-01"}
    )
    denied_comp = _Entry(
        "c_no", {"base_salary": 200_000, "effective_date": "2025-01-01"}
    )

    async def fake_get(eid):
        return emp if eid == "e_emp" else None

    async def fake_nodes(**_kwargs):
        return [denied_comp, allowed_comp]

    emp.nodes = fake_nodes  # type: ignore[attr-defined]

    async def fake_resolve_role(_user_id, _rtype, resource_id):
        if resource_id in ("e_emp", "c_ok"):
            return "viewer"
        return None

    monkeypatch.setattr("app.models.nodes.Entry.get", staticmethod(fake_get))
    monkeypatch.setattr(
        "app.services.permissions.resolve_role", staticmethod(fake_resolve_role)
    )

    ctx = ToolContext(user_id="u1", workspace_id="w1", scope="entry:e1")
    # Newest (denied) skipped; falls through to allowed 50k.
    assert await ctx.get_employee_compensation("e_emp") == 50_000.0
