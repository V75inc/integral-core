"""Tests for Integral UI route InteractAction formatting."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

_AGENT_ACTIONS_DIR = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "agent",
        "agents",
        "integral",
        "integral_agent",
        "actions",
        "integral",
    )
)
if _AGENT_ACTIONS_DIR not in sys.path:
    sys.path.insert(0, _AGENT_ACTIONS_DIR)

from ui_route_interact_action import (  # noqa: E402
    UiRouteInteractAction,
    format_ui_route_facts,
)


def test_format_ui_route_facts_compact():
    block = format_ui_route_facts(
        {
            "url": "/apps/n.App.sales",
            "route_path": "/apps/n.App.sales",
            "page_kind": "app_dashboards",
            "breadcrumbs": [
                {"label": "Business Admin"},
                {"label": "Sales"},
            ],
            "focused_app_id": "n.App.sales",
            "metadata": {"app_name": "Sales"},
        }
    )
    assert block is not None
    assert "optional background" in block
    assert 'app="Sales"' in block
    assert "app_id=n.App.sales" in block
    assert "BEGIN_CONTEXT_DATA" not in block


def test_format_ui_route_facts_empty():
    assert format_ui_route_facts(None) is None
    assert format_ui_route_facts({}) is None


@pytest.mark.asyncio
async def test_ui_route_action_adds_orchestration_parameter():
    added = []
    page = {
        "route_path": "/apps/n.App.1",
        "page_kind": "app_detail",
        "focused_app_id": "n.App.1",
        "metadata": {"app_name": "Demo"},
    }

    class _Visitor:
        interaction = object()
        data = {"page_context": page}

        async def add_parameter(self, param):
            added.append(param)

        async def unrecord_action_execution(self):
            pass

    action = UiRouteInteractAction()
    await action.execute(_Visitor())
    assert len(added) == 1
    assert added[0]["scope"] == "orchestration"
    assert "current screen" in added[0]["condition"]
    assert format_ui_route_facts(page) == added[0]["response"]


@pytest.mark.asyncio
async def test_ui_route_action_skips_without_context():
    calls = SimpleNamespace(unrecord=0)

    class _Visitor:
        interaction = object()
        data = {}

        async def add_parameter(self, param):
            raise AssertionError("should not add")

        async def unrecord_action_execution(self):
            calls.unrecord += 1

    await UiRouteInteractAction().execute(_Visitor())
    assert calls.unrecord == 1
