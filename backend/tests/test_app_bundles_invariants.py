"""App Bundles v1 invariant gates — I-APP-01..05.

Phase 10 Plan 10-07 (CONTENT-FACTORY-SEED-01) lands the I-APP-01..05 invariant
section in ``docs/INVARIANTS.md``. This file is the executable gate that fails
CI when any invariant regresses.

  - I-APP-01 — Manifest v2 only.
  - I-APP-02 — App lifecycle atomicity.
  - I-APP-03 — Cross-App permission propagation via single ``relation_runtime``
               helper.
  - I-APP-04 — ``requires_apps[]`` enforcement.
  - I-APP-05 — Same-Workspace App scope.

Most tests delegate to the existing Plan 10-05 + Plan 10-06 regression suites
(cross-reference); the grep gates and structural gates land here as the
canonical single-source-of-truth for the invariant block.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

# Repository root — anchor every shell-out off this path so tests pass when
# invoked from any cwd.
_REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# I-APP-01 — Manifest v2 only
# ---------------------------------------------------------------------------


def test_I_APP_01_no_v1_schema_version_in_library_bundles():
    """No library bundle declares ``operational_model_schema_version: 1``."""
    result = subprocess.run(
        [
            "grep",
            "-rn",
            "operational_model_schema_version.*1",
            "backend/app/packages/",
            "--include=*.yaml",
        ],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )
    # grep returns 1 when no matches; that's the GREEN case for this gate.
    # If grep returns 0 (matches found), we filter out the v2 lines (which
    # contain `version: 2` not `version: 1`).
    bad_lines = [
        line
        for line in result.stdout.splitlines()
        if "operational_model_schema_version" in line
        and ("version: 1" in line or "version=1" in line or 'version": 1' in line)
        and "version: 2" not in line
        and "version=2" not in line
        and 'version": 2' not in line
    ]
    assert not bad_lines, (
        f"I-APP-01 violation: seeded packages still declare v1 manifest. "
        f"Offending lines:\n" + "\n".join(bad_lines)
    )


def test_I_APP_01_seeded_packages_all_declare_v2():
    """Every seeded package compiles + declares schema_version=2.

    Cross-reference: ``tests/test_seeded_packages_v2.py`` is the
    parametrized round-trip suite. This is a structural gate that asserts
    the suite exists.
    """
    test_file = _REPO_ROOT / "backend" / "tests" / "test_seeded_packages_v2.py"
    assert test_file.exists(), (
        "test_seeded_packages_v2.py is the I-APP-01 compile gate; missing "
        "the file removes the round-trip assertion."
    )


def test_I_APP_01_compiler_rejects_v1_manifest():
    """compile_canonical_manifest raises on a v1 manifest."""
    from app.exceptions import OperationalModelValidationError
    from app.services.operational_model_runtime import compile_canonical_manifest

    v1_manifest = {
        "operational_model_schema_version": 1,
        "scope": "track",
        "package": {"name": "legacy", "version": "1.0.0"},
        "track": {"entry_types": [], "views": []},
    }
    with pytest.raises(OperationalModelValidationError):
        compile_canonical_manifest(manifest=v1_manifest)


# ---------------------------------------------------------------------------
# I-APP-02 — App lifecycle atomicity
# ---------------------------------------------------------------------------


def test_I_APP_02_lifecycle_atomicity_regression_suite_exists():
    """test_app_lifecycle.py covers the 12-step install + compensation."""
    test_file = _REPO_ROOT / "backend" / "tests" / "test_app_lifecycle.py"
    assert test_file.exists()
    content = test_file.read_text()
    # Required regression scenarios from Plan 10-05.
    for needle in (
        "test_install_transaction_compensates_in_reverse_order",
        "test_install_with_settings_schema_pauses_at_step_9",
        "test_resume_install_with_valid_token_completes",
        "test_uninstall_archives_by_default",
        "test_force_uninstall_emits_force_action",
    ):
        assert needle in content, f"I-APP-02 regression missing: {needle}"


# ---------------------------------------------------------------------------
# I-APP-03 — Cross-App permission propagation via single helper
# ---------------------------------------------------------------------------


def test_I_APP_03_label_field_resolver_is_single_source():
    """The ``label_field`` field is only read through ``relation_runtime``.

    Risk 4 / Pitfall 5 gate: any other reader is a cross-App leak vector.
    Allowed loci (whitelist):
      - relation_runtime.py — the canonical resolver
      - schemas/cross_app_relations.py — wire shape declarations
      - operational_model_runtime.py — compile-time normalization
      - operational_model_compile.py — compile-time normalization (split from
        operational_model_runtime.py per
        .planning/refactors/operational_model_runtime_split_plan.md; sets the
        field on the canonical spec at compile time, does NOT read
        source-field content — same role as the runtime entry above)
      - models/edges.py — REFERENCES.target_app_id docstring reference
      - tests/* — assertions about the contract
    """
    result = subprocess.run(
        [
            "grep",
            "-rn",
            r"label_field\b",
            "backend/app/",
            "--include=*.py",
        ],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )
    offending = []
    for line in result.stdout.splitlines():
        path = line.split(":", 1)[0]
        # Whitelisted readers.
        if any(
            white in path
            for white in (
                "relation_runtime.py",
                "schemas/cross_app_relations.py",
                "operational_model_runtime.py",
                "operational_model_compile.py",
                "models/edges.py",
                "tests/",
            )
        ):
            continue
        offending.append(line)
    assert not offending, (
        f"I-APP-03 violation: ``label_field`` referenced outside the "
        f"single-resolver whitelist. Offenders:\n" + "\n".join(offending)
    )


def test_I_APP_03_restricted_stub_test_exists():
    """test_cross_app_relations.py asserts restricted stubs carry no source data."""
    test_file = _REPO_ROOT / "backend" / "tests" / "test_cross_app_relations.py"
    assert test_file.exists()
    content = test_file.read_text()
    assert "test_restricted_stub_carries_no_label_field_value" in content


# ---------------------------------------------------------------------------
# I-APP-04 — requires_apps enforcement
# ---------------------------------------------------------------------------


def test_I_APP_04_requires_apps_regression_suite_exists():
    """test_requires_apps.py covers install + uninstall dep blockers."""
    test_file = _REPO_ROOT / "backend" / "tests" / "test_requires_apps.py"
    assert test_file.exists()
    content = test_file.read_text()
    for needle in (
        "test_install_blocks_without_hard_dep",
        "test_uninstall_blocked_by_dependent_manifest_declaration",
        "test_force_uninstall_bypasses_dep_check",
    ):
        assert needle in content, f"I-APP-04 regression missing: {needle}"


# ---------------------------------------------------------------------------
# I-APP-05 — Same-Workspace App scope
# ---------------------------------------------------------------------------


def test_I_APP_05_cross_workspace_rejection_test_exists():
    """test_cross_app_relations.py asserts cross-Workspace pins are rejected."""
    test_file = _REPO_ROOT / "backend" / "tests" / "test_cross_app_relations.py"
    assert test_file.exists()
    content = test_file.read_text()
    assert (
        "test_cross_workspace_target_rejected_via_instance_pin" in content
    ), "I-APP-05 regression missing"


# ---------------------------------------------------------------------------
# I-CHA strict-superset gate — single-Literal invariant preserved by Phase 10
# ---------------------------------------------------------------------------


def test_I_CHA_single_literal_for_policy_and_change_event_action():
    """PolicyAction + ChangeEventAction each have EXACTLY ONE Literal definition."""
    result = subprocess.run(
        [
            "grep",
            "-cE",
            r"^(PolicyAction|ChangeEventAction)\s*=\s*Literal",
            "backend/app/schemas/audit.py",
            "backend/app/schemas/policy.py",
        ],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )
    counts = []
    for line in result.stdout.splitlines():
        # grep -c output: "<path>:<count>"
        try:
            counts.append(int(line.rsplit(":", 1)[1]))
        except (ValueError, IndexError):
            continue
    assert sum(counts) == 2, (
        f"I-CHA gate failed: expected exactly 2 Literal definitions "
        f"(PolicyAction in policy.py + ChangeEventAction in audit.py), "
        f"got total={sum(counts)} per-file={counts}"
    )


def test_I_CHA_app_lifecycle_actions_in_both_literals():
    """app.installed / app.uninstalled / app.force_uninstalled appear in both Literals."""
    from typing import get_args

    from app.schemas.audit import ChangeEventAction
    from app.schemas.policy import PolicyAction

    ce_actions = set(get_args(ChangeEventAction))
    pol_actions = set(get_args(PolicyAction))
    lifecycle = {"app.installed", "app.uninstalled", "app.force_uninstalled"}
    assert lifecycle.issubset(
        ce_actions
    ), f"missing in ChangeEventAction: {lifecycle - ce_actions}"
    assert lifecycle.issubset(
        pol_actions
    ), f"missing in PolicyAction: {lifecycle - pol_actions}"


# ---------------------------------------------------------------------------
# Content Factory canonical reference — structural gate
# ---------------------------------------------------------------------------


def test_content_factory_in_library_catalog():
    """Content Factory is discoverable via the YAML library loader."""
    from app.services.operational_model_loader import load_library_operational_models

    slugs = {s.slug for s in load_library_operational_models()}
    assert "content-factory" in slugs


def test_content_factory_bundle_assets_exist():
    """Skill SKILL.md and agent persona files exist on disk (canonical bundle path)."""
    base = _REPO_ROOT / "backend" / "app" / "packages" / "content-factory"
    assert (base / "operational-model.yaml").exists()
    assert (base / "skills" / "carousel_drafter" / "SKILL.md").exists()
    assert (base / "skills" / "performance_reviewer" / "SKILL.md").exists()
    assert (base / "agents" / "drafter.yaml").exists()


@pytest.mark.parametrize(
    "model_path",
    sorted(
        (_REPO_ROOT / "backend" / "app" / "packages").glob("*/operational-model.yaml")
    ),
    ids=lambda p: p.parent.name,
)
def test_I_BUNDLE_profiles_compile(model_path):
    """I-BUNDLE-01/04 — every on-disk bundle compiles; dir name == slug."""
    import yaml

    from app.services.operational_model_loader import load_library_operational_models

    slug = model_path.parent.name
    raw = yaml.safe_load(model_path.read_text(encoding="utf-8")) or {}
    package = raw.get("package") or {}
    assert (
        str(package.get("slug") or "") == slug
    ), f"I-BUNDLE-04: {slug}/operational-model.yaml package.slug mismatch"
    spec = next((s for s in load_library_operational_models() if s.slug == slug), None)
    assert spec is not None, f"I-BUNDLE-01: {slug} not loaded by library scanner"
    from app.services.operational_model_runtime import compile_canonical_manifest

    compile_canonical_manifest(manifest=spec.manifest)


