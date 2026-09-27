"""A custom-field name is the key, or one exact label. Anything else is refused."""

from types import SimpleNamespace

import pytest

from app.agentive.services.query_spec import _rewrite_entry_field_refs
from app.schemas.query_spec import QuerySpec
from app.services.field_keys import (
    catalog_from_entry_types,
    resolve_field_map,
    resolve_field_reference,
)

_CATALOG = (
    {"key": "value", "label": "Value"},
    {"key": "stage", "label": "Stage"},
)


def test_label_lands_as_the_key():
    """Value is the label. The stored field is value."""
    assert resolve_field_reference("Value", _CATALOG) == "value"
    assert resolve_field_map({"Value": 240000}, _CATALOG) == {"value": 240000}


def test_unknown_name_is_refused():
    """A name that is neither a key nor a unique label does not pass through."""
    with pytest.raises(ValueError, match="not on this entry type"):
        resolve_field_reference("Nope", _CATALOG)


def test_ambiguous_label_is_refused():
    """Two fields titled Amount cannot be guessed."""
    catalog = (
        {"key": "amount", "label": "Price"},
        {"key": "fee", "label": "Price"},
    )
    with pytest.raises(ValueError, match="more than one"):
        resolve_field_reference("Price", catalog)


def test_query_sort_on_an_unknown_key_is_a_validation_error():
    """custom_fields.Nope does not become a null ranking."""
    spec = QuerySpec(
        resource="entry",
        select=["id", "custom_fields.Nope"],
        sort=[{"field": "custom_fields.Nope", "direction": "desc"}],
    )
    with pytest.raises(ValueError, match="Nope"):
        _rewrite_entry_field_refs(spec, list(_CATALOG))


def test_query_sort_label_is_rewritten_before_ranking():
    """custom_fields.Value sorts on the real key."""
    spec = QuerySpec(
        resource="entry",
        select=["id", "custom_fields.Value"],
        sort=[{"field": "custom_fields.Value", "direction": "desc"}],
    )
    _rewrite_entry_field_refs(spec, list(_CATALOG))
    assert spec.sort[0].field == "custom_fields.value"
    assert spec.select[1] == "custom_fields.value"


def test_catalog_reads_entry_type_form_schema():
    """The label comes from the EntryType field, not from the payload."""
    entry_type = SimpleNamespace(
        form_schema={"fields": [{"key": "value", "name": "Value"}]}
    )
    assert catalog_from_entry_types([entry_type]) == [
        {"key": "value", "label": "Value"}
    ]
