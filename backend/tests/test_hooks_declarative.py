"""Declarative hook interpreters — transform / public_share / dedup grammars."""

import pytest


def test_transform_copy_strip():
    from app.services.hooks.declarative import apply_transform

    source = {
        "title": "Proposal X",
        "body": "Cover",
        "custom_fields": {
            "total_price": 1000,
            "total_cost": 600,
            "projected_margin_pct": 40,
            "line_items": [{"x": 1}],
        },
    }
    block = {
        "copy_fields": [
            {"from": "title", "to": "title"},
            {"from": "body", "to": "body"},
            {"from": "custom_fields.total_price", "to": "custom_fields.value"},
        ],
        "strip_fields": [
            "custom_fields.total_cost",
            "custom_fields.projected_margin_pct",
            "custom_fields.line_items",
        ],
    }
    result = apply_transform(source, block)
    assert result["title"] == "Proposal X"
    assert result["body"] == "Cover"
    assert result["custom_fields"]["value"] == 1000
    assert "total_cost" not in result["custom_fields"]
    assert "projected_margin_pct" not in result["custom_fields"]
    assert "line_items" not in result["custom_fields"]


def test_transform_override_flag_blocks_when_set():
    from app.services.hooks.declarative import OverrideRequiredError, apply_transform

    source = {"custom_fields": {"under_margin": True}}
    block = {"copy_fields": [], "strip_fields": [], "override_flag": "under_margin"}
    with pytest.raises(OverrideRequiredError):
        apply_transform(source, block, override=False)
    # With override=True, no exception.
    result = apply_transform(source, block, override=True)
    assert isinstance(result, dict)


def test_transform_gate_blocks_when_not_won():
    from app.services.hooks.declarative import TransformGateDeniedError, apply_transform

    source = {
        "title": "Deal",
        "custom_fields": {"stage": "negotiation", "value": 10},
    }
    block = {
        "copy_fields": [{"from": "title", "to": "title"}],
        "gate_field": "custom_fields.stage",
        "gate_value": "won",
    }
    with pytest.raises(TransformGateDeniedError):
        apply_transform(source, block)
    # enforce_gate=False bypasses for callers that already checked.
    result = apply_transform(source, block, enforce_gate=False)
    assert result["title"] == "Deal"


def test_transform_gate_passes_when_won():
    from app.services.hooks.declarative import apply_transform, gate_satisfied

    source = {
        "title": "Deal",
        "custom_fields": {"stage": "won", "value": 48_000},
    }
    block = {
        "copy_fields": [
            {"from": "title", "to": "title"},
            {"from": "custom_fields.value", "to": "custom_fields.budget"},
        ],
        "gate_field": "custom_fields.stage",
        "gate_value": "won",
    }
    assert gate_satisfied(source, block) is True
    result = apply_transform(source, block)
    assert result["title"] == "Deal"
    assert result["custom_fields"]["budget"] == 48_000


def test_public_share_gate_passes():
    from app.services.hooks.declarative import apply_public_share_projection

    entry = {
        "title": "Case",
        "body": "Summary",
        "custom_fields": {
            "published": True,
            "client_name": "Contoso",
            "sector": "logistics",
            "extra": "leak",
        },
    }
    block = {
        "gate_field": "published",
        "gate_value": True,
        "projection_fields": [
            "title",
            "body",
            {"field": "client_name", "source": "custom_fields.client_name"},
            {"field": "sector", "source": "custom_fields.sector"},
        ],
    }
    result = apply_public_share_projection(entry, block)
    assert result["title"] == "Case"
    assert result["client_name"] == "Contoso"
    assert "extra" not in result  # whitelist


def test_public_share_gate_blocks_when_not_published():
    from app.services.hooks.declarative import (
        ShareGateDeniedError,
        apply_public_share_projection,
    )

    entry = {"title": "Case", "custom_fields": {"published": False}}
    block = {
        "gate_field": "published",
        "gate_value": True,
        "projection_fields": ["title"],
    }
    with pytest.raises(ShareGateDeniedError):
        apply_public_share_projection(entry, block)


def test_dedup_match_field_normalize():
    from app.services.hooks.declarative import dedup_candidates

    src = {"custom_fields": {"email": "  Maya@Contoso.example  "}}
    block = {
        "match_field_pairs": [
            {
                "source": "custom_fields.email",
                "target": "custom_fields.email",
                "normalize": "lower_strip",
            },
        ],
    }
    targets = [
        {"id": "n.Entry.A", "custom_fields": {"email": "maya@contoso.example"}},
        {"id": "n.Entry.B", "custom_fields": {"email": "other@example.com"}},
    ]
    matches = dedup_candidates(src, targets, block)
    assert [m["id"] for m in matches] == ["n.Entry.A"]
