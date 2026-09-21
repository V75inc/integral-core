from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.operational_model_runtime import (
    backfill_view_entry_type_constraints_from_manifest,
    materialize_view_config_from_spec,
    normalize_view_config,
)


def test_normalize_view_config_preserves_card_template():
    card_template = {
        "title": "title",
        "subtitle": "{{custom_fields.deal_value}} · {{custom_fields.probability}}%",
        "excerpt_field": "body",
        "excerpt_chars": 60,
        "footer_tags": ["custom_fields.stage"],
    }
    result = normalize_view_config(
        "kanban",
        {
            "kanban_columns": [{"key": "lead", "label": "Lead"}],
            "card_template": card_template,
        },
    )
    assert result["card_template"] == card_template


def test_materialize_view_config_from_spec_preserves_card_template():
    card_template = {
        "title": "title",
        "subtitle": "{{custom_fields.deal_value}} · {{custom_fields.probability}}%",
        "excerpt_field": "body",
        "excerpt_chars": 60,
        "footer_tags": ["custom_fields.stage"],
    }
    result = materialize_view_config_from_spec(
        {
            "key": "pipeline",
            "view_type": "kanban",
            "kanban_columns": [{"key": "lead", "label": "Lead"}],
            "card_template": card_template,
        }
    )
    assert result["card_template"] == card_template


def test_materialize_extension_view_preserves_target_key():
    result = materialize_view_config_from_spec(
        {
            "key": "asset_detail_panel",
            "view_type": "extension_view",
            "extension_view_key": "asset_detail",
        }
    )

    assert result["extension_view_key"] == "asset_detail"


@pytest.mark.asyncio
async def test_legacy_extension_view_recovers_target_key_from_manifest():
    view = SimpleNamespace(
        config={"_manifest_view_key": "asset_detail_panel"},
        name="Asset detail",
        type="extension_view",
        entry_type_keys=["asset"],
        default_entry_type_key="asset",
        save=AsyncMock(),
    )
    tier = {
        "views": [
            {
                "key": "asset_detail_panel",
                "view_type": "extension_view",
                "extension_view_key": "asset_detail",
            }
        ]
    }

    with patch(
        "app.services.operational_model_runtime.resolve_track_runtime_profile",
        new=AsyncMock(return_value=(None, tier, None)),
    ):
        await backfill_view_entry_type_constraints_from_manifest(
            track=SimpleNamespace(), views=[view]
        )

    assert view.config["extension_view_key"] == "asset_detail"
    view.save.assert_awaited_once()
