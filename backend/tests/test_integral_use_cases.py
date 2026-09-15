"""Smoke-test Integral CUCS scenarios load and stay aligned with agent SOP."""

from __future__ import annotations

import os

import pytest
from jvagent.testing.use_case_loader import discover_use_cases, load_use_case

from tests.integral_agent_paths import INTEGRAL_AGENT_APP_ROOT

_USE_CASES_ROOT = os.path.join(
    INTEGRAL_AGENT_APP_ROOT,
    "agents",
    "integral",
    "integral_agent",
    "use-cases",
)


def test_integral_use_cases_discover_non_empty():
    paths = discover_use_cases(_USE_CASES_ROOT)
    assert paths, "expected at least one CUCS under integral_agent/use-cases"


@pytest.mark.parametrize(
    "path",
    discover_use_cases(_USE_CASES_ROOT),
    ids=lambda p: p.stem,
)
def test_integral_use_case_loads(path):
    doc = load_use_case(path)
    assert doc["schema"] == "jvagent.use-case/v1"
    assert doc["turns"]


def test_attachments_list_and_deliver_harness_phrasing():
    """Card renders below the reply — harness must not steer the model to say 'above'."""
    path = os.path.join(_USE_CASES_ROOT, "attachments", "list-and-deliver.yaml")
    doc = load_use_case(path)
    list_turn = next(t for t in doc["turns"] if t["id"] == "list-files")
    answer = list_turn["harness"]["decisions"][-1]["answer"].lower()
    assert "above" not in answer
    assert "integral_list_attachments" in [
        d.get("tool")
        for d in list_turn["harness"]["decisions"]
        if d.get("action") == "tool"
    ]
    assert "integral_attachments" in doc["traceability"]["skills"]
