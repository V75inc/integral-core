import pytest

from app.services.content_profile_runtime import (
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
