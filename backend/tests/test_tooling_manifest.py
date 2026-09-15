"""Manifest loader + validator: real manifest loads; existing tools parse;
broken entries are rejected with clear errors."""

import pytest

from app.agentive.tooling.manifest import (
    ManifestError,
    ToolSpec,
    load_manifest,
    validate_manifest,
)


def test_load_existing_tools():
    reg = load_manifest()  # reads backend/app/agentive/tool_manifest.yaml
    names = {t.name for t in reg.values()}
    assert "integral_list_tracks" in names
    assert "integral_create_entry" in names
    lt = reg["integral_list_tracks"]
    assert lt.op_class == "read"
    assert lt.http.method == "GET" and lt.http.path == "/api/tracks"
    ce = reg["integral_create_entry"]
    assert ce.op_class == "propose"
    assert ce.staging_kind == "create_entry"


def test_validate_passes_for_existing():
    reg = {n: t for n, t in load_manifest().items() if t.status == "existing"}
    validate_manifest(reg)  # raises ManifestError on any violation


def test_validate_passes_for_the_whole_registry_including_gaps():
    """The natural call — no pre-filtering — must pass.

    Rule (c) used to demand a registered ``staging_kind`` from every propose
    tool regardless of status, so ``validate_manifest(load_manifest())`` failed
    on ``gap`` entries that are correct as written (a gap has no executor to
    name yet). Only the status-filtered call above passed, which put the
    invariant in the test rather than in the function and left the validator
    unusable as a gate.
    """
    validate_manifest(load_manifest())


def test_validate_still_rejects_an_implemented_propose_without_staging_kind():
    """Scoping rule (c) to `existing` must not blunt it for real tools."""
    reg = load_manifest()
    broken = reg["integral_create_entry"].model_copy(
        update={"staging_kind": None, "status": "existing"}
    )
    with pytest.raises(ManifestError, match="staging_kind"):
        validate_manifest({broken.name: broken})


def test_validate_rejects_bad_policy_action():
    reg = load_manifest()
    bad = list(reg.values())[0].model_copy(
        update={"policy_action": "not.a.real.action"}
    )
    with pytest.raises(ManifestError, match="policy_action"):
        validate_manifest({bad.name: bad})


def test_validate_rejects_propose_without_staging_kind():
    reg = load_manifest()
    ce = reg["integral_create_entry"].model_copy(update={"staging_kind": None})
    with pytest.raises(ManifestError, match="staging_kind"):
        validate_manifest({ce.name: ce})
