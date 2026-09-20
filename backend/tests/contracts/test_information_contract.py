"""WP-02 contract: field identity survives presentation changes."""

import pytest
from pydantic import ValidationError

from app.contracts.information import (
    FieldDefinition,
    FieldNamespace,
    RecordRevision,
    resolve_field_value,
    schema_revision_from_profile_version,
)


def test_field_identity_is_stable_across_a_label_rename() -> None:
    """A display rename does not change the published field identity."""
    before = FieldDefinition(
        id="fld.vehicle.registration",
        key="registration_number",
        label="Registration number",
        type="text",
        schema_revision=1,
    )
    after = before.model_copy(update={"label": "Licence plate"})

    assert after.id == before.id
    assert after.key == before.key
    assert after.label == "Licence plate"


def test_platform_and_business_fields_are_explicitly_namespaced() -> None:
    """A business status cannot masquerade as an Entry platform attribute."""
    business_status = FieldDefinition(
        id="fld.rental.vehicle_status",
        key="vehicle_status",
        label="Vehicle status",
        type="select",
        namespace=FieldNamespace.BUSINESS,
        schema_revision=1,
    )

    assert business_status.namespace is FieldNamespace.BUSINESS
    assert business_status.key != "status"
    with pytest.raises(ValidationError, match="collides with a platform field"):
        FieldDefinition(
            id="fld.rental.status",
            key="status",
            label="Rental status",
            type="select",
            schema_revision=1,
        )


def test_field_resolution_does_not_guess_from_a_null_business_value() -> None:
    """A null business value never falls through to the platform status."""
    business = FieldDefinition(
        id="fld.rental.workflow_state",
        key="workflow_state",
        label="Workflow state",
        type="select",
        schema_revision=1,
    )
    platform = FieldDefinition(
        id="sys.entry.status",
        key="status",
        label="Record status",
        type="text",
        namespace=FieldNamespace.PLATFORM,
        owner="integral-core",
        schema_revision=1,
    )
    platform_values = {"status": "active"}
    custom_fields = {"workflow_state": None}

    assert (
        resolve_field_value(
            business, platform_values=platform_values, custom_fields=custom_fields
        )
        is None
    )
    assert (
        resolve_field_value(
            platform, platform_values=platform_values, custom_fields=custom_fields
        )
        == "active"
    )


def test_revisions_reject_zero_or_missing_values() -> None:
    """Concurrency and schema checks never accept an implicit revision."""
    with pytest.raises(ValidationError):
        RecordRevision(record_revision=0, schema_revision=1)


def test_schema_revision_uses_profile_publication_version_or_legacy_baseline() -> None:
    assert schema_revision_from_profile_version(3) == 3
    assert schema_revision_from_profile_version(None) == 1
    assert schema_revision_from_profile_version(0) == 1
