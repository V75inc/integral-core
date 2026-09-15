"""Phase 10 Plan 10-03 — manifest v2 compiler test suite.

Covers the v2 compiler extensions per RESEARCH §"Architectural Decisions" Decision 2
(pluggable section parsers) and §"Open Questions" Q9 resolution.

Tests:
- v1 manifest rejection with structured upgrade-path message
- 6 new section parsers (skills, agents, settings_schema, seeds, permissions,
  requires_apps) per `app_bundles_v1.md` §4.2
- `is_public_catalog` kwarg gates `kind: custom` skills at compile time
- track-scope manifests still compile under v2
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

import pytest

from app.exceptions import (
    BadRequestError,
    ContentProfileV1RejectedError,
    ContentProfileValidationError,
)
from app.services.content_profile_runtime import (
    compile_canonical_manifest,
    invalidate_manifest_cache,
)

V2_MANIFEST_BASE: Dict[str, Any] = {
    "content_profile_schema_version": 2,
    "scope": "app",
    "package": {"name": "test_app", "version": "1.0.0"},
    "app": {},
}


V2_TRACK_MANIFEST_BASE: Dict[str, Any] = {
    "content_profile_schema_version": 2,
    "scope": "track",
    "package": {"name": "test_track", "version": "1.0.0"},
    "track": {
        "entry_types": [],
        "views": [],
        "taxonomy": {"tag_groups": []},
    },
}


def _merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-copy ``base`` and shallow-merge top-level keys with ``overlay``."""
    out = deepcopy(base)
    for k, v in overlay.items():
        if k == "app" and isinstance(v, dict):
            out["app"] = {**out.get("app", {}), **v}
        elif k == "track" and isinstance(v, dict):
            out["track"] = {**out.get("track", {}), **v}
        else:
            out[k] = v
    return out


@pytest.fixture(autouse=True)
def _clear_compile_cache():
    """Invalidate the compile cache between tests so changes to manifests hit fresh."""
    invalidate_manifest_cache()
    yield
    invalidate_manifest_cache()


# ---------------------------------------------------------------------------
# v1 rejection
# ---------------------------------------------------------------------------


def test_v1_manifest_rejected_with_upgrade_message():
    """v1 manifest input raises ContentProfileV1RejectedError carrying the
    'manifest v1 no longer supported' upgrade-path message."""
    raw = {
        "content_profile_schema_version": 1,
        "scope": "track",
        "track": {"entry_types": [], "views": [], "taxonomy": {"tag_groups": []}},
    }
    with pytest.raises(ContentProfileV1RejectedError) as excinfo:
        compile_canonical_manifest(manifest=raw)
    msg = str(
        excinfo.value.message if hasattr(excinfo.value, "message") else excinfo.value
    )
    assert "manifest v1 no longer supported" in msg.lower()
    # subclass relationship
    assert isinstance(excinfo.value, ContentProfileValidationError)
    assert isinstance(excinfo.value, BadRequestError)


def test_v1_manifest_rejection_is_also_caught_by_base_error():
    """Existing BadRequestError-catching call sites still observe the v1 reject."""
    raw = {"content_profile_schema_version": 1, "scope": "track"}
    with pytest.raises(BadRequestError):
        compile_canonical_manifest(manifest=raw)


# ---------------------------------------------------------------------------
# Minimal v2 compile
# ---------------------------------------------------------------------------


def test_v2_manifest_with_no_app_sections_compiles():
    """A minimal v2 app-scoped manifest compiles to a canonical shape with empty
    section defaults."""
    raw = deepcopy(V2_MANIFEST_BASE)
    out = compile_canonical_manifest(manifest=raw)
    assert out["content_profile_schema_version"] == 2
    assert out["scope"] == "app"
    assert "app" in out


def test_track_scope_compiles_with_v2_schema_version():
    """Track-scope manifest at schema_version: 2 compiles cleanly."""
    raw = deepcopy(V2_TRACK_MANIFEST_BASE)
    out = compile_canonical_manifest(manifest=raw)
    assert out["content_profile_schema_version"] == 2
    assert out["scope"] == "track"


# ---------------------------------------------------------------------------
# Skills section
# ---------------------------------------------------------------------------


