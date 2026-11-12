"""App proposals do not confer physical storage authority."""

from dataclasses import replace

import pytest
from jvspatial import IndexSpec

from app.exceptions import BadRequestError
from app.services.operational_model_compile import _normalize_package_meta
from app.services.storage_indexes.catalog import build_catalog
from app.services.storage_indexes.proposals import approved_specs, normalize_requests


def proposal():
    return normalize_requests(
        [
            {
                "key": "amount",
                "query_key": "orders",
                "track_key": "orders",
                "entry_type_key": "order",
                "fields": [{"field": "amount", "direction": 1}],
            }
        ],
        queries=[{"key": "orders"}],
        tracks=[
            {
                "key": "orders",
                "entry_types": [
                    {"key": "order", "fields": [{"key": "amount", "type": "number"}]}
                ],
            }
        ],
    )


def test_pending_proposal_roundtrip_and_qualified_platform_approval():
    requests = proposal()
    assert requests == normalize_requests(
        requests,
        queries=[{"key": "orders"}],
        tracks=[
            {
                "key": "orders",
                "entry_types": [
                    {"key": "order", "fields": [{"key": "amount", "type": "number"}]}
                ],
            }
        ],
    )
    compiled = {
        "package": {"slug": "example", "version": "1.0.0"},
        "app": {"storage_index_requests": requests},
    }
    assert approved_specs(compiled, set()) == []
    with pytest.raises(ValueError, match="separately qualified"):
        approved_specs(compiled, {"example:amount"})
    spec = IndexSpec(
        "example:amount",
        "app:example",
        "node",
        ("(data #>> '{context,amount}')",),
        version="1.0.0",
        queries=("example:orders",),
    )
    approval = {
        "request": "example:amount",
        "qualification_sha256": "a" * 64,
        "spec": spec.to_dict(),
    }
    assert approved_specs(compiled, {"example:amount"}, approvals=[approval]) == [spec]
    approval["spec"] = replace(spec, required=True, unique=True).to_dict()
    with pytest.raises(ValueError, match="revision"):
        approved_specs(compiled, {"example:amount"}, approvals=[approval])


def test_package_storage_capability_is_versioned():
    assert _normalize_package_meta(
        {"capabilities": [{"type": "storage_index_requests", "config": {"version": 1}}]}
    )["capabilities"][0]["config"] == {"version": 1}
    for config in ({"version": 2}, {"predicate": "true"}):
        with pytest.raises(BadRequestError):
            _normalize_package_meta(
                {"capabilities": [{"type": "storage_index_requests", "config": config}]}
            )


def test_catalog_is_bounded_and_environment_independent(monkeypatch):
    monkeypatch.delenv("INTEGRAL_STORAGE_CATALOG_PROVIDERS", raising=False)
    first = build_catalog()
    monkeypatch.setenv("WORKSPACE_ID", "another-workspace")
    second = build_catalog()
    assert [s.fingerprint for s in first] == [s.fingerprint for s in second]
    assert len({s.fingerprint for s in first}) == len(first)
    assert all(s.queries for s in first if not s.required)


@pytest.mark.parametrize("flag", ["0", "false", "no", "off", "", "garbage"])
def test_managed_mode_cannot_disable_integrity_verification(monkeypatch, flag):
    """Every disabled or ambiguous declaration flag fails before serving."""
    from jvspatial import ManagedSchemaError

    from app.services.storage_indexes.runtime import postgres_kwargs

    monkeypatch.setenv("JVSPATIAL_POSTGRES_INDEX_CREATION", "managed")
    monkeypatch.setenv("JVSPATIAL_AUTO_CREATE_INDEXES", flag)
    with pytest.raises(ManagedSchemaError, match="integrity"):
        postgres_kwargs()


def test_compiler_preserves_pending_storage_requests():
    """A compiled App can round-trip typed proposals without self-approval."""
    from app.services.operational_model_runtime import compile_canonical_manifest

    manifest = {
        "operational_model_schema_version": 2,
        "scope": "app",
        "package": {"slug": "example", "version": "1.0.0"},
        "app": {
            "tracks": [
                {
                    "key": "orders",
                    "name": "Orders",
                    "entry_types": [
                        {
                            "key": "order",
                            "name": "Order",
                            "fields": [{"key": "amount", "type": "number"}],
                        }
                    ],
                }
            ],
            "queries": [
                {
                    "key": "orders",
                    "handler_key": "orders",
                    "input_schema": {"type": "object"},
                    "output_schema": {"type": "object"},
                    "query_template": {
                        "resource": "entry",
                        "select": ["id"],
                        "limit": 20,
                        "cost_ceiling": 100,
                    },
                }
            ],
            "storage_index_requests_version": 1,
            "storage_index_requests": proposal(),
        },
    }
    result = compile_canonical_manifest(manifest=manifest)
    assert result["app"]["storage_index_requests"] == proposal()
    assert (
        compile_canonical_manifest(manifest=result)["app"]["storage_index_requests"]
        == proposal()
    )
