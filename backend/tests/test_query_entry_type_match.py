"""Entry-type keys and unknown custom-field filters on integral_query_entries."""

from types import SimpleNamespace

from app.services.agent_insights import (
    _entry_type_matches_filter,
    _unknown_custom_filter_fields,
)


def _pay_calendar():
    return SimpleNamespace(
        name="Pay Calendar",
        form_schema={
            "_manifest_entry_type_key": "pay_calendar",
            "fields": [
                {"key": "cadence"},
                {"key": "anchor_period_start"},
                {"key": "pay_date_offset_days"},
            ],
        },
    )


def _general():
    return SimpleNamespace(
        name="General",
        form_schema={
            "_manifest_entry_type_key": "general",
            "fields": [{"key": "sync_from_hrm"}, {"key": "legal_entity"}],
        },
    )


def test_schema_key_matches_pay_calendar_display_name():
    """A manifest key, display name, or hyphenated slug names the same type."""
    entry_type = _pay_calendar()
    assert _entry_type_matches_filter(entry_type, "pay_calendar")
    assert _entry_type_matches_filter(entry_type, "Pay Calendar")
    assert _entry_type_matches_filter(entry_type, "pay-calendar")


def test_display_name_case_and_trailing_s_still_match():
    """Name matching stays case-insensitive and tolerant of a trailing s."""
    opportunity = SimpleNamespace(name="Opportunity", form_schema={})
    assert _entry_type_matches_filter(opportunity, "opportunity")
    bug = SimpleNamespace(name="Bug", form_schema={})
    assert _entry_type_matches_filter(bug, "bugs")


def test_unknown_custom_field_names_the_real_type_keys():
    """An undeclared filter field is reported with the loaded type keys."""
    report = _unknown_custom_filter_fields(
        {"cal": _pay_calendar(), "gen": _general()},
        ["section"],
    )
    assert report == {
        "fields": ["section"],
        "entry_type_keys": ["pay_calendar", "general"],
    }


def test_declared_field_with_no_matching_row_is_not_unknown():
    """A declared field with no matching row is an empty result, not unknown."""
    assert _unknown_custom_filter_fields({"cal": _pay_calendar()}, ["cadence"]) is None