def test_skills_section_parsed():
    """Declared declarative skill appears normalized in out['app']['skills']."""
    raw = _merge(
        V2_MANIFEST_BASE,
        {
            "app": {
                "skills": [
                    {
                        "key": "carousel_drafter",
                        "name": "Carousel Drafter",
                        "kind": "declarative",
                        "description": "Drafts carousels",
                        "tools_required": [
                            "integral_create_entry",
                            "integral_patch_entry",
                        ],
                        "prompt_template": "skills/carousel_drafter/PROMPT.md",
                        "parameters": {
                            "count": {"type": "integer", "default": 5},
                        },
                    }
                ]
            }
        },
    )
    out = compile_canonical_manifest(manifest=raw)
    skills = out["app"]["skills"]
    assert len(skills) == 1
    sk = skills[0]
    assert sk["key"] == "carousel_drafter"
    assert sk["kind"] == "declarative"
    assert sk["prompt_template"] == "skills/carousel_drafter/PROMPT.md"
    assert sk["tools_required"] == [
        "integral_create_entry",
        "integral_patch_entry",
    ]
    # default values
    assert sk.get("private") is False
    assert sk.get("trust_tier") == "untrusted"


def test_skills_declarative_requires_prompt_template():
    """Declarative skill missing prompt_template raises validation."""
    raw = _merge(
        V2_MANIFEST_BASE,
        {
            "app": {
                "skills": [
                    {
                        "key": "missing_prompt",
                        "kind": "declarative",
                    }
                ]
            }
        },
    )
    with pytest.raises(BadRequestError):
        compile_canonical_manifest(manifest=raw)


def test_skills_custom_requires_handler_ref():
    """Custom-kind skill missing handler_ref raises validation."""
    raw = _merge(
        V2_MANIFEST_BASE,
        {
            "app": {
                "skills": [
                    {"key": "needs_handler", "kind": "custom"},
                ]
            }
        },
    )
    with pytest.raises(BadRequestError):
        compile_canonical_manifest(manifest=raw)


# ---------------------------------------------------------------------------
# Agents section
# ---------------------------------------------------------------------------


def test_agents_section_parsed():
    """Agent referencing a declared skill normalizes cleanly."""
    raw = _merge(
        V2_MANIFEST_BASE,
        {
            "app": {
                "skills": [
                    {
                        "key": "carousel_drafter",
                        "kind": "declarative",
                        "prompt_template": "x.md",
                    }
                ],
                "agents": [
                    {
                        "key": "drafter_agent",
                        "name": "Drafter Agent",
                        "persona_ref": "agents/drafter.yaml",
                        "skills": ["carousel_drafter"],
                        "scope": "app",
                    }
                ],
            }
        },
    )
    out = compile_canonical_manifest(manifest=raw)
    agents = out["app"]["agents"]
    assert len(agents) == 1
    ag = agents[0]
    assert ag["key"] == "drafter_agent"
    assert ag["skills"] == ["carousel_drafter"]
    assert ag["scope"] == "app"
    assert ag.get("staging") == "required"  # default


def test_agents_section_rejects_missing_skill_reference():
    """Agent referencing a skill key that was not declared raises validation."""
    raw = _merge(
        V2_MANIFEST_BASE,
        {
            "app": {
                "skills": [],
                "agents": [
                    {
                        "key": "bad_agent",
                        "name": "Bad",
                        "persona_ref": "a.yaml",
                        "skills": ["nonexistent_skill"],
                        "scope": "app",
                    }
                ],
            }
        },
    )
    with pytest.raises(BadRequestError):
        compile_canonical_manifest(manifest=raw)


# ---------------------------------------------------------------------------
# Settings schema section
# ---------------------------------------------------------------------------


def test_settings_schema_section_parsed():
    """Declared JSON Schema round-trips into out['app']['settings_schema']."""
    schema = {
        "type": "object",
        "properties": {
            "publish_cadence": {
                "type": "string",
                "enum": ["weekly", "daily"],
                "default": "weekly",
                "title": "Publish cadence",
            }
        },
    }
    raw = _merge(V2_MANIFEST_BASE, {"app": {"settings_schema": schema}})
    out = compile_canonical_manifest(manifest=raw)
    assert out["app"]["settings_schema"] == schema


def test_settings_schema_rejects_non_object_type():
    """A settings_schema with type != 'object' raises validation."""
    raw = _merge(
        V2_MANIFEST_BASE,
        {"app": {"settings_schema": {"type": "array"}}},
    )
    with pytest.raises(BadRequestError):
        compile_canonical_manifest(manifest=raw)


