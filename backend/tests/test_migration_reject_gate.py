"""Phase 5 Plan 05-02 — no-migration-path reject gate (MIG-03) tests.

Covers:
  - reject gate detects unhandled would_need_migration impacts when manifest
    declares ZERO migration ops.
  - reject gate passes (returns []) when manifest declares ANY migration ops
    (v1 conservative: trusts the author).
  - publish_draft raises BadRequestError 422 with details.unhandled_breaks
    when reject gate trips and force=False.
  - publish_draft bypasses the reject gate and returns a skipped tracker
    when force=True.
  - publish endpoint policy gate evaluates ``migration.publish`` (default)
    vs ``migration.force_publish`` (when force=true) — separate actions so
    a Policy can allow publish but DENY force.

Single-Literal grep gates and provenance free-string grep guard are exercised
at the end of the file (Tests 13 + 14).
"""

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions import BadRequestError
from app.services.migrations import detect_unhandled_breaks

# ---------------------------------------------------------------------------
# detect_unhandled_breaks — pure function
# ---------------------------------------------------------------------------


def test_reject_gate_empty_impacts_returns_empty():
    """No impacts → nothing to reject."""
    assert detect_unhandled_breaks(impacts=[], migrations=[]) == []


def test_reject_gate_no_break_no_reject():
    """Impacts that report zero would_need_migration / would_fail_validation pass."""
    impacts = [
        {"track_id": "t1", "would_need_migration": 0, "would_fail_validation": 0},
        {"track_id": "t2", "would_need_migration": 0, "would_fail_validation": 0},
    ]
    assert detect_unhandled_breaks(impacts=impacts, migrations=[]) == []


def test_reject_gate_break_without_migration_rejects():
    """would_need_migration > 0 AND no migrations declared → rejected."""
    impacts = [
        {"track_id": "t1", "would_need_migration": 3, "would_fail_validation": 0},
    ]
    unhandled = detect_unhandled_breaks(impacts=impacts, migrations=[])
    assert len(unhandled) == 1
    assert "t1" in unhandled[0]
    assert "3 entries" in unhandled[0]


def test_reject_gate_validation_failure_rejects():
    """would_fail_validation > 0 (without any migration ops) → rejected."""
    impacts = [
        {"track_id": "t1", "would_need_migration": 0, "would_fail_validation": 5},
    ]
    unhandled = detect_unhandled_breaks(impacts=impacts, migrations=[])
    assert len(unhandled) == 1
    assert "would fail validation" in unhandled[0]


def test_reject_gate_break_with_migration_passes():
    """Author declared a migration op → trust the author (v1 conservative)."""
    impacts = [
        {"track_id": "t1", "would_need_migration": 5, "would_fail_validation": 0},
    ]
    migrations = [
        {
            "from_version": "v1",
            "to_version": "v1.1",
            "ops": [
                {
                    "op": "rename_field",
                    "entry_type": "task",
                    "from": "prio",
                    "to": "priority",
                }
            ],
        },
    ]
    assert detect_unhandled_breaks(impacts=impacts, migrations=migrations) == []


def test_reject_gate_multiple_tracks_with_breaks_reports_all():
    impacts = [
        {"track_id": "t1", "would_need_migration": 1, "would_fail_validation": 0},
        {"track_id": "t2", "would_need_migration": 2, "would_fail_validation": 0},
        {"track_id": "t3", "would_need_migration": 0, "would_fail_validation": 0},
    ]
    unhandled = detect_unhandled_breaks(impacts=impacts, migrations=[])
    assert len(unhandled) == 2
    assert any("t1" in u for u in unhandled)
    assert any("t2" in u for u in unhandled)
    assert not any("t3" in u for u in unhandled)


def test_reject_gate_handles_malformed_op_entries():
    """Empty op strings + non-dict entries do not crash + are not counted."""
    impacts = [
        {"track_id": "t1", "would_need_migration": 1, "would_fail_validation": 0},
    ]
    migrations = [
        {"ops": [{"op": ""}, "garbage", {}, None]},
    ]
    # No real op declared — should still reject.
    unhandled = detect_unhandled_breaks(impacts=impacts, migrations=migrations)
    assert len(unhandled) == 1


