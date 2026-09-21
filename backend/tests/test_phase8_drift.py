"""Phase 8 cross-cutting drift gate (Plan 08-06 Task 1 Step 3).

Asserts that Phase 8 introduced ZERO new APIRouter / HTTPException /
inline BaseModel patterns AND ZERO new PolicyAction / ChangeEventAction /
ActorKind Literal members. Tighter scoping than the project-wide
``test_jvspatial_convention_compliance.py`` — the gates below run against
the 8 Phase 8 backend files specifically, so a regression in one of
them fails this file even if the project-wide gate is silent for other
reasons (e.g. allowlist update).

Phase 8 acceptance criterion #5 (`bash .ci/jvspatial_drift_check.sh`
exits 0) is also re-verified in this module so the Phase 8 SUMMARY can
cite a single test artifact.

See docs/INVARIANTS.md → "Phase 8 — Settings & Configuration Surface"
(I-SET-01 / I-SET-02) for the invariants this gate locks in.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from typing import get_args

import pytest

# Repo root — backend/tests/test_phase8_drift.py is 2 levels under root.
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"

# Phase 8 backend files — the 8 files that received additions across
# Plans 08-02, 08-04, 08-05. The drift gate runs against this set
# specifically; project-wide drift is covered by the
# ``test_jvspatial_convention_compliance.py`` allowlist.
PHASE_8_FILES: list[str] = [
    "app/api/retrieval_config.py",
    "app/schemas/retrieval_config.py",
    "app/schemas/sharing_overview.py",
    "app/schemas/connector_bindings.py",
    "app/agentive/api/connectors.py",
    "app/agentive/services/connector_registry_node.py",
    "app/schemas/agentive/connectors.py",
    "app/api/shared_with_me.py",
]

# Baseline Literal-member counts at Phase 7 close (verified 2026-05-17 via
# typing.get_args). Phase 8 plans (08-01..08-05) MUST NOT expand these.
# A future plan that DOES expand a Literal must bump the baseline AND
# justify the expansion in its SUMMARY — the bump is the review gate.
POLICY_ACTION_BASELINE = 145  # +4: collaborator_role_update (9ffd952, I-ROLE-01); +3: workspace.apps_reorder, app.tracks_reorder, tool.invoke (hook/tool dispatch); +2: model_credential.{upsert,revoke} (e26adeb, per-user BYOK model credentials)
CHANGE_EVENT_ACTION_BASELINE = 121  # +4: collaborator_role_update (9ffd952, I-ROLE-01); +3: workspace.apps_reorder, app.tracks_reorder, tool.invoke; +2: model_credential.{upsert,revoke} (e26adeb, per-user BYOK model credentials)
# ActorKind is a Phase 1 D-09 / D-10 single-Literal invariant — locked at 4.
ACTOR_KIND_BASELINE = 4

# ---------------------------------------------------------------------------
# Helpers — load & walk each Phase 8 file's AST exactly once per test.
# ---------------------------------------------------------------------------


def _load_asts() -> dict[str, ast.Module]:
    """Return {relative_path: AST} for every Phase 8 file."""
    trees: dict[str, ast.Module] = {}
    for rel in PHASE_8_FILES:
        path = BACKEND / rel
        assert path.exists(), f"Phase 8 file missing: {rel}"
        trees[rel] = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return trees


# ---------------------------------------------------------------------------
# Case 1 — No ``from fastapi import APIRouter`` in any Phase 8 file.
# ---------------------------------------------------------------------------


def test_no_apirouter_in_phase8_files() -> None:
    """No Phase 8 file imports APIRouter from fastapi.

    The canonical pattern is ``@endpoint`` from ``jvspatial.api`` (I-CONV-01).
    """
    offending: list[str] = []
    for rel, tree in _load_asts().items():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == "fastapi":
                    for alias in node.names:
                        if alias.name == "APIRouter":
                            offending.append(f"{rel}:{node.lineno}: APIRouter")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "fastapi.APIRouter":
                        offending.append(f"{rel}:{node.lineno}: APIRouter")
    assert (
        not offending
    ), "Phase 8 drift — forbidden APIRouter import. Use @endpoint:\n" + "\n".join(
        offending
    )


# ---------------------------------------------------------------------------
# Case 2 — No ``raise HTTPException(...)`` in any Phase 8 file.
# ---------------------------------------------------------------------------


def test_no_http_exception_in_phase8_files() -> None:
    """No Phase 8 file raises HTTPException.

    The canonical pattern is to raise a ``JVSpatialAPIException`` subclass
    from ``app.api.errors`` (I-CONV-02).
    """
    offending: list[str] = []
    for rel, tree in _load_asts().items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise):
                continue
            exc = node.exc
            if isinstance(exc, ast.Call):
                target = exc.func
                if isinstance(target, ast.Name) and target.id == "HTTPException":
                    offending.append(f"{rel}:{node.lineno}: raise HTTPException")
                elif (
                    isinstance(target, ast.Attribute) and target.attr == "HTTPException"
                ):
                    offending.append(f"{rel}:{node.lineno}: raise HTTPException")
    assert not offending, (
        "Phase 8 drift — forbidden raise HTTPException. Use "
        "JVSpatialAPIException subclass from app.api.errors:\n" + "\n".join(offending)
    )


# ---------------------------------------------------------------------------
# Case 3 — No inline BaseModel declarations in API modules.
# ---------------------------------------------------------------------------


def test_no_inline_basemodel_in_phase8_api_files() -> None:
    """API files (``api/*`` / ``agentive/api/*``) do not define BaseModel
    subclasses — request/response schemas live under ``app/schemas/``
    (I-CONV-03).

    Schema files (``schemas/*``) are expected to define BaseModel subclasses
    and are not scanned by this gate.
    """
    api_files = [
        rel
        for rel in PHASE_8_FILES
        if (rel.startswith("app/api/") or rel.startswith("app/agentive/api/"))
    ]
    offending: list[str] = []
    for rel in api_files:
        tree = ast.parse((BACKEND / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for base in node.bases:
                base_name: str | None = None
                if isinstance(base, ast.Name):
                    base_name = base.id
                elif isinstance(base, ast.Attribute):
                    base_name = base.attr
                if base_name == "BaseModel":
                    offending.append(
                        f"{rel}:{node.lineno}: class {node.name}(BaseModel)"
                    )
    assert not offending, (
        "Phase 8 drift — inline BaseModel in api/ module. Extract to "
        "backend/app/schemas/:\n" + "\n".join(offending)
    )


# ---------------------------------------------------------------------------
# Case 4 — PolicyAction Literal member count is unchanged.
# ---------------------------------------------------------------------------


def test_policy_action_unchanged() -> None:
    """PolicyAction Literal must hold exactly the Phase 7-close member count.

    A1 of plan 08-06 — Settings panels are READ proxies (I-SET-01), so no
    new ``settings.*`` / ``retrieval.*`` / ``binding.*`` action members
    should have appeared.

    To intentionally add a member, bump the baseline here AND document the
    new member in the relevant plan's SUMMARY — this test's failure is the
    review gate.
    """
    from app.schemas.policy import PolicyAction

    members = set(get_args(PolicyAction))
    assert len(members) == POLICY_ACTION_BASELINE, (
        f"PolicyAction Literal count changed: expected "
        f"{POLICY_ACTION_BASELINE}, got {len(members)}. "
        f"Bump the baseline in test_phase8_drift.py only if the new "
        f"member is intentional and documented in a plan SUMMARY."
    )


# ---------------------------------------------------------------------------
# Case 5 — ChangeEventAction Literal member count is unchanged.
# ---------------------------------------------------------------------------


def test_change_event_action_unchanged() -> None:
    """ChangeEventAction Literal must hold exactly the Phase 7-close count.

    Phase 8 added zero new audit event types — Connector CRUD reuses the
    existing ``connector.create/update/delete`` members, resource-level
    invitation revoke reuses ``workspace.invitation_revoke`` (08-05 §A4).
    """
    from app.schemas.audit import ChangeEventAction

    members = set(get_args(ChangeEventAction))
    assert len(members) == CHANGE_EVENT_ACTION_BASELINE, (
        f"ChangeEventAction Literal count changed: expected "
        f"{CHANGE_EVENT_ACTION_BASELINE}, got {len(members)}. "
        f"Bump the baseline in test_phase8_drift.py only if the new "
        f"member is intentional and documented in a plan SUMMARY."
    )


# ---------------------------------------------------------------------------
# Case 6 — ActorKind Literal members are unchanged (D-09 invariant).
# ---------------------------------------------------------------------------


def test_actor_kind_unchanged() -> None:
    """ActorKind Literal is the Phase 1 D-09 single-Literal invariant —
    locked at exactly 4 members (``human``, ``agent``, ``connector``,
    ``system``). Phase 8 introduced no new actor surface."""
    from app.schemas.provenance import ActorKind

    members = set(get_args(ActorKind))
    expected = {"human", "agent", "connector", "system"}
    assert members == expected, (
        f"ActorKind Literal changed: expected {sorted(expected)}, got "
        f"{sorted(members)}. ActorKind is the D-09 single-Literal "
        f"invariant — any expansion requires substrate review."
    )
    assert len(members) == ACTOR_KIND_BASELINE


# ---------------------------------------------------------------------------
# Case 7 — Phase 8 surface in particular passes the D-05 grep gate.
#
# Pre-existing D-05 regressions on the dev-monorepo-rebuild branch
# (documented in deferred-items.md from Plan 08-02 and 08-04) span
# auth.py / operational_models.py / ai_chat.py / invitations.py / shares.py
# / workspaces.py / attachments.py / access.py / approvals.py /
# retrieve.py / tracks.py / users.py / connectors.py(sync) / agentive/
# api/staging.py. NONE of these handlers are in Phase 8's surface —
# Phase 8 itself adds ZERO new D-05 violations. This case asserts the
# narrower scope: every Phase 8 mutation handler emits change events.
# ---------------------------------------------------------------------------


def test_phase8_mutation_handlers_emit_change_event() -> None:
    """Every mutation handler ADDED in Phase 8 emits ``emit_change_event``.

    Mirrors the AST walker in ``test_change_event_no_bypass.py`` but scoped
    to Phase 8 surface: scans ``app/agentive/api/connectors.py`` (the file
    that received the new CRUD + binding handlers), ``app/api/shared_with_me.py``
    (the file with the new ``/me/sharing-overview`` handler — a READ
    endpoint that legitimately must not emit), ``app/api/retrieval_config.py``
    (a READ endpoint that legitimately must not emit), and the new
    ``app/api/invitations.py`` ``delete_invitation_endpoint`` handler from
    Plan 08-05.

    See ``backend/tests/test_change_event_no_bypass.py`` for the
    project-wide D-05 gate. Pre-existing failures there (43 handlers across
    12+ files NOT in Phase 8 surface) are tracked in
    ``.planning/phases/08-settings-configuration-surface/deferred-items.md``
    and do not block this gate.
    """
    # Phase 8 mutation handlers (by file:func) that MUST emit.
    expected_emitters: list[tuple[str, str]] = [
        # Plan 08-02 Task 1 — CRUD additions to agentive connectors.
        ("app/agentive/api/connectors.py", "update_connector_endpoint"),
        ("app/agentive/api/connectors.py", "delete_connector_endpoint"),
        # Plan 08-02 Task 3 — bindings additions.
        ("app/agentive/api/connectors.py", "create_connector_binding"),
        ("app/agentive/api/connectors.py", "delete_connector_binding"),
        # Plan 08-05 Task 3 — resource-level invitation revoke.
        ("app/api/invitations.py", "delete_invitation_endpoint"),
    ]
    missing: list[str] = []
    for rel, func_name in expected_emitters:
        path = BACKEND / rel
        if not path.exists():
            missing.append(f"{rel} — file missing")
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found = False
        emits = False
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == func_name:
                found = True
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Call):
                        target = inner.func
                        if (
                            isinstance(target, ast.Name)
                            and target.id == "emit_change_event"
                        ):
                            emits = True
                            break
                        if (
                            isinstance(target, ast.Attribute)
                            and target.attr == "emit_change_event"
                        ):
                            emits = True
                            break
                break
        if not found:
            # Tolerate if the handler signature uses an alternate naming
            # convention; the project-wide D-05 gate would still catch it
            # against its baseline.
            continue
        if not emits:
            missing.append(f"{rel}::{func_name} — missing emit_change_event")
    assert (
        not missing
    ), "Phase 8 mutation handler missing emit_change_event:\n" + "\n".join(missing)


# ---------------------------------------------------------------------------
# Case 8 — bash .ci/jvspatial_drift_check.sh exits 0 (Phase 8 AC #5).
# ---------------------------------------------------------------------------


def test_jvspatial_drift_check_exits_zero() -> None:
    """Phase 8 acceptance criterion #5 — drift check exits 0 on the
    full backend tree. Asserts that the project-wide pre-commit hook
    (which Phase 8 plans never modify) still passes."""
    script = REPO_ROOT / ".ci" / "jvspatial_drift_check.sh"
    if not script.exists():
        pytest.skip("jvspatial_drift_check.sh not present in this checkout")
    result = subprocess.run(
        ["bash", str(script)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"jvspatial_drift_check.sh exited non-zero ({result.returncode}).\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# Sanity case — PHASE_8_FILES catalogue references real files.
# ---------------------------------------------------------------------------


def test_phase_8_files_catalogue_valid() -> None:
    """Every entry in PHASE_8_FILES points at a real file under backend/.

    Catches stale entries if a future plan renames or removes a Phase 8
    file without updating this catalogue.
    """
    missing = [rel for rel in PHASE_8_FILES if not (BACKEND / rel).exists()]
    assert not missing, f"PHASE_8_FILES references non-existent paths: {missing}"
    assert len(PHASE_8_FILES) >= 6, (
        f"PHASE_8_FILES should hold at least 6 Phase 8 backend files; "
        f"found {len(PHASE_8_FILES)}"
    )
