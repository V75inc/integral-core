"""Filing never silently drops or semantically reinterprets supplied data."""

import pytest

from app.agentive.tooling.stagers_filing import _normalize_agent_fields

SCHEMA = {
    "fields": [
        {"key": "contact_email", "name": "Email address"},
        {"key": "phone", "name": "Telephone"},
    ]
}


def test_schema_keys_and_unambiguous_display_names_only():
    assert _normalize_agent_fields(
        {"Email Address": "test@example.invalid", "phone": ""}, SCHEMA
    ) == {
        "contact_email": "test@example.invalid",
        "phone": "",
    }
    assert _normalize_agent_fields({"phone": None}, SCHEMA) == {"phone": None}


@pytest.mark.parametrize(
    "fields", [{"email": "test@example.invalid"}, {"mobile": "123"}, {"unexpected": 1}]
)
def test_no_global_domain_aliases_or_dropped_unknown_fields(fields):
    with pytest.raises(ValueError, match="unknown"):
        _normalize_agent_fields(fields, SCHEMA)


def test_empty_schema_is_authoritative():
    with pytest.raises(ValueError, match="unknown"):
        _normalize_agent_fields({"email": "test@example.invalid"}, {"fields": []})


def test_ambiguous_schema_names_require_exact_stable_key():
    schema = {
        "fields": [
            {"key": "customer_phone", "name": "Phone"},
            {"key": "supplier_phone", "name": "Phone"},
        ]
    }
    with pytest.raises(ValueError, match="ambiguous"):
        _normalize_agent_fields({"Phone": "123"}, schema)
    assert _normalize_agent_fields({"customer_phone": "123"}, schema) == {
        "customer_phone": "123"
    }


def test_multiple_aliases_cannot_overwrite_one_field():
    with pytest.raises(ValueError, match="Multiple supplied fields"):
        _normalize_agent_fields({"phone": "123", "Telephone": "456"}, SCHEMA)
