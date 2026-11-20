"""Explicit field formats and absence of app-specific ID synthesis."""

from __future__ import annotations

import pytest

from app.exceptions import BadRequestError
from app.models.nodes import EntryType, Track, Workspace
from app.services.operational_model_compile import _normalize_field_spec
from app.services.operational_model_entry_fields import (
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
                    _normalize_field_spec(
                        {
                            "key": "work_email",
                            "type": "text",
                            "validation_format": "email",
                        }
                    ),
                    _normalize_field_spec(
                        {
                            "key": "personal_email",
                            "type": "text",
                            "validation_format": "email",
                        }
                    ),
                    _normalize_field_spec(
                        {"key": "phone", "type": "text", "validation_format": "phone"}
                    ),
                    _normalize_field_spec(
                        {
                            "key": "emergency_contact_phone",
                            "type": "text",
                            "validation_format": "phone",
                        }
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
async def test_core_does_not_invent_a_domain_identifier(employee_runtime_tier):
    """Only application policy can allocate a business identifier."""
    ws = await Workspace.create(name="Test Company", kind="organization")
    track = await Track.create(
        title="Employees", owner_id="test-owner", workspace_id=ws.id
    )
    et = await EntryType.create(name="Employee", track_id=track.id)
    out, _ = await validate_and_materialize_entry_custom_fields(
        track=track, entry_type=et, custom_fields={}, runtime_tier=employee_runtime_tier
    )
    assert not out.get("employee_id")


@pytest.fixture
def onboarding_runtime_tier():
    return {
        "entry_types": [
            {
                "key": "onboarding_form",
                "name": "Onboarding Form",
                "fields": [
                    _normalize_field_spec(
                        {
                            "key": "personal_email",
                            "type": "text",
                            "validation_format": "email",
                        }
                    ),
                    _normalize_field_spec(
                        {"key": "phone", "type": "text", "validation_format": "phone"}
                    ),
                    _normalize_field_spec(
                        {
                            "key": "emergency_contact_phone",
                            "type": "text",
                            "validation_format": "phone",
                        }
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


@pytest.mark.asyncio
async def test_text_format_does_not_follow_the_field_name(employee_runtime_tier):
    ws = await Workspace.create(name="Test Organization", kind="organization")
    track = await Track.create(
        title="Contacts", owner_id="test-owner", workspace_id=ws.id
    )
    et = await EntryType.create(name="Contact", track_id=track.id)
    tier = employee_runtime_tier
    tier["entry_types"][0] = {
        **tier["entry_types"][0],
        "key": "contact",
        "name": "Contact",
        "fields": [
            _normalize_field_spec({"key": "work_email", "type": "text"}),
            _normalize_field_spec(
                {"key": "reply_address", "type": "text", "validation_format": "email"}
            ),
        ],
    }
    out, _ = await validate_and_materialize_entry_custom_fields(
        track=track,
        entry_type=et,
        custom_fields={
            "work_email": "unvalidated notes",
            "reply_address": "reply@example.com",
        },
        runtime_tier=tier,
    )
    assert out["work_email"] == "unvalidated notes"
    with pytest.raises(BadRequestError, match="valid email address"):
        await validate_and_materialize_entry_custom_fields(
            track=track,
            entry_type=et,
            custom_fields={"reply_address": "not-an-email"},
            runtime_tier=tier,
        )