@pytest.mark.parametrize(
    "model_path",
    sorted(
        (_REPO_ROOT / "backend" / "app" / "packages").glob("*/operational-model.yaml")
    ),
    ids=lambda p: p.parent.name,
)
def test_I_BUNDLE_declared_skills_have_skill_md(model_path):
    """I-BUNDLE-02 — declared skill keys have skills/<key>/SKILL.md on disk."""
    import yaml

    from app.services.operational_model_loader import _expand_bare_skill_keys

    raw = yaml.safe_load(model_path.read_text(encoding="utf-8")) or {}
    app_block = dict(raw.get("app") or {})
    track_block = dict(raw.get("track") or {})
    app_tier = _expand_bare_skill_keys(app_block)
    track_tier = _expand_bare_skill_keys(track_block)
    skills = list(app_tier.get("skills") or []) + list(track_tier.get("skills") or [])
    bundle_dir = model_path.parent
    for spec in skills:
        if not isinstance(spec, dict):
            continue
        key = spec.get("key")
        if not key:
            continue
        ref = str(spec.get("prompt_template") or spec.get("prompt_template_ref") or "")
        if not ref.startswith("skills/") or not ref.endswith("SKILL.md"):
            continue
        skill_md = bundle_dir / "skills" / key / "SKILL.md"
        assert (
            skill_md.is_file()
        ), f"I-BUNDLE-02: missing {skill_md.relative_to(bundle_dir)}"


