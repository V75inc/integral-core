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


def test_sdk_optimistic_update_signature_matches_runtime() -> None:
    import inspect

    from integral_sdk.context import OperationContext as PublicContext

    from app.services.app_operations.context import OperationContext as CoreContext

    public = inspect.signature(PublicContext.update_entry_fields)
    runtime = inspect.signature(CoreContext.update_entry_fields)
    assert list(public.parameters) == list(runtime.parameters)
    for name, parameter in public.parameters.items():
        assert parameter.kind == runtime.parameters[name].kind
        assert parameter.default == runtime.parameters[name].default
    assert (
        public.parameters["expected_record_revision"].kind
        is inspect.Parameter.KEYWORD_ONLY
    )
