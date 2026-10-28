"""Runtime-tier specs must not drop fields the EntryType form_schema already has."""

from types import SimpleNamespace

from app.services.operational_model_entry_fields import resolve_entry_type_spec


def test_tier_spec_keeps_form_schema_fields_it_does_not_declare():
    entry_type = SimpleNamespace(
        name="Invoice line",
        form_schema={
            "fields": [
                {"key": "description", "name": "Description", "type": "text"},
                {"key": "quantity", "name": "Quantity", "type": "number"},
                {"key": "net_amount", "name": "Net amount", "type": "number"},
            ]
        },
    )
    # Attached model published before net_amount existed.
    tier = {
        "entry_types": [
            {
                "key": "invoice_line",
                "name": "Invoice line",
                "fields": [
                    {"key": "description", "name": "Description", "type": "text"},
                    {"key": "quantity", "name": "Quantity", "type": "number"},
                ],
            }
        ]
    }

    spec = resolve_entry_type_spec(entry_type, tier)
    keys = [field["key"] for field in spec["fields"]]

    assert keys == ["description", "quantity", "net_amount"]
    # The published tier object itself is left unchanged.
    assert [field["key"] for field in tier["entry_types"][0]["fields"]] == [
        "description",
        "quantity",
    ]


def test_known_composite_uses_the_already_compiled_tier():
    """A composite's scoped registry need not remain active during record writes."""
    from app.models.nodes import EntryType
    from app.services.operational_model_compile import compile_canonical_manifest
    from app.services.operational_model_entry_fields import resolve_entry_type_spec

    fields = [{"key": "cost", "type": "money"}]
    canonical = compile_canonical_manifest(
        manifest={
            "operational_model_schema_version": 2,
            "scope": "track",
            "field_types": [{"key": "money", "base": "number", "config": {}}],
            "track": {
                "entry_types": [{"key": "record", "name": "Record", "fields": fields}]
            },
        }
    )
    entry_type = EntryType(name="Record", form_schema={"fields": fields})
    spec = resolve_entry_type_spec(entry_type, canonical["track"])
    assert spec["fields"][0]["composite"]["base"] == "number"