def test_reject_gate_handles_none_inputs():
    """None inputs are tolerated as empty lists."""
    assert detect_unhandled_breaks(impacts=None, migrations=None) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# publish_draft — reject gate + force flag integration
# ---------------------------------------------------------------------------


def _make_draft():
    d = MagicMock()
    d.id = "draft-1"
    d.status = "draft"
    d.draft_of_id = "published-1"
    d.manifest = {"scope": "track", "track": {"entry_types": []}}
    d.version_label = "test"
    d.signature = {}
    d.save = AsyncMock()
    return d


def _make_published():
    p = MagicMock()
    p.id = "published-1"
    p.status = "published"
    p.scope = "track"
    p.manifest = {}
    p.version_number = 1
    p.version_label = ""
    p.published_id = ""
    p.parent_version_id = None
    p.published_at = None
    p.updated_at = None
    p.signature = {}
    p.migration_status = "complete"
    p.save = AsyncMock()
    return p


@pytest.mark.asyncio
async def test_publish_draft_raises_422_on_unhandled_break():
    """force=False + unhandled break → BadRequestError with details.unhandled_breaks."""
    from app.services.content_profile_atomic_swap import publish_draft

    draft = _make_draft()
    published = _make_published()

    impacts = [
        {"track_id": "t1", "would_need_migration": 3, "would_fail_validation": 0},
    ]

    with (
        patch(
            "app.services.content_profile_atomic_swap.compile_canonical_manifest",
            return_value={
                "scope": "track",
                "track": {"entry_types": []},
                "migrations": [],
            },
        ),
        patch(
            "app.services.content_profile_diff.compute_entry_impact_for_attached",
            new=AsyncMock(return_value=impacts),
        ),
    ):
        with pytest.raises(BadRequestError) as excinfo:
            await publish_draft(draft=draft, published=published, force=False)

    assert "no migration path" in excinfo.value.message
    # The error carries the structured detail payload
    assert "unhandled_breaks" in (excinfo.value.details or {})
    assert excinfo.value.details["unhandled_breaks"]
    # Swap did NOT happen — published manifest unchanged
    assert published.manifest == {}


@pytest.mark.asyncio
async def test_publish_draft_force_bypasses_reject_gate():
    """force=True bypasses reject gate; tracker reports skipped."""
    from app.services.content_profile_atomic_swap import publish_draft

    draft = _make_draft()
    published = _make_published()
    impacts = [
        {"track_id": "t1", "would_need_migration": 3, "would_fail_validation": 0},
    ]

    with (
        patch(
            "app.services.content_profile_atomic_swap.compile_canonical_manifest",
            return_value={
                "scope": "track",
                "track": {"entry_types": []},
                "migrations": [],
            },
        ),
        patch(
            "app.services.content_profile_diff.compute_entry_impact_for_attached",
            new=AsyncMock(return_value=impacts),
        ),
        patch(
            "app.services.content_profile_atomic_swap._sync_entry_type_form_schemas",
            new=AsyncMock(),
        ),
        patch(
            "app.services.content_profile_atomic_swap.emit_change_event",
            new=AsyncMock(),
        ),
        patch(
            "app.services.content_profile_atomic_swap.invalidate_manifest_cache",
        ),
    ):
        result = await publish_draft(draft=draft, published=published, force=True)

    # Forced publish DID swap (published.save called) but did NOT migrate.
    assert result["migration_run"]["executed"] is False
    assert "force_publish" in result["migration_run"]["skipped_reason"]
    assert result["migration_tracker"]["executed"] is False
    published.save.assert_awaited()


