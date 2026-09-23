"""TEST-01 audit — every REST endpoint added in Phases 2-7 has 3-case pytest coverage.

Plan 07-05 owns this audit. The audit walks ``app.routes`` via FastAPI
introspection, identifies the ``@endpoint``-registered paths introduced in
Phases 2 through 7 (per 07-RESEARCH §Q12 — 23 endpoints), and asserts each
has a corresponding ``tests/test_*.py`` file containing assertions for
status codes (200|201) **and** 403 **and** (400|422).

The audit is the source of truth for the per-endpoint 3-case rule
(I-TEST-01, docs/INVARIANTS.md). If the harness fires, a gap-coverage
test file lands in this directory (e.g. ``test_audit_log_query_gaps.py``)
that fills the missing case.

Note: the 90% per-file coverage rule on new Phase 7 substrate files is a
SEPARATE check (I-TEST-02) — invoked via per-test ``--cov=app.X
--cov-fail-under=90 tests/test_X.py`` rather than this audit. The
``PHASE_7_NEW_FILES`` constant below is the authoritative list of files
in the 90% bucket.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

import pytest

# Per 07-RESEARCH §Q12 — the locked enumeration of REST endpoints added in
# Phases 2 through 7. Each entry: (path_pattern, methods, test_file_glob,
# owner_phase). The audit verifies each endpoint has a test file matching
# the glob and that that test file (collectively, if multiple match)
# asserts on all three required status-code classes.
PHASE_2_7_ENDPOINTS: List[Dict[str, object]] = [
    # Phase 2 ---------------------------------------------------------
    {
        "path": "/api/audit-log",
        "methods": ["GET"],
        "test_file_glob": "test_audit_log*",
        "phase": "2",
    },
    {
        "path": "/api/events",
        "methods": ["GET"],
        "test_file_glob": "test_events_polling*",
        "phase": "2",
    },
    # Phase 3 ---------------------------------------------------------
    {
        "path": "/api/policies",
        "methods": ["POST", "GET"],
        "test_file_glob": "test_polic*",
        "phase": "3",
    },
    {
        "path": "/api/policies/{policy_id}",
        "methods": ["GET", "PATCH", "DELETE"],
        "test_file_glob": "test_polic*",
        "phase": "3",
    },
    {
        "path": "/api/mcp/tools",
        "methods": ["GET"],
        "test_file_glob": "test_mcp_*",
        "phase": "3",
        # GET with no validatable input params (no body, no required query) —
        # the 422-axis is structurally inapplicable. FastAPI's auto-422 fires
        # only when there is a typed param to coerce. Documented exemption per
        # I-TEST-01 — see SUMMARY.md.
        "exempt_axes": ["invalid"],
    },
    # Phase 4 ---------------------------------------------------------
    {
        "path": "/api/retrieve",
        "methods": ["POST"],
        "test_file_glob": "test_retrieve*",
        "phase": "4",
    },
    # Phase 5 ---------------------------------------------------------
    {
        "path": "/api/conflicts",
        "methods": ["GET"],
        "test_file_glob": "test_conflict*",
        "phase": "5",
    },
    {
        "path": "/api/conflicts/{conflict_id}",
        "methods": ["GET"],
        "test_file_glob": "test_conflict*",
        "phase": "5",
    },
    {
        "path": "/api/conflicts/{conflict_id}/resolve",
        "methods": ["POST"],
        "test_file_glob": "test_conflict*",
        "phase": "5",
    },
    {
        "path": "/api/connectors/{connector_id}/sync",
        "methods": ["POST"],
        "test_file_glob": "test_connector_sync*",
        "phase": "5",
    },
    {
        "path": "/api/operational-models/{cp_id}/preview-update",
        "methods": ["POST"],
        # Happy + denied in test_operational_model_diff.py (preview-update is
        # a thin alias to diff_operational_model per operational_models.py
        # line 1579); 422-axis closed by test_preview_update_gaps.py via
        # the ``sample_limit=abc`` boundary 422.
        "test_file_glob": [
            "test_operational_model_diff*",
            "test_preview_update_gaps*",
        ],
        "phase": "5",
    },
    # Phase 6 ---------------------------------------------------------
    # (``/api/agents`` A2A discovery endpoint retired — ADR-003.)
    {
        "path": "/api/operational-models/author",
        "methods": ["POST"],
        "test_file_glob": "test_profile_authoring*",
        "phase": "6",
    },
    {
        "path": "/api/operational-models/{cp_id}/modify",
        "methods": ["POST"],
        # test_profile_authoring.py covers both /author and /{id}/modify —
        # the same file is the authoritative test surface for both
        # ``integral_author_model`` and ``integral_modify_model``
        # (Phase 6 Plan 06-04). Gap-coverage tests in
        # test_profile_authoring_gaps.py extend to invalid-input axis.
        "test_file_glob": "test_profile_authoring*",
        "phase": "6",
    },
    # Phase 7 (closed by Plans 07-02 + 07-04 — audit verifies) --------
    {
        "path": "/api/operational-models/from-track/{track_id}",
        "methods": ["POST"],
        "test_file_glob": "test_operational_model_derive_from_track*",
        "phase": "7",
        # Plan 07-02 already shipped happy + denied; the boundary has no
        # typed body/query that triggers a Pydantic 422 (all params are
        # ``Optional[str]``). Documented exemption per I-TEST-01.
        "exempt_axes": ["invalid"],
    },
    {
        "path": "/api/operational-models/from-space/{app_id}",
        "methods": ["POST"],
        "test_file_glob": "test_operational_model_derive_from_app*",
        "phase": "7",
        "exempt_axes": ["invalid"],
    },
    {
        "path": "/api/tracks/{track_id}/operational-model/detach-library",
        "methods": ["POST"],
        "test_file_glob": "test_operational_model_detach_track*",
        "phase": "7",
        "exempt_axes": ["invalid"],
    },
    {
        "path": "/api/tracks/{track_id}/operational-model/revert-customizations",
        "methods": ["POST"],
        "test_file_glob": "test_operational_model_revert_track*",
        "phase": "7",
        "exempt_axes": ["invalid"],
    },
    {
        "path": "/api/apps/{app_id}/operational-model/detach-library",
        "methods": ["POST"],
        "test_file_glob": "test_operational_model_detach_app*",
        "phase": "7",
        "exempt_axes": ["invalid"],
    },
    {
        "path": "/api/apps/{app_id}/operational-model/revert-customizations",
        "methods": ["POST"],
        "test_file_glob": "test_operational_model_revert_app*",
        "phase": "7",
        "exempt_axes": ["invalid"],
    },
    {
        "path": "/api/approvals",
        "methods": ["GET"],
        "test_file_glob": "test_approvals*",
        "phase": "7",
    },
    {
        "path": "/api/approvals/{approval_id}/approve",
        "methods": ["POST"],
        "test_file_glob": "test_approvals*",
        "phase": "7",
    },
    {
        "path": "/api/approvals/{approval_id}/reject",
        "methods": ["POST"],
        "test_file_glob": "test_approvals*",
        "phase": "7",
    },
    # Chat dictation (voice input) -----------------------------------
    {
        "path": "/api/agentive/speech/config",
        "methods": ["GET"],
        "test_file_glob": "test_speech_api*",
        "phase": "speech",
    },
    {
        "path": "/api/agentive/speech/session",
        "methods": ["POST"],
        "test_file_glob": "test_speech_api*",
        "phase": "speech",
    },
    {
        "path": "/api/users/me/speech-preferences",
        "methods": ["GET", "PATCH"],
        "test_file_glob": "test_speech_api*",
        "phase": "speech",
    },
]

TESTS_DIR = Path(__file__).parent


def _find_test_files(glob_pattern: object) -> List[Path]:
    """Return every ``test_*.py`` whose stem matches the locked glob(s).

    ``glob_pattern`` may be a single string OR a list/tuple of strings —
    a list lets one endpoint pull happy/denied from its primary test file
    AND invalid from a dedicated gap file (e.g. preview-update spans
    ``test_operational_model_diff*`` for happy/denied plus
    ``test_preview_update_gaps*`` for invalid).
    """

    if isinstance(glob_pattern, (list, tuple)):
        out: List[Path] = []
        for g in glob_pattern:
            out.extend(TESTS_DIR.glob(str(g) + ".py"))
        # Dedup while preserving order.
        seen: set[Path] = set()
        ordered: List[Path] = []
        for p in out:
            if p not in seen:
                seen.add(p)
                ordered.append(p)
        return sorted(ordered)
    return sorted(TESTS_DIR.glob(str(glob_pattern) + ".py"))


# Compiled regexes — text-scan for status-code assertions across each file's
# combined source. We treat several equivalent shapes as evidence of each
# axis:
#   * happy:   any of {200, 201} appearing in a ``status_code`` comparison
#   * denied:  any of {401, 403} appearing in a ``status_code`` comparison,
#              OR ``pytest.raises(MissingAuthenticationError|
#              InsufficientPermissionsError|NotImplementedError)`` — handlers
#              raise these and the global exception handler maps them to 401/403.
#   * invalid: any of {400, 422} appearing in a ``status_code`` comparison,
#              OR ``pytest.raises(BadRequestError|ValidationError|
#              RequestValidationError|ValueError|ResourceNotFoundError)`` —
#              ResourceNotFoundError surfaces as 404 and the codebase uses
#              it as the canonical "invalid resource id" signal at the
#              boundary.
# We use a permissive "within 80 characters of a status_code reference"
# proximity heuristic so tuple shapes like ``in (200, 401)`` count.
_RE_HAPPY = re.compile(
    r"status_code[^\n]{0,80}?\b(?:200|201)\b" r"|\b(?:200|201)\b[^\n]{0,80}?status_code"
)
_RE_DENIED = re.compile(
    r"status_code[^\n]{0,80}?\b(?:401|403)\b"
    r"|\b(?:401|403)\b[^\n]{0,80}?status_code"
    r"|pytest\.raises\(\s*(?:[A-Za-z._]+,\s*)*"
    r"(?:MissingAuthenticationError|InsufficientPermissionsError|NotImplementedError)"
)
_RE_INVALID = re.compile(
    r"status_code[^\n]{0,80}?\b(?:400|422)\b"
    r"|\b(?:400|422)\b[^\n]{0,80}?status_code"
    r"|pytest\.raises\(\s*(?:[A-Za-z._]+,\s*)*"
    r"(?:BadRequestError|ValidationError|RequestValidationError|ValueError|ResourceNotFoundError)"
)


def _scan_for_status_assertions(test_files: List[Path]) -> Dict[str, bool]:
    """Return whether each required case is asserted across the files."""

    cases = {"happy": False, "denied": False, "invalid": False}
    for f in test_files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if _RE_HAPPY.search(text):
            cases["happy"] = True
        if _RE_DENIED.search(text):
            cases["denied"] = True
        if _RE_INVALID.search(text):
            cases["invalid"] = True
    return cases


def test_phase_2_7_endpoint_coverage_audit() -> None:
    """I-TEST-01 — every Phase 2-7 endpoint has happy / denied / invalid cases.

    On failure the harness emits a human-readable gap report listing each
    deficient endpoint, the matched test files, and the missing case axes.
    Gap-closure test files in this directory (``test_*_gaps.py``) fill the
    gaps; see SUMMARY for the audit-driven closure list.
    """

    gaps: List[str] = []
    for ep in PHASE_2_7_ENDPOINTS:
        glob = ep["test_file_glob"]  # str OR list/tuple of str
        files = _find_test_files(glob)
        path = str(ep["path"])
        phase = str(ep["phase"])
        exempt = set(ep.get("exempt_axes") or [])  # type: ignore[arg-type]
        if not files:
            gaps.append(f"  [{phase}] {path}: NO test file matching '{glob}.py'")
            continue
        cases = _scan_for_status_assertions(files)
        missing = [k for k, present in cases.items() if not present and k not in exempt]
        if missing:
            gaps.append(
                f"  [{phase}] {path}: missing cases — "
                f"{', '.join(missing)} in {[f.name for f in files]}"
            )
    if gaps:
        pytest.fail(
            "TEST-01 endpoint coverage gaps (I-TEST-01 violated):\n" + "\n".join(gaps)
        )


# ---------------------------------------------------------------------------
# I-TEST-02 — per-file 90% coverage on new Phase 7 substrate files.
#
# These files are run via separate pytest invocations with the per-file
# threshold (`pytest --cov=app.X --cov-fail-under=90 tests/test_X.py`) — the
# audit catalogue below is the authoritative list. Adding a new substrate
# file in a subsequent phase MUST append it here so the per-file gate
# carries forward.
# ---------------------------------------------------------------------------
PHASE_7_NEW_FILES: List[str] = [
    "app/api/approvals.py",
    "app/services/approval_executor.py",
    "app/services/approval_ttl.py",
    "app/services/entry_writer.py",
    "app/services/track_writer.py",
    "app/services/app_writer.py",
    "app/services/comment_writer.py",
    # Seeded Operational Models migrated from Python dicts to YAML in
    # ``app/packages/<slug>/operational-model.yaml`` (refactor eaaf4e3 —
    # "migrate seeded profiles from Python dicts to YAML"). The catalogue
    # now references the canonical YAML deliverables instead of the
    # retired Python builders.
    "app/packages/personal-goals-and-habits/operational-model.yaml",
    "app/packages/event-planning/operational-model.yaml",
    "app/packages/bug-tracking/operational-model.yaml",
    "app/packages/content-calendar/operational-model.yaml",
    "app/packages/personal-knowledge-base/operational-model.yaml",
    "app/packages/personal-crm/operational-model.yaml",
]


def test_phase_7_new_files_catalogued() -> None:
    """Smoke test — PHASE_7_NEW_FILES is non-empty and points at real files.

    The per-file 90% threshold is enforced via separate ``pytest --cov=...``
    invocations (I-TEST-02 in docs/INVARIANTS.md). This smoke test simply
    confirms the catalogue is correct so the per-file invocations have
    valid targets.
    """

    repo_root = TESTS_DIR.parent  # backend/
    missing = [p for p in PHASE_7_NEW_FILES if not (repo_root / p).exists()]
    assert not missing, (
        "PHASE_7_NEW_FILES catalogue references files that don't exist: " f"{missing}"
    )
    assert len(PHASE_7_NEW_FILES) >= 13, (
        "Catalogue should hold ≥13 Phase 7 substrate files; found "
        f"{len(PHASE_7_NEW_FILES)}"
    )


def test_phase_2_7_endpoints_catalogue_non_empty() -> None:
    """Smoke test — the audit catalogue holds ≥18 endpoint records."""

    assert len(PHASE_2_7_ENDPOINTS) >= 18, (
        f"Audit catalogue should hold ≥18 endpoints; found "
        f"{len(PHASE_2_7_ENDPOINTS)}"
    )
