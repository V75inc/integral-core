"""Kanban column keys are merged into workflow select enums at compile time."""

from app.services.content_profile_compile import compile_canonical_manifest


def test_compile_syncs_kanban_columns_into_status_enum():
    manifest = {
        "content_profile_schema_version": 2,
        "scope": "track",
        "package": {"name": "Finance", "slug": "finance-test", "version": "1.0.0"},
        "track": {
            "entry_types": [
                {
                    "key": "qb_invoice",
                    "name": "QB Invoice",
                    "fields": [
                        {
                            "key": "status",
                            "name": "Status",
                            "type": "select",
                            "enum": ["open", "paid"],
                        }
                    ],
                }
            ],
            "views": [
                {
                    "key": "invoices_kanban",
                    "name": "Pipeline",
                    "view_type": "kanban",
                    "entry_type_keys": ["qb_invoice"],
                    "group_by": "custom_fields.status",
                    "kanban_columns": [
                        {"key": "open", "label": "Open"},
                        {"key": "paid", "label": "Paid"},
                        {"key": "overdue", "label": "Overdue"},
                    ],
                }
            ],
        },
    }
    out = compile_canonical_manifest(manifest=manifest)
    fields = out["track"]["entry_types"][0]["fields"]
    status = next(f for f in fields if f["key"] == "status")
    assert status["enum"] == ["open", "paid", "overdue"]
