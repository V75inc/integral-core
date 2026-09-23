"""Kanban view config normalization — group_by must not clobber profile status fields."""

from app.services.operational_model_compile import (
    KANBAN_STAGE_GROUP_BY,
    normalize_view_config,
)


def test_kanban_preserves_custom_fields_status_group_by():
    cfg = normalize_view_config(
        "kanban",
        {
            "group_by": "custom_fields.status",
            "kanban_columns": [
                {"key": "open", "label": "Open"},
                {"key": "paid", "label": "Paid"},
            ],
        },
    )
    assert cfg["group_by"] == "custom_fields.status"


def test_kanban_defaults_bare_status_to_kanban_stage():
    cfg = normalize_view_config("kanban", {"group_by": "status"})
    assert cfg["group_by"] == KANBAN_STAGE_GROUP_BY


def test_kanban_defaults_empty_group_by_to_kanban_stage():
    cfg = normalize_view_config("kanban", {})
    assert cfg["group_by"] == KANBAN_STAGE_GROUP_BY
