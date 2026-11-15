"""A record type's one-record flag (``singleton``) survives into the stored schema.

The compiler set it, and create-time enforcement reads it from the stored
form_schema, but the merge copied open_as_page/canvas/create_wizard and not
singleton, so a one-record type (an employer profile) allowed duplicates.
"""

from __future__ import annotations

import pytest

from app.services.operational_model_merge import (
    _form_schema_from_entry_type_spec,
    merge_entry_type_schema_from_spec,
)

pytestmark = pytest.mark.smoke


def test_new_schema_from_spec_keeps_singleton():
    out = _form_schema_from_entry_type_spec(
        {"key": "company_profile", "fields": [], "singleton": True}
    )
    assert out["singleton"] is True


def test_nested_form_schema_spec_keeps_singleton():
    out = _form_schema_from_entry_type_spec(
        {"key": "company_profile", "form_schema": {"fields": []}, "singleton": True}
    )
    assert out["singleton"] is True


def test_spec_without_singleton_stays_many():
    out = _form_schema_from_entry_type_spec({"key": "line", "fields": []})
    assert not out.get("singleton")


def test_library_update_carries_singleton_onto_existing_type():
    current = _form_schema_from_entry_type_spec(
        {"key": "company_profile", "fields": []}
    )
    assert not current.get("singleton")
    desired = _form_schema_from_entry_type_spec(
        {"key": "company_profile", "fields": [], "singleton": True}
    )
    merged, changed = merge_entry_type_schema_from_spec(
        current, desired, spec_key="company_profile"
    )
    assert changed
    assert merged["singleton"] is True
