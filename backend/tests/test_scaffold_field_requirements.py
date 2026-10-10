"""Approved-design field names survive the prose the model actually writes."""

from app.agentive.tooling.scaffold_build import _approved_field_requirements

_INVENTORY = """
# Inventory Management App

### 1. Inventory Items
- Purpose: Manage products.
- Fields:
  - Item Name (text): Name of the product
  - SKU (text): Stock Keeping Unit (optional, for unique identification)
  - Stock Count (number): Current quantity in stock
  - Description (text): Short description of the item (optional)
  - Item Image (file): Picture of the item
    - unique per store (optional)
- Views:
  - Table
"""


def test_inventory_optional_notes_are_not_part_of_the_field_name():
    required = _approved_field_requirements(
        {
            "proposal": _INVENTORY,
            "acceptance_assertions": [
                'Track "Inventory Items" with fields: Item Name, SKU, Stock Count, Description, Item Image'
            ],
        }
    )
    assert required["inventory items"] == {
        "item name",
        "sku",
        "stock count",
        "description",
        "item image",
    }


def test_track_colon_heading_and_checklist_backup():
    required = _approved_field_requirements(
        {
            "proposal": "## Track: Tasks\n- Fields:\n- Purpose: later (optional)\n",
            "acceptance_assertions": [
                "Tasks track with fields: Title (text), Status (select)"
            ],
        }
    )
    assert required["tasks"] == {"title", "status"}
