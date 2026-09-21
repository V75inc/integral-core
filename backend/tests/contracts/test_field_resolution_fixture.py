"""WP-02: backend and frontend share field-resolution conformance cases."""

from __future__ import annotations

import json
from pathlib import Path

from app.contracts.information import resolve_legacy_entry_field_path_value

_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "frontend"
    / "src"
    / "fixtures"
    / "fieldResolutionCases.json"
)


def test_information_contract_resolves_shared_field_cases() -> None:
    """Every UI conformance case has the same API/query result."""
    cases = json.loads(_FIXTURE.read_text(encoding="utf-8"))

    for case in cases:
        entry = case["entry"]
        for field_path, expected in case["expectations"].items():
            assert (
                resolve_legacy_entry_field_path_value(field_path, entry) == expected
            ), f"{case['name']}: {field_path}"
