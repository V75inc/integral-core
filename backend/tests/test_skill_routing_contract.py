"""Deterministic checks for Core skill ownership and qualification coverage."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.smoke

ROOT = Path(__file__).resolve().parents[2]


def test_skill_ownership_contract_resolves_to_unique_core_skill_descriptions() -> None:
    yaml = pytest.importorskip("yaml")
    contract_path = (
        ROOT
        / "backend/tests/fixtures/qualification/contracts/skill-routing-contract.yaml"
    )
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    skills_root = ROOT / contract["skills_root"]

    ownership = contract["ownership"]
    intent_classes = [item["intent_class"] for item in ownership]
    assert len(intent_classes) == len(set(intent_classes))

    for item in ownership:
        skill_path = skills_root / item["owner_skill"] / "SKILL.md"
        assert skill_path.is_file(), item["owner_skill"]
        frontmatter = skill_path.read_text(encoding="utf-8").split("---", 2)[1]
        skill = yaml.safe_load(frontmatter)
        assert skill["name"] == item["owner_skill"]
        assert skill["description"].strip()
        assert item["boundary"].strip()


def test_skill_routing_cases_are_present_in_phase0_qualification_inventory() -> None:
    yaml = pytest.importorskip("yaml")
    contract = yaml.safe_load(
        (
            ROOT
            / "backend/tests/fixtures/qualification/contracts/skill-routing-contract.yaml"
        ).read_text(encoding="utf-8")
    )
    manifest = yaml.safe_load(
        (
            ROOT
            / "backend/tests/fixtures/qualification/contracts/phase-0-qualification-manifest.yaml"
        ).read_text(encoding="utf-8")
    )
    fixtures = {fixture["id"]: fixture for fixture in manifest["fixtures"]}

    assert set(contract["routing_cases"]) <= set(
        fixtures["skill_routing"]["required_cases"]
    )
