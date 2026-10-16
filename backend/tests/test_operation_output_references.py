"""Bounded dependency inspection for generic operation result payloads."""

import pytest

pytestmark = pytest.mark.unit


def collect(value):
    from app.services.app_operations.output_references import output_references

    return output_references(value)


def test_nested_canonical_and_typed_references_are_combined():
    assert collect(
        {
            "rows": [
                {"kind": "entry", "id": "opaque-entry"},
                {"source": "n.Entry.private"},
            ]
        }
    ) == {
        ("entry", "opaque-entry"),
        ("entry", "n.Entry.private"),
    }


@pytest.mark.parametrize("value", [None, {}, False, 0, "invalid"])
def test_present_reference_collection_must_be_a_list(value):
    assert collect({"nested": {"object_refs": value}}) is None


@pytest.mark.parametrize(
    "reference",
    [
        {"kind": "entry", "id": ""},
        {"kind": "entry", "id": 12},
        {"kind": "entry", "id": " spaced "},
        {"kind": "track", "id": "n.Entry.wrong"},
        {"kind": "unknown", "id": "opaque"},
        {},
        None,
    ],
)
def test_malformed_explicit_reference_is_not_an_empty_dependency_set(reference):
    assert collect({"object_refs": [reference]}) is None


def test_domain_identifiers_without_resource_claims_remain_domain_data():
    assert collect({"kind": "invoice", "id": "INV-001", "title": "Invoice"}) == set()


def test_unknown_canonical_kind_refuses_inspection():
    assert collect({"source": "n.UndeclaredResource.secret"}) is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), object()])
def test_non_json_values_refuse_inspection(value):
    assert collect({"value": value}) is None


def test_typed_reference_budget_is_global():
    assert collect([{"kind": "entry", "id": f"opaque-{n}"} for n in range(129)]) is None


def test_cycles_and_depth_are_bounded():
    cyclic = []
    cyclic.append(cyclic)
    assert collect(cyclic) is None
    deep = "value"
    for _ in range(22):
        deep = [deep]
    assert collect(deep) is None


def test_string_and_aggregate_text_are_bounded():
    assert collect("x" * 65537) is None
    assert collect(["x" * 65536] * 17) is None


def test_duplicate_references_share_the_authority_budget():
    assert collect([{"kind": "entry", "id": "n.Entry.same"}] * 100) == {
        ("entry", "n.Entry.same")
    }


@pytest.mark.parametrize("kind", [[], {}, True])
def test_malformed_reference_kind_refuses_without_raising_type_error(kind):
    assert collect({"object_refs": [{"kind": kind, "id": "opaque"}]}) is None


def test_structured_domain_kind_is_not_mistaken_for_a_resource_claim():
    assert collect({"kind": {"category": "invoice"}, "id": "INV-001"}) == set()


def test_one_hundred_standard_object_refs_fit_the_operation_budget():
    from app.schemas.capabilities import ObjectRef

    references = [
        ObjectRef(
            kind="entry",
            id=f"n.Entry.item-{n}",
            workspace_id="n.Workspace.scope",
            app_id="n.WorkspaceApp.scope",
        ).model_dump()
        for n in range(100)
    ]
    observed = collect({"object_refs": references})
    expected = {("entry", f"n.Entry.item-{n}") for n in range(100)} | {
        ("workspace", "n.Workspace.scope"),
        ("app", "n.WorkspaceApp.scope"),
    }
    assert observed == expected


def test_complete_result_metadata_has_the_same_structural_and_text_bounds():
    from app.api.errors import InsufficientPermissionsError
    from app.services.app_operations.result_access import _dependencies

    with pytest.raises(InsufficientPermissionsError):
        _dependencies({"output": {}, "evidence": {"audit_correlation_id": "x" * 65537}})


def test_complete_standard_reference_envelope_is_bounded_without_losing_metadata():
    from app.schemas.capabilities import ObjectRef
    from app.services.app_operations.result_access import _dependencies

    references = [
        ObjectRef(
            kind="entry",
            id=f"n.Entry.item-{n}",
            workspace_id="n.Workspace.scope",
            app_id="n.WorkspaceApp.scope",
        ).model_dump()
        for n in range(100)
    ]
    result = {
        "output": {"object_refs": references},
        "object_refs": references,
        "evidence": {"object_refs": references},
    }
    inspected, owning = _dependencies(result)
    assert len(inspected) == 102
    assert len(owning) == 100


def test_canonical_auth_object_reference_is_not_an_empty_dependency_set():
    assert collect({"id": "o.User.private-user", "entity": "User"}) == {
        ("user", "o.User.private-user")
    }


def test_unsupported_core_object_family_cannot_bypass_reference_inspection():
    assert collect({"source": "o.UserModelCredential.private-credential"}) is None
