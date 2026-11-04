from copy import deepcopy

import pytest

from app.api.errors import BadRequestError
from app.services.ui_definition_activation import validate_ui_only_transition


def contract():
    return {
        "package": {"slug": "records"},
        "app": {
            "operations": [
                {
                    "key": "save",
                    "handler_ref": "tools.records:save",
                    "authorization": {"roles": ["editor"]},
                }
            ],
            "tools": [{"key": "save", "handler_ref": "tools.records:save"}],
            "queries": [{"key": "existing", "handler_ref": "tools.records:read"}],
            "extension_views": [{"key": "isolated", "operations": ["save"]}],
            "tracks": [
                {
                    "key": "records",
                    "entry_types": [
                        {
                            "key": "record",
                            "fields": [{"key": "amount", "type": "number"}],
                            "ui_contributions": [
                                {"placement": "entry_compose", "view": "form"}
                            ],
                        }
                    ],
                    "views": [
                        {
                            "key": "lines",
                            "view_type": "region-system/editable-related-lines",
                            "config": {
                                "persist_tool": "save",
                                "catalog_tool": "catalog",
                                "column_options": {"tax": {"tool": "taxes"}},
                            },
                        }
                    ],
                }
            ],
            "ui_components": [{"key": "editor", "digest": "a" * 64}],
        },
    }


def test_allows_only_ui_and_additive_query_changes_without_mutating_input():
    before = contract()
    snapshot = deepcopy(before)
    after = deepcopy(before)
    after["app"]["ui_components"][0]["digest"] = "b" * 64
    after["app"]["queries"].append(
        {"key": "lookup", "handler_ref": "tools.records:lookup"}
    )
    after["app"]["tracks"][0]["entry_types"][0]["ui_contributions"] = [
        {"component_key": "editor", "owns_field_data": True}
    ]
    config = after["app"]["tracks"][0]["views"][0]["config"]
    config["catalog_query"] = "lookup"
    config["column_options"]["tax"]["query"] = "lookup"
    validate_ui_only_transition(before, after)
    assert before == snapshot and config["catalog_query"] == "lookup"


@pytest.mark.parametrize(
    "change",
    ["roles", "handler", "tool", "field", "query", "remove_query", "bridge", "persist"],
)
def test_rejects_authority_or_existing_contract_changes(change):
    before = contract()
    after = deepcopy(before)
    app = after["app"]
    if change == "roles":
        app["operations"][0]["authorization"]["roles"].append("viewer")
    elif change == "handler":
        app["operations"][0]["handler_ref"] = "tools.other:save"
    elif change == "tool":
        app["tools"][0]["handler_ref"] = "tools.other:save"
    elif change == "field":
        app["tracks"][0]["entry_types"][0]["fields"][0]["type"] = "text"
    elif change == "query":
        app["queries"][0]["handler_ref"] = "tools.other:read"
    elif change == "remove_query":
        app["queries"] = []
    elif change == "bridge":
        app["extension_views"][0]["operations"].append("delete")
    elif change == "persist":
        app["tracks"][0]["views"][0]["config"]["persist_tool"] = "other"
    with pytest.raises(BadRequestError, match="action contract"):
        validate_ui_only_transition(before, after)
