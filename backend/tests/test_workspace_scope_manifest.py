"""Phase D2 — workspace-scope manifest compiler support.

Exercises ``compile_canonical_manifest`` for ``scope: workspace`` manifests
introduced in Spec §6.4. The workspace bundle nests app sub-manifests (inline
or via ``operational_model_ref``) and may declare cross-app relations spanning multiple
apps within the bundle.

Per plan D2 the schema_version stays at 2 for now (B2 loader transitionally
keeps it at 2 so v2-compiler downstream callers continue to compile cleanly).
"""

import pytest


def test_workspace_scope_manifest_compiles_with_apps():
    from app.services.operational_model_runtime import compile_canonical_manifest

    m = {
        "operational_model_schema_version": 2,
        "scope": "workspace",
        "package": {"name": "ws-demo"},
        "workspace": {
            "apps": [
                {
                    "slug": "crm",
                    "name": "CRM",
                    "profile": {
                        "operational_model_schema_version": 2,
                        "scope": "app",
                        "app": {
                            "tracks": [
                                {
                                    "key": "contacts",
                                    "name": "Contacts",
                                    "entry_types": [
                                        {"key": "contact", "name": "Contact"}
                                    ],
                                }
                            ]
                        },
                    },
                },
            ],
            "cross_app_relations": [],
        },
    }
    out = compile_canonical_manifest(manifest=m)
    assert out["scope"] == "workspace"
    assert len(out["workspace"]["apps"]) == 1


def test_workspace_scope_rejects_empty_apps():
    from app.services.operational_model_runtime import compile_canonical_manifest

    with pytest.raises(Exception, match="apps"):
        compile_canonical_manifest(
            manifest={
                "operational_model_schema_version": 2,
                "scope": "workspace",
                "package": {"name": "empty"},
                "workspace": {"apps": []},
            }
        )


def test_cross_app_relation_target_must_exist_in_apps():
    from app.services.operational_model_runtime import compile_canonical_manifest

    with pytest.raises(Exception, match="cross_app_relations"):
        compile_canonical_manifest(
            manifest={
                "operational_model_schema_version": 2,
                "scope": "workspace",
                "package": {"name": "bad"},
                "workspace": {
                    "apps": [
                        {
                            "slug": "a",
                            "name": "A",
                            "profile": {
                                "operational_model_schema_version": 2,
                                "scope": "app",
                                "app": {"tracks": []},
                            },
                        }
                    ],
                    "cross_app_relations": [
                        {
                            "key": "x",
                            "source": {
                                "app": "a",
                                "track": "t",
                                "entry_type": "e",
                                "field": "f",
                            },
                            "target": {
                                "app": "MISSING",
                                "track": "t",
                                "entry_type": "e",
                            },
                        },
                    ],
                },
            }
        )
