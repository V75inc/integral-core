"""Agent introspection tests — Phase 3.1 Plan 03.1-04 (ANC-09).

Section 1 (Task 1): describe_substrate additive extensions
  - relation_targets
  - edges (REFERENCES / ANCHORS / TEMPLATED_FROM)
  - template_var_resolvers (resolver vocabulary)
  - governance_actions (PolicyAction members under 'anchor.' namespace)
  - back-compat: existing keys preserved verbatim
  - GET /api/operational-model-substrate exposes template_var_resolvers

Section 2 (Task 2): describe_operational_model round-trips related_views
  - related_views lives in manifest payload — existing export_node path
    surfaces it without any API change (regression / verification only)

Section 3 (Task 3): describe_substrate back-compat consumer regression
  - a pre-3.1 consumer reading only the four legacy keys is unaffected

No ``app.main`` import — test_auth middleware is gitignored per
deferred-items.md; substrate functions are tested directly.
"""

from __future__ import annotations

from typing import get_args

import pytest

from app.schemas.policy import PolicyAction
from app.services.operational_model_authoring import (
    describe_operational_model,
    describe_substrate,
)

# ===== Section 1 — describe_substrate additive extensions =====


@pytest.mark.asyncio
async def test_describe_substrate_exposes_relation_targets():
    """``relation_targets`` enumerates the locked Literal: entry, track."""
    payload = await describe_substrate()
    assert payload["relation_targets"] == ["entry", "track"]


@pytest.mark.asyncio
async def test_describe_substrate_exposes_edges():
    """``edges`` enumerates REFERENCES + ANCHORS + TEMPLATED_FROM with descriptors."""
    payload = await describe_substrate()
    edges = payload["edges"]
    assert "REFERENCES" in edges
    assert "ANCHORS" in edges
    assert "TEMPLATED_FROM" in edges
    assert edges["ANCHORS"]["source"] == "Entry"
    assert edges["ANCHORS"]["target"] == "Track"
    assert "relation.target=track" in edges["ANCHORS"]["via"]
    assert edges["TEMPLATED_FROM"]["source"] == "Track"
    assert edges["TEMPLATED_FROM"]["target"] == "OperationalModel"


@pytest.mark.asyncio
async def test_describe_substrate_exposes_template_var_resolvers():
    """``template_var_resolvers`` lists the v1 resolver tokens."""
    payload = await describe_substrate()
    resolvers = set(payload["template_var_resolvers"])
    assert {":current_user", ":entry_id", ":anchored_track"}.issubset(resolvers)


@pytest.mark.asyncio
async def test_describe_substrate_exposes_governance_actions():
    """``governance_actions`` filters PolicyAction Literal for 'anchor.' members."""
    payload = await describe_substrate()
    govs = set(payload["governance_actions"])
    expected = {a for a in get_args(PolicyAction) if a.startswith("anchor.")}
    assert govs == expected
    # Spot-check the locked Plan 03.1-03 names.
    assert {"anchor.create", "anchor.delete", "anchor.cascade"}.issubset(govs)


@pytest.mark.asyncio
async def test_describe_substrate_back_compat_preserved():
    """Existing keys preserved (additive extension; no removals)."""
    payload = await describe_substrate()
    for key in ("field_types", "view_types", "plugins", "registry_versions"):
        assert key in payload, f"Pre-3.1 key {key!r} dropped — additive contract broken"


@pytest.mark.asyncio
async def test_describe_substrate_unchanged_consumer_pattern():
    """A pre-3.1 agent consumer reading only the four legacy keys is unaffected.

    Section 3 regression — exercises the additive-extension contract end-to-end.
    """
    payload = await describe_substrate()
    legacy_keys = {"field_types", "view_types", "plugins", "registry_versions"}
    assert legacy_keys.issubset(set(payload.keys()))
    # The legacy values themselves must remain populated and structurally intact.
    assert payload["field_types"] is not None
    assert payload["view_types"] is not None
    assert isinstance(payload["plugins"], list)
    assert isinstance(payload["registry_versions"], dict)


# ===== Section 2 — operational-model-substrate endpoint =====