# ---------------------------------------------------------------------------
# Seeds section
# ---------------------------------------------------------------------------


def test_seeds_section_parsed():
    """Declared seeds list appears in out['app']['seeds']."""
    raw = _merge(
        V2_MANIFEST_BASE,
        {
            "app": {
                "seeds": [
                    {
                        "track": "source_material",
                        "entries": [
                            {"title": "Hello", "body": "world"},
                            {"title": "Two", "body": ""},
                        ],
                    }
                ]
            }
        },
    )
    out = compile_canonical_manifest(manifest=raw)
    seeds = out["app"]["seeds"]
    assert len(seeds) == 1
    assert seeds[0]["track"] == "source_material"
    assert len(seeds[0]["entries"]) == 2


# ---------------------------------------------------------------------------
# Permissions section
# ---------------------------------------------------------------------------


def test_permissions_section_parsed():
    """install_requires_workspace_role + default_app_role normalize with defaults."""
    raw = _merge(
        V2_MANIFEST_BASE,
        {
            "app": {
                "permissions": {
                    "install_requires_workspace_role": "admin",
                    "default_app_role": "viewer",
                }
            }
        },
    )
    out = compile_canonical_manifest(manifest=raw)
    perms = out["app"]["permissions"]
    assert perms["install_requires_workspace_role"] == "admin"
    assert perms["default_app_role"] == "viewer"


def test_permissions_section_defaults_to_member():
    """Empty permissions block defaults to install=member + default_app_role=member."""
    raw = _merge(V2_MANIFEST_BASE, {"app": {"permissions": {}}})
    out = compile_canonical_manifest(manifest=raw)
    perms = out["app"]["permissions"]
    assert perms["install_requires_workspace_role"] == "member"
    assert perms["default_app_role"] == "member"


# ---------------------------------------------------------------------------
# requires_apps section
# ---------------------------------------------------------------------------


def test_requires_apps_section_parsed():
    """Hard + soft App dependencies appear with optional flags correctly set."""
    raw = _merge(
        V2_MANIFEST_BASE,
        {
            "app": {
                "requires_apps": [
                    {
                        "key": "hr_app",
                        "min_version": "1.0.0",
                        "optional": False,
                        "reason": "cross-app refs",
                    },
                    {
                        "key": "notifications_app",
                        "optional": True,
                        "reason": "optional surface",
                    },
                ]
            }
        },
    )
    out = compile_canonical_manifest(manifest=raw)
    deps = out["app"]["requires_apps"]
    assert len(deps) == 2
    hard = next(d for d in deps if d["key"] == "hr_app")
    soft = next(d for d in deps if d["key"] == "notifications_app")
    assert hard["optional"] is False
    assert hard["min_version"] == "1.0.0"
    assert soft["optional"] is True
    # min_version default applied
    assert soft["min_version"] == "0.0.0"


def test_requires_apps_requires_key():
    """A requires_apps entry missing 'key' raises validation."""
    raw = _merge(
        V2_MANIFEST_BASE,
        {"app": {"requires_apps": [{"optional": True}]}},
    )
    with pytest.raises(BadRequestError):
        compile_canonical_manifest(manifest=raw)


# ---------------------------------------------------------------------------
# is_public_catalog gate
# ---------------------------------------------------------------------------


def test_custom_skill_rejected_when_public_catalog_flag_set():
    """is_public_catalog=True + a kind:custom skill raises validation."""
    raw = _merge(
        V2_MANIFEST_BASE,
        {
            "app": {
                "skills": [
                    {
                        "key": "private_logic",
                        "kind": "custom",
                        "handler_ref": "module.fn",
                    }
                ]
            }
        },
    )
    with pytest.raises(BadRequestError):
        compile_canonical_manifest(manifest=raw, is_public_catalog=True)


def test_custom_skill_accepted_when_public_catalog_flag_unset():
    """Same custom-kind manifest compiles when is_public_catalog is unset (default False)."""
    raw = _merge(
        V2_MANIFEST_BASE,
        {
            "app": {
                "skills": [
                    {
                        "key": "private_logic",
                        "kind": "custom",
                        "handler_ref": "module.fn",
                    }
                ]
            }
        },
    )
    out = compile_canonical_manifest(manifest=raw)
    assert out["app"]["skills"][0]["kind"] == "custom"
    assert out["app"]["skills"][0]["handler_ref"] == "module.fn"


