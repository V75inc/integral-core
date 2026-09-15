"""Unit tests for Employee-specific validation and ID auto-generation.

Covers:
- Email validation for `work_email` and `personal_email`.
- Phone validation for `phone` and `emergency_contact_phone`.
- Auto-generation of `employee_id` on new employee creation.
"""

from __future__ import annotations

import pytest

from app.exceptions import BadRequestError
from app.models.nodes import Entry, EntryType, Track, Workspace
from app.services.content_profile_compile import _normalize_field_spec
from app.services.content_profile_entry_fields import (
    validate_and_materialize_entry_custom_fields,
)


@pytest.fixture
def employee_runtime_tier():
    return {
        "entry_types": [
            {
                "key": "employee",
                "name": "Employee",
                "fields": [
                    _normalize_field_spec({"key": "employee_id", "type": "text"}),
                    _normalize_field_spec({"key": "work_email", "type": "text"}),
                    _normalize_field_spec({"key": "personal_email", "type": "text"}),
                    _normalize_field_spec({"key": "phone", "type": "text"}),
                    _normalize_field_spec(
                        {"key": "emergency_contact_phone", "type": "text"}
                    ),
                ],
                "base_fields": {},
                "required_tag_groups": [],
            }
        ],
        "views": [],
        "taxonomy": {"tag_groups": []},
        "defaults": {},
    }


@pytest.mark.asyncio
async def test_employee_email_validation(employee_runtime_tier):
    """Emails must match a standard format if provided."""
    ws = await Workspace.create(name="Test Company", kind="organization")
    track = await Track.create(
        title="Employees", owner_id="test-owner", workspace_id=ws.id
    )
    et = await EntryType.create(name="Employee", track_id=track.id)

    # Valid emails
    out, _ = await validate_and_materialize_entry_custom_fields(
        track=track,
        entry_type=et,
        custom_fields={
            "work_email": "john@example.com",
            "personal_email": "john.doe@gmail.co.uk",
        },
        runtime_tier=employee_runtime_tier,
    )
    assert out["work_email"] == "john@example.com"
    assert out["personal_email"] == "john.doe@gmail.co.uk"

    # Invalid work email
    with pytest.raises(BadRequestError, match="must be a valid email address"):
        await validate_and_materialize_entry_custom_fields(
            track=track,
            entry_type=et,
            custom_fields={"work_email": "john.example.com"},
            runtime_tier=employee_runtime_tier,
        )

    # Invalid personal email
    with pytest.raises(BadRequestError, match="must be a valid email address"):
        await validate_and_materialize_entry_custom_fields(
            track=track,
            entry_type=et,
            custom_fields={"personal_email": "john@example"},
            runtime_tier=employee_runtime_tier,
        )


@pytest.mark.asyncio
async def test_employee_phone_validation(employee_runtime_tier):
    """Phones must contain at least 7 digits and consist only of valid characters."""
    ws = await Workspace.create(name="Test Company", kind="organization")
    track = await Track.create(
        title="Employees", owner_id="test-owner", workspace_id=ws.id
    )
    et = await EntryType.create(name="Employee", track_id=track.id)

    # Valid phones
    out, _ = await validate_and_materialize_entry_custom_fields(
        track=track,
        entry_type=et,
        custom_fields={
            "phone": "+1 (555) 019-2834",
            "emergency_contact_phone": "123-4567",
        },
        runtime_tier=employee_runtime_tier,
    )
    assert out["phone"] == "+1 (555) 019-2834"
    assert out["emergency_contact_phone"] == "123-4567"

    # Too short phone
    with pytest.raises(BadRequestError, match="must be a valid phone number"):
        await validate_and_materialize_entry_custom_fields(
            track=track,
            entry_type=et,
            custom_fields={"phone": "123456"},
            runtime_tier=employee_runtime_tier,
        )

    # Invalid characters
    with pytest.raises(BadRequestError, match="must be a valid phone number"):
        await validate_and_materialize_entry_custom_fields(
            track=track,
            entry_type=et,
            custom_fields={"phone": "123-4567 ext 123"},
            runtime_tier=employee_runtime_tier,
        )