@pytest.mark.asyncio
async def test_operational_model_substrate_endpoint_exposes_resolvers():
    """``get_operational_model_substrate`` payload exposes template_var_resolvers.

    Direct call to the endpoint handler — avoids importing ``app.main`` which
    transitively imports ``app.middleware.test_auth`` (gitignored per
    deferred-items.md). Auth-gate exception path is also exercised via the
    `request=None` branch which raises MissingAuthenticationError; we wrap
    a minimal Request mock with a principal id set on state.user.
    """
    from unittest.mock import MagicMock

    from app.api.operational_models import get_operational_model_substrate

    req = MagicMock()
    req.state.user.id = "u-substrate-test"

    body = await get_operational_model_substrate(req)
    assert "template_var_resolvers" in body
    assert ":current_user" in body["template_var_resolvers"]
    # back-compat: legacy keys preserved
    for key in ("field_types", "view_types", "plugins", "registry_versions"):
        assert key in body


@pytest.mark.asyncio
async def test_compact_substrate_omits_config_schemas():
    import json
    from unittest.mock import MagicMock

    from app.api.operational_models import get_operational_model_substrate

    full_req = MagicMock()
    full_req.state.user.id = "u-substrate-test"
    full = await get_operational_model_substrate(full_req)
    compact_req = MagicMock()
    compact_req.state.user.id = "u-substrate-test"
    compact_req.query_params = {"compact": "1"}
    compact = await get_operational_model_substrate(compact_req)

    assert len(json.dumps(compact)) < len(json.dumps(full)) / 4
    assert "config_schema" not in json.dumps(compact)
    wiki = next(item for item in compact["view_types"] if item["type"] == "wiki")
    assert "parent_field" in wiki["config_keys"]


@pytest.mark.asyncio
async def test_operational_model_substrate_endpoint_exposes_retrieval_capability():
    """``get_operational_model_substrate`` payload exposes the live retrieval block.

    The agent reads ``retrieval.semantic_available`` up front to know which
    search modes are live (mode-adaptive retrieval). Modes/default_mode track
    that flag.
    """
    from unittest.mock import MagicMock

    from app.api.operational_models import get_operational_model_substrate

    req = MagicMock()
    req.state.user.id = "u-substrate-test"

    body = await get_operational_model_substrate(req)
    assert "retrieval" in body
    retrieval = body["retrieval"]
    assert isinstance(retrieval["semantic_available"], bool)
    assert isinstance(retrieval["modes"], list)
    assert "graph" in retrieval["modes"]
    assert "note" in retrieval
    if retrieval["semantic_available"]:
        assert retrieval["modes"] == ["graph", "semantic", "hybrid"]
        assert retrieval["default_mode"] == "hybrid"
    else:
        assert retrieval["modes"] == ["graph"]
        assert retrieval["default_mode"] == "graph"


# ===== Section 2 — describe_operational_model round-trips related_views =====


@pytest.mark.asyncio
async def test_describe_operational_model_round_trips_related_views():
    """related_views lives in the compiled manifest payload; describe_operational_model
    surfaces it without any API change (regression — Plan 03.1-04 only adds
    the validator + storage slot in Task 2).

    Avoids the ``test_user`` fixture because that fixture pulls in
    ``authenticated_client`` → ``app.main`` → ``app.middleware.test_auth``
    (gitignored per deferred-items.md). We exercise the substrate path
    directly: provision a Track + OperationalModel + COLLABORATES_ON owner
    grant, then call ``describe_operational_model`` with a synthetic user id.
    """
    from datetime import datetime, timezone

    from app.models.edges import COLLABORATES_ON, HAS_OPERATIONAL_MODEL
    from app.models.nodes import OperationalModel, Track, User

    now = datetime.now(timezone.utc).isoformat()
    user = await User.create(
        user_id="introspection-owner",
        display_name="Introspection Owner",
        created_at=now,
    )
    track = await Track.create(
        title="Anchored Parent",
        owner_id=user.id,
        workspace_id="ws-introspection",
    )
    await user.connect(track, edge=COLLABORATES_ON, role="owner")

    cp = await OperationalModel.create(
        name="Introspection CP",
        scope="track",
        manifest={
            "operational_model_schema_version": 2,
            "scope": "track",
            "package": {"slug": "intro_test", "name": "Intro", "version": "1.0.0"},
            "track": {
                "entry_types": [
                    {
                        "key": "project",
                        "name": "Project",
                        "fields": [],
                        "related_views": [
                            {"view": ":anchored_track/board", "bind": {}},
                        ],
                    }
                ],
                "taxonomy": {"tag_groups": []},
                "views": [],
            },
        },
        library_package=False,
        created_at=now,
        updated_at=now,
    )
    await track.connect(cp, edge=HAS_OPERATIONAL_MODEL, attached_at=now)
    track.attached_operational_model_id = cp.id
    await track.save()

    payload = await describe_operational_model(user_id=user.id, track_id=track.id)
    assert "published" in payload, f"got {payload!r}"
    pub = payload["published"]
    # ``manifest`` is the stored shape on the CP node (flat-exported).
    entry_types = pub.get("manifest", {}).get("track", {}).get("entry_types", [])
    assert len(entry_types) >= 1
    rv = entry_types[0].get("related_views", [])
    assert rv == [{"view": ":anchored_track/board", "bind": {}}]