def test_I_BUNDLE_trusted_bundles_with_tools_declare_trust_tier():
    """Bundles declaring app.tools[] must set package.trust_tier trusted/audited."""
    import yaml

    packages_root = _REPO_ROOT / "backend" / "app" / "packages"
    for model_path in packages_root.glob("*/operational-model.yaml"):
        raw = yaml.safe_load(model_path.read_text(encoding="utf-8")) or {}
        tools = (raw.get("app") or {}).get("tools") or []
        if not tools:
            continue
        tier = str((raw.get("package") or {}).get("trust_tier") or "").lower()
        assert tier in {
            "trusted",
            "audited",
        }, f"{model_path.parent.name} declares tools but trust_tier={tier!r}"


def test_content_factory_install_integration_test_exists():
    """domain_apps/test_content_factory_install.py provides end-to-end coverage."""
    test_file = (
        _REPO_ROOT
        / "backend"
        / "tests"
        / "domain_apps"
        / "test_content_factory_install.py"
    )
    assert test_file.exists()
    content = test_file.read_text()
    for needle in (
        "test_end_to_end_install_awaiting_settings_then_finalize",
        "test_install_with_pre_supplied_settings_skips_pause",
        "test_install_with_invalid_settings_rejected",
        "test_seeds_idempotent_on_replant",
        "test_write_content_piece_referencing_seeded_brand_voice",
        "test_archive_preserves_tracks_and_emits_app_uninstalled",
        "test_force_uninstall_emits_force_action",
    ):
        assert needle in content, f"Content Factory integration test missing: {needle}"


def test_I_CRUD_01_service_layer_drift_check_passes():
    """I-CRUD-01 — service-layer drift guard exits 0 on clean tree."""
    script = _REPO_ROOT / ".ci" / "service_layer_drift_check.sh"
    assert script.exists(), "service_layer_drift_check.sh missing"
    result = subprocess.run(
        ["bash", str(script)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
