"""The public SDK exposes the WP-02 information vocabulary without Core imports."""

import sys
from pathlib import Path

SDK_ROOT = Path(__file__).resolve().parents[3] / "sdk" / "python"
if str(SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_ROOT))


def test_sdk_exports_information_contract_types() -> None:
    from integral_sdk import FieldDefinition, RecordRevision, RelationDefinition

    assert FieldDefinition.__name__ == "FieldDefinition"
    assert RecordRevision.__name__ == "RecordRevision"
    assert RelationDefinition.__name__ == "RelationDefinition"