@pytest.mark.asyncio
async def test_publish_draft_passes_when_migration_declared():
    """Manifest with declared migrations[].ops passes the reject gate."""
    from app.services.content_profile_atomic_swap import publish_draft

    draft = _make_draft()
    published = _make_published()
    impacts = [
        {"track_id": "t1", "would_need_migration": 3, "would_fail_validation": 0},
    ]
    compiled = {
        "scope": "track",
        "track": {"entry_types": []},
        "migrations": [
            {
                "ops": [
                    {"op": "rename_field", "entry_type": "t", "from": "a", "to": "b"}
                ]
            },
        ],
    }

    with (
        patch(
            "app.services.content_profile_atomic_swap.compile_canonical_manifest",
            return_value=compiled,
        ),
        patch(
            "app.services.content_profile_diff.compute_entry_impact_for_attached",
            new=AsyncMock(return_value=impacts),
        ),
        patch(
            "app.services.content_profile_atomic_swap._sync_entry_type_form_schemas",
            new=AsyncMock(),
        ),
        patch(
            "app.services.content_profile_atomic_swap.emit_change_event",
            new=AsyncMock(),
        ),
        patch(
            "app.services.content_profile_atomic_swap.invalidate_manifest_cache",
        ),
        patch(
            "app.services.migrations.runner.run_migration_async",
            new=AsyncMock(
                return_value={
                    "status": "running",
                    "affected_entry_count": 3,
                }
            ),
        ),
    ):
        result = await publish_draft(
            draft=draft,
            published=published,
            force=False,
            await_runner=False,
        )

    assert result["migration_run"]["executed"] is True
    assert result["migration_tracker"]["executed"] is True
    # Async tracker shape
    assert "status" in result["migration_tracker"]


@pytest.mark.asyncio
async def test_publish_draft_force_param_default_is_false():
    """Default behaviour preserves the prior reject-gate path."""
    import inspect

    from app.services.content_profile_atomic_swap import publish_draft

    sig = inspect.signature(publish_draft)
    assert sig.parameters["force"].default is False


# ---------------------------------------------------------------------------
# Policy gate — migration.publish vs migration.force_publish separation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_endpoint_evaluates_migration_publish_by_default():
    """Default publish path calls policy_engine with action='migration.publish'."""
    from app.api import content_profiles as cp_api
    from app.schemas.policy import Decision

    draft = _make_draft()
    parent = _make_published()
    request = MagicMock()

    captured = {}

    async def fake_evaluate(*, subject, action, resource, **_):
        captured["action"] = action
        return Decision(allowed=True, reason="default_human_policy")

    with (
        patch.object(cp_api, "resolve_principal_id", return_value="user-1"),
        patch(
            "app.models.nodes.ContentProfile.get",
            new=AsyncMock(side_effect=[draft, parent]),
        ),
        patch.object(
            cp_api, "_resolve_cp_edit_permission", new=AsyncMock(return_value=True)
        ),
        patch.object(cp_api, "policy_evaluate", new=fake_evaluate),
        patch(
            "app.services.content_profile_atomic_swap.publish_draft",
            new=AsyncMock(
                return_value={
                    "published_id": parent.id,
                    "version_number": 2,
                    "migration_run": {"executed": False},
                    "migration_tracker": {"executed": False},
                }
            ),
        ),
    ):
        await cp_api.publish_content_profile_draft(
            request=request,
            content_profile_id=draft.id,
        )
    assert captured["action"] == "migration.publish"


@pytest.mark.asyncio
async def test_publish_endpoint_evaluates_force_publish_when_force_true():
    """force=True evaluates the separate 'migration.force_publish' action."""
    from app.api import content_profiles as cp_api
    from app.schemas.policy import Decision

    draft = _make_draft()
    parent = _make_published()
    request = MagicMock()

    captured = {}

    async def fake_evaluate(*, subject, action, resource, **_):
        captured["action"] = action
        return Decision(allowed=True, reason="default_human_policy")

    with (
        patch.object(cp_api, "resolve_principal_id", return_value="user-1"),
        patch(
            "app.models.nodes.ContentProfile.get",
            new=AsyncMock(side_effect=[draft, parent]),
        ),
        patch.object(
            cp_api, "_resolve_cp_edit_permission", new=AsyncMock(return_value=True)
        ),
        patch.object(cp_api, "policy_evaluate", new=fake_evaluate),
        patch(
            "app.services.content_profile_atomic_swap.publish_draft",
            new=AsyncMock(
                return_value={
                    "published_id": parent.id,
                    "version_number": 2,
                    "migration_run": {"executed": False},
                    "migration_tracker": {"executed": False},
                }
            ),
        ),
    ):
        await cp_api.publish_content_profile_draft(
            request=request,
            content_profile_id=draft.id,
            force=True,
        )
    assert captured["action"] == "migration.force_publish"