@pytest.mark.asyncio
async def test_employee_id_auto_generation(employee_runtime_tier):
    """Employee ID must auto-generate sequentially if not specified."""
    ws = await Workspace.create(name="Test Company", kind="organization")
    track = await Track.create(
        title="Employees", owner_id="test-owner", workspace_id=ws.id
    )
    et = await EntryType.create(name="Employee", track_id=track.id)

    # 1. No existing employees -> should default to '0001'
    out1, _ = await validate_and_materialize_entry_custom_fields(
        track=track,
        entry_type=et,
        custom_fields={},
        runtime_tier=employee_runtime_tier,
    )
    assert out1["employee_id"] == "0001"

    # Create one employee entry with id '0001'
    e1 = await Entry.create(
        track_id=track.id,
        type_id=et.id,
        title="Employee 1",
        author_id="test-owner",
        custom_fields={"employee_id": "0001"},
    )

    # 2. One existing employee with ID '0001' -> should generate '0002'
    out2, _ = await validate_and_materialize_entry_custom_fields(
        track=track,
        entry_type=et,
        custom_fields={},
        runtime_tier=employee_runtime_tier,
    )
    assert out2["employee_id"] == "0002"

    # Create another employee entry with id '0015' (gap)
    e2 = await Entry.create(
        track_id=track.id,
        type_id=et.id,
        title="Employee 2",
        author_id="test-owner",
        custom_fields={"employee_id": "0015"},
    )

    # 3. Existing employees have IDs '0001' and '0015' -> should generate '0016'
    out3, _ = await validate_and_materialize_entry_custom_fields(
        track=track,
        entry_type=et,
        custom_fields={},
        runtime_tier=employee_runtime_tier,
    )
    assert out3["employee_id"] == "0016"

    # 4. User specified ID -> should not overwrite
    out4, _ = await validate_and_materialize_entry_custom_fields(
        track=track,
        entry_type=et,
        custom_fields={"employee_id": "9999"},
        runtime_tier=employee_runtime_tier,
    )
    assert out4["employee_id"] == "9999"


@pytest.fixture
def onboarding_runtime_tier():
    return {
        "entry_types": [
            {
                "key": "onboarding_form",
                "name": "Onboarding Form",
                "fields": [
                    _normalize_field_spec({"key": "personal_email", "type": "text"}),
                    _normalize_field_spec({"key": "phone", "type": "text"}),
                    _normalize_field_spec(
                        {"key": "emergency_contact_phone", "type": "text"}
                    ),
                ],
                "base_fields": {},
                "required_tag_groups": [],
            }
        ],
        "views": [],
        "taxonomy": {"tag_groups": []},
        "defaults": {},
    }


@pytest.mark.asyncio
async def test_onboarding_fields_validation(onboarding_runtime_tier):
    """Onboarding form should validate email and phone formats properly."""
    ws = await Workspace.create(name="Test Company", kind="organization")
    track = await Track.create(
        title="Employee Onboarding", owner_id="test-owner", workspace_id=ws.id
    )
    et = await EntryType.create(name="Onboarding Form", track_id=track.id)

    # Valid values
    out, _ = await validate_and_materialize_entry_custom_fields(
        track=track,
        entry_type=et,
        custom_fields={
            "personal_email": "jane@example.com",
            "phone": "555-1234",
            "emergency_contact_phone": "+1-555-987-6543",
        },
        runtime_tier=onboarding_runtime_tier,
    )
    assert out["personal_email"] == "jane@example.com"
    assert out["phone"] == "555-1234"
    assert out["emergency_contact_phone"] == "+1-555-987-6543"

    # Invalid email
    with pytest.raises(BadRequestError, match="must be a valid email address"):
        await validate_and_materialize_entry_custom_fields(
            track=track,
            entry_type=et,
            custom_fields={"personal_email": "jane.example.com"},
            runtime_tier=onboarding_runtime_tier,
        )

    # Invalid phone
    with pytest.raises(BadRequestError, match="must be a valid phone number"):
        await validate_and_materialize_entry_custom_fields(
            track=track,
            entry_type=et,
            custom_fields={"phone": "12345"},  # Too short
            runtime_tier=onboarding_runtime_tier,
        )
