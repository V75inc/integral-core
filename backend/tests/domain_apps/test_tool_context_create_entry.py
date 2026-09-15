"""``ToolContext.create_entry`` — the one write primitive bundle tools were
missing (Wave 1 payroll completion: ``generate_payslips_for_pay_run`` needs
to create one real Payslip record per employee, not just attach a file).

Real DB, real install — not mocks — because the thing actually worth
proving is that relation fields (Payslip's ``pay_run``/``compensation``)
materialize real ``REFERENCES`` edges through
``validate_and_materialize_entry_custom_fields``, the same as the real
``POST /entries`` endpoint — not just land as bare id strings in
``custom_fields`` that only look linked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.edges import CONTAINS, IS_MEMBER_OF, REFERENCES
from app.models.nodes import (
    App,
    ContentProfile,
    Entry,
    EntryType,
    Track,
    User,
    Workspace,
)
from app.services.app_lifecycle import install_app
from app.services.content_profile_loader import load_library_profiles_with_issues
from app.services.content_profile_plugins import (
    discover_and_register_plugins,
    reset_discovered_for_tests,
)
from app.services.hooks.registry import ToolContext
from app.utils.time import utc_now_iso
from app.views import content_profile_view_types as view_types


@pytest.fixture(scope="module", autouse=True)
def _view_types():
    view_types._REGISTRY.pop("editable_table", None)
    view_types._REGISTRY.pop("action_bar", None)
    reset_discovered_for_tests()
    discover_and_register_plugins()
    yield
    view_types._REGISTRY.pop("editable_table", None)
    view_types._REGISTRY.pop("action_bar", None)
    reset_discovered_for_tests()


async def _make_workspace() -> tuple[Workspace, str]:
    """Returns ``(ws, owner_id)`` — a REAL resolvable owner, not the bare
    ``"u_1"`` string every call site here used to pass straight into both
    ``install_app`` and ``ToolContext(user_id=...)``. Two distinct bugs
    that used to hide behind that same fake id:
    (1) install_app's wire_app_owner needs an actor that resolves to a
        real workspace membership or it raises AppOwnerWireError outright.
    (2) ToolContext.create_entry's OWN role check
        (``resolve_role(self.user_id, "track", track_id) in ("owner",
        "editor")``) returns None — silently no-opping the create — for
        an id with no real relationship to the workspace at all. A fake
        actor id doesn't just risk failing loudly (bug 1); it can also
        fail *silently*, which is worse.
    """
    now = utc_now_iso()
    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="WS Create Entry Test",
        name_fold="ws create entry test",
        created_at=now,
        updated_at=now,
    )
    owner = await User.create(name="WS Create Entry Test owner")
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner", joined_at=now)
    return ws, owner.id


async def _load_library_cp(slug: str) -> ContentProfile:
    specs, _ = load_library_profiles_with_issues(profiles_root=Path("app/profiles"))
    spec = next(s for s in specs if s.slug == slug)
    manifest = dict(spec.manifest)
    pkg = dict(manifest.get("package") or {})
    pkg["version"] = spec.version or pkg.get("version") or "1.0.0"
    manifest["package"] = pkg
    now = utc_now_iso()
    return await ContentProfile.create(
        name=manifest["package"]["name"],
        scope="app",
        manifest=manifest,
        library_package=True,
        version=manifest["package"]["version"],
        created_at=now,
        updated_at=now,
    )


async def _provision_payroll_workspace():
    ws, owner_id = await _make_workspace()
    hr_lib = await _load_library_cp("hr_app")
    payroll_lib = await _load_library_cp("payroll-app")
    hr_result = await install_app(
        workspace_id=ws.id, library_cp_id=hr_lib.id, actor_id=owner_id
    )
    payroll_result = await install_app(
        workspace_id=ws.id, library_cp_id=payroll_lib.id, actor_id=owner_id, settings={}
    )
    payroll_app = await App.get(payroll_result["app_id"])
    tracks = await payroll_app.nodes(edge=[CONTAINS], node=["Track"])
    payslips_track = next(
        t for t in tracks if getattr(t, "title", "") == "Guyana Payslips"
    )
    pay_runs_track = next(
        t for t in tracks if getattr(t, "title", "") == "Guyana Pay Runs"
    )
    comp_track = next(
        t for t in tracks if getattr(t, "title", "") == "Guyana Compensation Records"
    )

    def _et_by_key(entry_types, key):
        return next(
            et
            for et in entry_types
            if (getattr(et, "form_schema", None) or {}).get("_manifest_entry_type_key")
            == key
        )

    now = utc_now_iso()
    pay_run_et = _et_by_key(
        await EntryType.find({"context.track_id": pay_runs_track.id}), "pay_run"
    )
    pay_run = await Entry.create(
        title="2026-06 pay run",
        body="",
        tags=[],
        custom_fields={"period_start": "2026-06-01"},
        type_id=pay_run_et.id,
        track_id=pay_runs_track.id,
        author_id="u_1",
        status="active",
        created_at=now,
        updated_at=now,
    )
    await pay_runs_track.connect(pay_run, edge=CONTAINS, added_at=now)

    comp_et = _et_by_key(
        await EntryType.find({"context.track_id": comp_track.id}), "compensation_record"
    )
    comp = await Entry.create(
        title="Comp record",
        body="",
        tags=[],
        custom_fields={"base_salary": 200000},
        type_id=comp_et.id,
        track_id=comp_track.id,
        author_id="u_1",
        status="active",
        created_at=now,
        updated_at=now,
    )
    await comp_track.connect(comp, edge=CONTAINS, added_at=now)

    return ws, payslips_track, pay_run, comp, owner_id


@pytest.mark.asyncio
async def test_create_entry_creates_real_entry_with_contains_edge():
    ws, payslips_track, pay_run, comp, owner_id = await _provision_payroll_workspace()
    ctx = ToolContext(user_id=owner_id, workspace_id=ws.id, scope="test")

    entry = await ctx.create_entry(
        track_id=payslips_track.id,
        entry_type_key="payslip",
        title="2026-06 — Test Employee",
        custom_fields={"gross": 200000, "deductions": 20000, "net": 180000},
    )

    assert entry is not None
    assert entry.custom_fields["gross"] == 200000
    reloaded = await Entry.get(entry.id)
    assert reloaded is not None
    assert reloaded.title == "2026-06 — Test Employee"

    contained = await payslips_track.nodes(edge=[CONTAINS], node=["Entry"])
    assert any(e.id == entry.id for e in contained)


@pytest.mark.asyncio
async def test_create_entry_materializes_relation_fields_as_references_edges():
    ws, payslips_track, pay_run, comp, owner_id = await _provision_payroll_workspace()
    ctx = ToolContext(user_id=owner_id, workspace_id=ws.id, scope="test")

    entry = await ctx.create_entry(
        track_id=payslips_track.id,
        entry_type_key="payslip",
        title="2026-06 — Test Employee",
        custom_fields={
            "pay_run": pay_run.id,
            "compensation": comp.id,
            "gross": 200000,
            "deductions": 20000,
            "net": 180000,
        },
    )

    assert entry is not None
    referenced = await entry.nodes(edge=[REFERENCES], node=["Entry"], direction="out")
    referenced_ids = {e.id for e in referenced}
    assert pay_run.id in referenced_ids
    assert comp.id in referenced_ids


@pytest.mark.asyncio
async def test_create_entry_returns_none_for_track_outside_workspace():
    ws, payslips_track, pay_run, comp, owner_id = await _provision_payroll_workspace()
    other_ws, _other_owner_id = await _make_workspace()
    ctx = ToolContext(user_id=owner_id, workspace_id=other_ws.id, scope="test")

    entry = await ctx.create_entry(
        track_id=payslips_track.id,
        entry_type_key="payslip",
        title="Should not create",
    )
    assert entry is None


@pytest.mark.asyncio
async def test_find_track_id_by_title_skips_uninstalled_apps_duplicate_track():
    """Regression: repeated install/uninstall/reinstall of the same App
    leaves its old Tracks behind (same title, not deleted, parent App
    lifecycle_state="uninstalled"). A raw title match with no lifecycle
    filter can resolve to the stale orphan instead of the live Track —
    caught live when generate_payslips_for_pay_run's create_entry call hit
    an uninstalled Payroll app's leftover Payslips Track whose EntryType
    never got the current session's field additions."""
    ws, payslips_track, pay_run, comp, owner_id = await _provision_payroll_workspace()

    # Simulate the exact real-world shape: uninstall this Payroll app...
    payroll_lib2 = await _load_library_cp("payroll-app")
    parents = await payslips_track.nodes(
        edge=[CONTAINS], node=["WorkspaceApp"], direction="in"
    )
    old_payroll_app = parents[0]
    old_payroll_app.lifecycle_state = "uninstalled"
    await old_payroll_app.save()

    # ...then reinstall it fresh (its own new Payslips Track, same title).
    reinstall_result = await install_app(
        workspace_id=ws.id, library_cp_id=payroll_lib2.id, actor_id="u_1", settings={}
    )
    new_payroll_app = await App.get(reinstall_result["app_id"])
    new_tracks = await new_payroll_app.nodes(edge=[CONTAINS], node=["Track"])
    new_payslips_track = next(
        t for t in new_tracks if getattr(t, "title", "") == "Guyana Payslips"
    )
    assert new_payslips_track.id != payslips_track.id  # genuinely a different Track

    ctx = ToolContext(user_id=owner_id, workspace_id=ws.id, scope="test")
    resolved_id = await ctx.find_track_id_by_title("Guyana Payslips")
    assert resolved_id == new_payslips_track.id  # the LIVE one, not the orphan

    entries = await ctx.find_entries_in_track_type("Guyana Payslips")
    # Neither track has entries yet in this test, but the important thing
    # is the resolution didn't error trying to walk the orphaned track.
    assert entries == []