@pytest.mark.asyncio
async def test_publish_endpoint_denied_when_policy_denies():
    """Policy denial → InsufficientPermissionsError, publish_draft never invoked."""
    from app.api import content_profiles as cp_api
    from app.api.errors import InsufficientPermissionsError
    from app.schemas.policy import Decision

    draft = _make_draft()
    parent = _make_published()
    request = MagicMock()

    publish_called = {"n": 0}

    async def fake_evaluate(*, subject, action, resource, **_):
        return Decision(allowed=False, reason="default_human_policy_denied")

    async def fake_publish(*args, **kwargs):
        publish_called["n"] += 1
        return {}

    with (
        patch.object(cp_api, "resolve_principal_id", return_value="user-1"),
        patch(
            "app.models.nodes.ContentProfile.get",
            new=AsyncMock(side_effect=[draft, parent]),
        ),
        patch.object(
            cp_api, "_resolve_cp_edit_permission", new=AsyncMock(return_value=True)
        ),
        patch.object(cp_api, "policy_evaluate", new=fake_evaluate),
        patch(
            "app.services.content_profile_atomic_swap.publish_draft", new=fake_publish
        ),
    ):
        with pytest.raises(InsufficientPermissionsError):
            await cp_api.publish_content_profile_draft(
                request=request,
                content_profile_id=draft.id,
                force=True,
            )
    assert publish_called["n"] == 0


# ---------------------------------------------------------------------------
# Single-Literal grep gates (Tests 13 + 14) + provenance free-string guard.
# These are runtime-executable equivalents of the verification rules listed
# at the bottom of the plan; they fail loudly if 05-02 accidentally
# introduces a Literal definition or a free-string provenance form.
# ---------------------------------------------------------------------------


def _backend_root() -> Path:
    return Path(__file__).resolve().parent.parent  # tests/.. == backend/


def _grep_count(pattern: str, glob: str = "app/**/*.py") -> int:
    """Count lines matching the regex pattern under app/, excluding __pycache__.

    Uses Python ``re`` rather than shelling out to ``grep`` so the gate is
    portable across GNU grep builds that do not treat ``\\s`` as whitespace.
    """
    import re

    root = _backend_root() / "app"
    rx = re.compile(pattern)
    count = 0
    for py in root.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        for line in py.read_text(encoding="utf-8").splitlines():
            if rx.search(line):
                count += 1
    return count


def test_single_literal_grep_gates():
    """05-02 adds ZERO new Literal members — grep gates stay at 1 match each."""
    # PolicyAction
    n_policy = _grep_count(r"^PolicyAction\s*=\s*Literal")
    assert (
        n_policy == 1
    ), f"PolicyAction Literal defined in {n_policy} places (expected 1)"
    # ChangeEventAction
    n_audit = _grep_count(r"^ChangeEventAction\s*=\s*Literal")
    assert (
        n_audit == 1
    ), f"ChangeEventAction Literal defined in {n_audit} places (expected 1)"
    # ActorKind
    n_actor = _grep_count(r"^ActorKind\s*=\s*Literal")
    assert n_actor == 1, f"ActorKind Literal defined in {n_actor} places (expected 1)"


def test_no_connector_provenance_free_string_in_migrations():
    """I-CON-01 guard — no free-string ``provenance.source = "connector:..."``
    introduced under the new migrations subpackage."""
    pkg = _backend_root() / "app" / "services" / "migrations"
    if not pkg.exists():
        pytest.skip("migrations package not present")
    pattern = re.compile(r'provenance\.source\s*=\s*"connector:')
    hits = []
    for path in pkg.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if pattern.search(text):
            hits.append(str(path))
    assert hits == [], f"Free-string provenance.source found in: {hits}"
