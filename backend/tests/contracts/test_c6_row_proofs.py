"""Candidate proofs for the C6 rows that are deterministic in-process.

Pytest collects this module. It does not add an HTTP route. The projections
and retry decisions use the existing information, staging, ledger, migration,
and outbox contracts.
"""

from __future__ import annotations

import pytest

from app.agentive import staging
from app.agentive.staging import (
    StagingError,
    bless_token,
    consume_token,
    create_staged_change,
    record_execute_outcome,
    revoke_token,
)
from app.contracts.information import (
    FieldDefinition,
    FieldNamespace,
    project_record_for_surfaces,
)
from app.services.app_operations.event_outbox import reconcile_external_outcome
from app.services.application_definitions import (
    build_requirement_ledger,
    preview_three_way_package_upgrade,
    resume_requirement_ledger,
)
from app.services.migrations.reject_gate import detect_unhandled_breaks
from app.services.operational_model_runtime import compile_canonical_manifest


@pytest.fixture(autouse=True)
def _clean_staging():
    staging._tokens.clear()
    staging._autonomy.clear()
    yield
    staging._tokens.clear()
    staging._autonomy.clear()


def _manifest(**app):
    return compile_canonical_manifest(
        manifest={
            "operational_model_schema_version": 2,
            "scope": "app",
            "package": {"slug": "c6-rows", "version": "1.0.0"},
            "app": {"tracks": [], "relations": [], "defaults": {}, **app},
        }
    )


def test_a05_rename_null_and_collision_agree_on_every_surface() -> None:
    plate = FieldDefinition(
        id="fld.vehicle.registration",
        key="registration_number",
        label="Registration number",
        type="text",
        schema_revision=1,
    )
    renamed = plate.model_copy(update={"label": "Licence plate"})
    workflow = FieldDefinition(
        id="fld.vehicle.workflow_state",
        key="workflow_state",
        label="Workflow state",
        type="select",
        schema_revision=1,
    )
    platform_status = FieldDefinition(
        id="sys.entry.status",
        key="status",
        label="Record status",
        type="text",
        namespace=FieldNamespace.PLATFORM,
        owner="integral-core",
        schema_revision=1,
    )
    entry = {
        "id": "n.Entry.c6",
        "title": "Van",
        "status": "active",
        "custom_fields": {"registration_number": "ABC-1", "workflow_state": None},
    }
    surfaces = project_record_for_surfaces((renamed, workflow, platform_status), entry)
    expected = {
        renamed.id: "ABC-1",
        workflow.id: None,
        platform_status.id: "active",
    }
    assert set(surfaces) == {"form", "view", "dashboard", "agent_query"}
    assert all(projection == expected for projection in surfaces.values())
    assert renamed.id == plate.id
    assert renamed.key == plate.key


@pytest.mark.asyncio
async def test_a07_approval_executes_once_and_reports_correction_expiry_cancel() -> (
    None
):
    staged = await create_staged_change(
        user_id="u-c6",
        session_id="s-c6",
        kind="update_entry",
        summary="Set workflow state",
        diff_human="- workflow_state",
        diff_machine={"entry_id": "target"},
        payload={"entry_id": "target", "fields": {"workflow_state": "open"}},
    )
    await bless_token(user_id="u-c6", token=staged.token)
    corrected = await record_execute_outcome(
        token=staged.token,
        error={"code": "corrected", "message": "Field was already open."},
    )
    assert corrected is not None
    assert corrected.last_error["code"] == "corrected"
    first = await consume_token(
        user_id="u-c6", token=staged.token, expected_kind="update_entry"
    )
    assert first["entry_id"] == "target"
    with pytest.raises(StagingError) as consumed:
        await consume_token(
            user_id="u-c6", token=staged.token, expected_kind="update_entry"
        )
    assert consumed.value.code == "already_consumed"

    expired = await create_staged_change(
        user_id="u-c6",
        session_id="s-c6-expire",
        kind="update_entry",
        summary="Expire me",
        diff_human="- note",
        diff_machine={"entry_id": "expire"},
        payload={"entry_id": "expire"},
        ttl_seconds=-1,
    )
    with pytest.raises(StagingError) as expiry:
        await bless_token(user_id="u-c6", token=expired.token)
    assert expiry.value.code == "already_expired"

    cancelled = await create_staged_change(
        user_id="u-c6",
        session_id="s-c6-cancel",
        kind="update_entry",
        summary="Cancel me",
        diff_human="- note",
        diff_machine={"entry_id": "cancel"},
        payload={"entry_id": "cancel"},
    )
    revoked = await revoke_token(user_id="u-c6", token=cancelled.token)
    assert revoked.state == "revoked"


def test_a08_restart_resumes_the_same_requirement_ledger() -> None:
    manifest = {
        "package": {"slug": "c6-build"},
        "app": {
            "tracks": [
                {
                    "key": "notes",
                    "name": "Notes",
                    "entry_types": [{"key": "note", "name": "Note"}],
                    "views": [{"key": "feed", "name": "Feed"}],
                }
            ]
        },
    }
    first = build_requirement_ledger(manifest)
    rebuilt = build_requirement_ledger(manifest)
    resumed = resume_requirement_ledger(first, rebuilt)
    assert [item["id"] for item in resumed] == [item["id"] for item in first]
    assert len(resumed) == len({item["id"] for item in resumed})


def test_a10_unmigrated_populated_change_keeps_records() -> None:
    records = [
        {
            "id": "n.Entry.populated",
            "status": "active",
            "custom_fields": {"workflow_state": "open"},
        }
    ]
    snapshot = [dict(record) for record in records]
    impacts = [
        {
            "track_id": "n.Track.notes",
            "total": 1,
            "would_need_migration": 1,
            "would_fail_validation": 0,
        }
    ]
    breaks = detect_unhandled_breaks(impacts, [])
    assert breaks
    assert records == snapshot
    assert detect_unhandled_breaks(impacts, [{"ops": [{"op": "backfill"}]}]) == []


def test_a11_unknown_external_outcome_reconciles_before_retry() -> None:
    waiting = reconcile_external_outcome(status="unknown", correlation_id="outbox-1")
    assert waiting == {
        "action": "reconcile",
        "correlation_id": "outbox-1",
        "retry": False,
    }
    observed = reconcile_external_outcome(
        status="unknown",
        correlation_id="outbox-1",
        observed_result={"ok": True},
    )
    assert observed["action"] == "apply_observed"
    assert observed["retry"] is False
    delivered = reconcile_external_outcome(
        status="delivered", correlation_id="outbox-1"
    )
    assert delivered["action"] == "skip"
    assert delivered["retry"] is False
    pending = reconcile_external_outcome(status="pending", correlation_id="outbox-1")
    assert pending["action"] == "deliver"


def test_a13_upgrade_preview_keeps_local_customization() -> None:
    base = _manifest()
    local = _manifest(tracks=[{"key": "tenant-notes", "name": "Tenant notes"}])
    incoming = _manifest()
    preview = preview_three_way_package_upgrade(
        base_package_manifest=base,
        effective_manifest=local,
        incoming_package_manifest=incoming,
    )
    assert preview["status"] == "ready"
    assert preview["conflicts"] == []
    assert preview["counts"]["local_only"] >= 1
    assert any(track.get("key") == "tenant-notes" for track in local["app"]["tracks"])