# ---------------------------------------------------------------------------
# Cross-App relation field extensions (Phase 10 Plan 10-06 APP-CROSS-RELATIONS-01)
# ---------------------------------------------------------------------------


def _cross_app_field_manifest(**relation_overrides: Any) -> Dict[str, Any]:
    """Helper: build an app-scope manifest with a single cross-App relation field."""
    relation_spec = {
        "target": "entry",
        "many": True,
        "target_app": "hr_app",
        "allow_cross_app": True,
        "label_field": "title",
        "on_target_uninstall": "block",
        "resolution": "workspace",
    }
    relation_spec.update(relation_overrides)
    return _merge(
        V2_MANIFEST_BASE,
        {
            "app": {
                "tracks": [
                    {
                        "key": "pay_run",
                        "name": "Pay Run",
                        "entry_types": [
                            {
                                "key": "pay_run_entry",
                                "name": "Pay Run Entry",
                                "fields": [
                                    {
                                        "key": "employees",
                                        "name": "Employees",
                                        "type": "relation",
                                        "relation": relation_spec,
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        },
    )


def test_cross_app_relation_field_compiles_with_full_spec():
    """target_app + allow_cross_app + label_field + on_target_uninstall + resolution all carry through."""
    raw = _cross_app_field_manifest()
    out = compile_canonical_manifest(manifest=raw)
    rel = out["app"]["tracks"][0]["entry_types"][0]["fields"][0]["relation"]
    assert rel["target_app"] == "hr_app"
    assert rel["allow_cross_app"] is True
    assert rel["label_field"] == "title"
    assert rel["on_target_uninstall"] == "block"
    assert rel["resolution"] == "workspace"


def test_cross_app_relation_target_app_without_allow_cross_app_rejected():
    """target_app set without allow_cross_app=True is a compile error."""
    raw = _cross_app_field_manifest(allow_cross_app=False)
    with pytest.raises(BadRequestError):
        compile_canonical_manifest(manifest=raw)


def test_cross_app_relation_invalid_on_target_uninstall_rejected():
    """on_target_uninstall must be 'block' | 'null' | 'archive_self'."""
    raw = _cross_app_field_manifest(on_target_uninstall="cascade_delete")
    with pytest.raises(BadRequestError):
        compile_canonical_manifest(manifest=raw)


def test_cross_app_relation_invalid_resolution_rejected():
    """resolution must be 'workspace' or 'instance:<id>'."""
    raw = _cross_app_field_manifest(resolution="random_token")
    with pytest.raises(BadRequestError):
        compile_canonical_manifest(manifest=raw)


def test_cross_app_relation_instance_resolution_requires_id():
    """resolution='instance:' (empty id after colon) is rejected."""
    raw = _cross_app_field_manifest(resolution="instance:")
    with pytest.raises(BadRequestError):
        compile_canonical_manifest(manifest=raw)


def test_cross_app_relation_archive_self_uninstall_compiles():
    """on_target_uninstall='archive_self' is accepted."""
    raw = _cross_app_field_manifest(on_target_uninstall="archive_self")
    out = compile_canonical_manifest(manifest=raw)
    rel = out["app"]["tracks"][0]["entry_types"][0]["fields"][0]["relation"]
    assert rel["on_target_uninstall"] == "archive_self"


def test_cross_app_relation_instance_resolution_compiles():
    """resolution='instance:<app_id>' is accepted (pinned install)."""
    raw = _cross_app_field_manifest(resolution="instance:app_42")
    out = compile_canonical_manifest(manifest=raw)
    rel = out["app"]["tracks"][0]["entry_types"][0]["fields"][0]["relation"]
    assert rel["resolution"] == "instance:app_42"


def test_intra_app_relation_field_still_compiles_with_no_cross_app_fields():
    """A relation field with NO target_app stays compatible — defaults carry."""
    raw = _merge(
        V2_MANIFEST_BASE,
        {
            "app": {
                "tracks": [
                    {
                        "key": "t",
                        "name": "T",
                        "entry_types": [
                            {
                                "key": "et",
                                "name": "ET",
                                "fields": [
                                    {
                                        "key": "links",
                                        "name": "Links",
                                        "type": "relation",
                                        "relation": {"target": "entry", "many": True},
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        },
    )
    out = compile_canonical_manifest(manifest=raw)
    rel = out["app"]["tracks"][0]["entry_types"][0]["fields"][0]["relation"]
    assert rel["target_app"] == ""
    assert rel["allow_cross_app"] is False
    assert rel["on_target_uninstall"] == "block"
    assert rel["resolution"] == "workspace"


# ---------------------------------------------------------------------------
# suppress_feed_fallback — must survive a second compile pass
#
# An app.tracks[] track's own attached ContentProfile is materialized by
# taking ITS compiled output tier, stripping a few keys
# (content_profile_merge._track_spec_to_library_manifest_dict), wrapping it
# as a fresh scope: "track" manifest, and compiling THAT (content_profile_
# merge.apply_space_track_spec_to_track -> merge_library_manifest_into_
# content_profile). suppress_feed_fallback must not be a one-shot flag that
# only the first compile pass honors.
# ---------------------------------------------------------------------------


def _track_with_one_view(*, suppress: bool | None) -> Dict[str, Any]:
    track: Dict[str, Any] = {
        "key": "settings",
        "name": "Settings",
        "entry_types": [{"key": "general", "name": "General", "fields": []}],
        "views": [
            {
                "key": "hub",
                "name": "Hub",
                "view_type": "table",
                "entry_type_keys": ["general"],
            }
        ],
    }
    if suppress is not None:
        track["suppress_feed_fallback"] = suppress
    return track


def test_suppress_feed_fallback_true_omits_feed_view():
    """suppress_feed_fallback: true leaves the declared views untouched."""
    raw = _merge(
        V2_MANIFEST_BASE, {"app": {"tracks": [_track_with_one_view(suppress=True)]}}
    )
    out = compile_canonical_manifest(manifest=raw)
    track = out["app"]["tracks"][0]
    assert [v["key"] for v in track["views"]] == ["hub"]
    assert track["suppress_feed_fallback"] is True


def test_feed_fallback_still_added_when_not_suppressed():
    """suppress_feed_fallback: false keeps the normal fallback behavior."""
    raw = _merge(
        V2_MANIFEST_BASE, {"app": {"tracks": [_track_with_one_view(suppress=False)]}}
    )
    out = compile_canonical_manifest(manifest=raw)
    track = out["app"]["tracks"][0]
    assert "feed" in [v["key"] for v in track["views"]]
    assert track["suppress_feed_fallback"] is False


def test_feed_fallback_defaults_to_added_when_flag_omitted():
    """No suppress_feed_fallback key at all behaves the same as false."""
    raw = _merge(
        V2_MANIFEST_BASE, {"app": {"tracks": [_track_with_one_view(suppress=None)]}}
    )
    out = compile_canonical_manifest(manifest=raw)
    track = out["app"]["tracks"][0]
    assert "feed" in [v["key"] for v in track["views"]]


def test_suppress_feed_fallback_survives_a_second_compile_pass():
    """Regression: an app-track's compiled tier, round-tripped through the
    exact shim shape ``_track_spec_to_library_manifest_dict`` builds (strip
    key/name/description/provision_on_create, wrap as scope: "track"), must
    NOT grow a Feed view on the second compile — this is what
    ``apply_space_track_spec_to_track`` does at install/reconcile time for
    every app.tracks[] entry. Found live: a payroll app declaring
    ``suppress_feed_fallback: true`` on its consolidated Settings track
    still showed a visible Feed tab after install."""
    raw = _merge(
        V2_MANIFEST_BASE, {"app": {"tracks": [_track_with_one_view(suppress=True)]}}
    )
    first_pass = compile_canonical_manifest(manifest=raw)
    track_tier = first_pass["app"]["tracks"][0]
    assert [v["key"] for v in track_tier["views"]] == ["hub"]  # sanity: no feed yet

    shim = {
        k: v
        for k, v in track_tier.items()
        if k not in ("provision_on_create", "key", "name", "description")
    }
    second_pass_input = {
        "content_profile_schema_version": 2,
        "scope": "track",
        "package": {"name": "settings", "version": "1.0.0"},
        "track": shim,
    }
    second_pass = compile_canonical_manifest(manifest=second_pass_input)
    assert [v["key"] for v in second_pass["track"]["views"]] == ["hub"], (
        "Feed view re-appeared on the second compile pass — "
        "suppress_feed_fallback did not survive the round-trip"
    )
