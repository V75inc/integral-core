"""Kanban view config normalization — group_by must not clobber profile status fields."""

import pytest

from app.api.errors import BadRequestError
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


@pytest.mark.parametrize(
    ("columns", "message"),
    [
        (["Open"], r"kanban_columns\[0\] must be an object"),
        ([{"label": "Open"}], r"kanban_columns\[0\]\.key must be a non-empty string"),
        ([{"key": "open", "label": 1}], r"kanban_columns\[0\]\.label must be a string"),
        ([{"key": "open"}, {"key": "open"}], "duplicate key"),
    ],
)
def test_kanban_rejects_malformed_columns_before_persistence(columns, message):
    with pytest.raises(BadRequestError, match=message):
        normalize_view_config("kanban", {"kanban_columns": columns})


def test_kanban_normalization_does_not_mutate_caller_config():
    columns = [{"key": " open ", "label": "Open"}]
    normalized = normalize_view_config("kanban", {"kanban_columns": columns})

    assert columns == [{"key": " open ", "label": "Open"}]
    assert normalized["kanban_columns"] == [{"key": "open", "label": "Open"}]


@pytest.mark.parametrize("view_type", ["feed", "kanban", "table"])
def test_tab_order_survives_view_config_normalization(view_type):
    assert normalize_view_config(view_type, {"tab_order": 2})["tab_order"] == 2


@pytest.mark.parametrize("position", [True, 2.5, "2"])
def test_tab_order_rejects_noninteger_values(position):
    with pytest.raises(BadRequestError, match="tab_order must be an integer"):
        normalize_view_config("feed", {"tab_order": position})
