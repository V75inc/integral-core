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
