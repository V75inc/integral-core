"""Field identity must survive Operational Model serialization boundaries."""

from app.services.operational_model_compile import _normalize_field_spec


def test_normalized_field_preserves_explicit_stable_id_across_label_and_key_change():
    original = _normalize_field_spec(
        {"id": "fld-rental-vehicle", "key": "assigned_vehicle", "name": "Vehicle"}
    )
    renamed = _normalize_field_spec(
        {"id": "fld-rental-vehicle", "key": "vehicle", "name": "Assigned car"}
    )

    assert original["id"] == renamed["id"] == "fld-rental-vehicle"
    assert original["key"] != renamed["key"]
    assert original["name"] != renamed["name"]


def test_legacy_field_gets_a_deterministic_compatibility_id():
    first = _normalize_field_spec({"key": "registration", "name": "Registration"})
    second = _normalize_field_spec(
        {"key": "registration", "name": "Vehicle registration"}
    )

    assert first["id"] == second["id"]
    assert first["id"].startswith("legacy-field-")
