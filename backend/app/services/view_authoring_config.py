"""Unambiguous authoring shorthand for the persisted View contract.

Keep this adapter shared by design capture and staged writes. It changes
representation only; the normal compiler remains the validation authority.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def adapt_view_config(view_type: str, config: Any) -> Any:
    """Copy equivalent column and sort representations without guessing values."""
    if not isinstance(config, dict):
        return config
    config = deepcopy(config)
    if isinstance(config.get("sort"), dict):
        config["sort"] = [config["sort"]]
    if str(view_type).lower() in {"kanban", "board"}:
        columns = config.get("kanban_columns")
        if isinstance(columns, list):
            adapted = []
            for column in columns:
                if isinstance(column, str) and column.strip():
                    column = {"key": column, "label": column}
                elif isinstance(column, dict) and "key" not in column:
                    value = column.get("value")
                    if isinstance(value, str) and value.strip():
                        column = {**column, "key": value}
                        column.pop("value")
                adapted.append(column)
            config["kanban_columns"] = adapted
    return config