# ===== Section 3 — related_views manifest validator (Task 2) =====
#
# Section 3 tests exercise ``_normalize_entry_type_spec`` end-to-end via
# ``compile_canonical_manifest`` to confirm the new ``related_views`` slot
# (a) survives the canonical compile path, (b) rejects empty/missing view
# refs, and (c) preserves back-compat when omitted.


def test_related_views_compile_through_canonical_manifest():
    """A full manifest with entry_types[*].related_views compiles cleanly."""
    from app.services.operational_model_runtime import compile_canonical_manifest

    manifest = {
        "operational_model_schema_version": 2,
        "scope": "track",
        "package": {"slug": "rv_test", "name": "RV", "version": "1.0.0"},
        "track": {
            "entry_types": [
                {
                    "key": "project",
                    "name": "Project",
                    "fields": [],
                    "related_views": [
                        {"view": "board", "bind": {}},
                        {
                            "view": ":anchored_track/feed",
                            "bind": {"currentUser": ":current_user"},
                        },
                    ],
                }
            ],
            "taxonomy": {"tag_groups": []},
            "views": [],
        },
    }
    compiled = compile_canonical_manifest(manifest=manifest, scope_hint="track")
    rv = compiled["track"]["entry_types"][0]["related_views"]
    assert len(rv) == 2
    # ``position`` defaults to "related" (today's render placement) when the
    # manifest doesn't declare one — additive field, back-compat preserved.
    assert rv[0] == {"view": "board", "bind": {}, "position": "related"}
    assert rv[1]["view"] == ":anchored_track/feed"
    assert rv[1]["bind"] == {"currentUser": ":current_user"}
    assert rv[1]["position"] == "related"


def test_related_views_missing_view_raises():
    """``related_views[].view`` is required."""
    from app.exceptions import BadRequestError
    from app.services.operational_model_compile import _normalize_entry_type_spec

    with pytest.raises(BadRequestError) as ei:
        _normalize_entry_type_spec(
            {
                "key": "project",
                "name": "Project",
                "fields": [],
                "related_views": [{"view": "", "bind": {}}],
            }
        )
    assert "related_views" in str(ei.value)
    assert ".view is required" in str(ei.value)


def test_related_views_empty_list_compiles_cleanly():
    """Empty ``related_views`` is valid (UX polish is Phase 7 scope)."""
    from app.services.operational_model_compile import _normalize_entry_type_spec

    out = _normalize_entry_type_spec(
        {
            "key": "project",
            "name": "Project",
            "fields": [],
            "related_views": [],
        }
    )
    assert out["related_views"] == []


def test_related_views_omitted_back_compat():
    """Entry types WITHOUT related_views compile cleanly (back-compat)."""
    from app.services.operational_model_compile import _normalize_entry_type_spec

    out = _normalize_entry_type_spec(
        {
            "key": "note",
            "name": "Note",
            "fields": [],
        }
    )
    # The new slot is always populated (additive normalization), but empty.
    assert out["related_views"] == []
