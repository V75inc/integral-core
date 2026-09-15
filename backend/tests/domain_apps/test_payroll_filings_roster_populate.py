"""Tests for "Populate from HR" (``app/profiles/payroll_filings/tools/
roster_populate.py``) — now a real, direct-create operation via
``ToolContext.create_entry`` (added for Wave 1 payroll completion), run
BOTH as an ``entry.create`` hook (a brand-new filing gets every active
employee's line automatically — the actual complaint this fixes: opening a
new filing showed zero lines until "Populate from HR" was clicked by hand)
and as the standalone action-bar button (to catch up anyone hired after
the filing already exists).

Real installs (hr_app + payroll_filings), not hand-rolled fixtures —
``create_entry`` does real schema validation + anchor auto-provisioning,
so a filing needs to go through the real creation path (via
``ToolContext.create_entry`` itself, exactly like the real API) to get a
correctly-schema'd employee_lines_track to populate into.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.edges import CONTAINS, IS_MEMBER_OF, OWNS
from app.models.nodes import (
    App,
    ContentProfile,
    Entry,
    EntryType,
    Track,
    User,
    Workspace,
)
from app.profiles.payroll_app.tools.roster_populate import (
    populate_from_hr_roster,
)
from app.services.app_graph import catalog_user
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


async def _make_workspace() -> Workspace:
    now = utc_now_iso()
    return await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="WS Roster Populate Test",
        name_fold="ws roster populate test",
        created_at=now,
        updated_at=now,
    )


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


async def _provision_workspace():
    ws = await _make_workspace()

    # A real owner — anchor auto-provisioning (materialize_anchor_track,
    # fired when creating a nis_schedule/paye_filing entry, since
    # employee_lines_track is an auto_provision relation field) does a real
    # governance check against the acting user's edit rights on the track,
    # not just a bare id string. Mirrors test_payroll_privilege.py's own
    # founder/OWNS pattern.
    owner = await User.create(user_id="u_owner", display_name="Owner Account")
    await catalog_user(owner)
    await owner.connect(ws, edge=OWNS, created_at=utc_now_iso())
    await owner.connect(ws, edge=IS_MEMBER_OF, role="admin", joined_at=utc_now_iso())

    hr_lib = await _load_library_cp("hr_app")
    payroll_lib = await _load_library_cp("payroll-app")
    hr_result = await install_app(
        workspace_id=ws.id, library_cp_id=hr_lib.id, actor_id=owner.id
    )
    # NIS/PAYE filings merged into payroll-app (formerly the standalone
    # payroll_filings package) — no separate app to install; the Filings
    # track comes along with Payroll itself.
    payroll_result = await install_app(
        workspace_id=ws.id, library_cp_id=payroll_lib.id, actor_id=owner.id, settings={}
    )

    hr_app = await App.get(hr_result["app_id"])
    hr_tracks = await hr_app.nodes(edge=[CONTAINS], node=["Track"])
    employees_track = next(
        t for t in hr_tracks if getattr(t, "title", "") == "Employees"
    )

    payroll_app = await App.get(payroll_result["app_id"])
    payroll_tracks = await payroll_app.nodes(edge=[CONTAINS], node=["Track"])
    filings_track = next(
        t for t in payroll_tracks if getattr(t, "title", "") == "Filings"
    )

    # Employee's `member` field is a required, workspace-gated User
    # reference (links an employee record to a login account) — one real
    # member reused across every test employee is fine here; nothing under
    # test cares about member uniqueness, only that the employee entries
    # themselves exist and get correctly populated onto filings.
    member_user = await User.create(user_id="u_member", display_name="Member Account")
    await catalog_user(member_user)
    await member_user.connect(
        ws, edge=IS_MEMBER_OF, role="member", joined_at=utc_now_iso()
    )

    return ws, employees_track, filings_track, member_user.id, owner.id


async def _make_employee(
    ctx: ToolContext,
    employees_track_id: str,
    member_id: str,
    *,
    title: str,
    status: str,
) -> Entry:
    entry = await ctx.create_entry(
        track_id=employees_track_id,
        entry_type_key="employee",
        title=title,
        custom_fields={"status": status, "member": member_id},
    )
    assert entry is not None
    return entry


async def _mirror_id(ctx: ToolContext, hrm_employee_id: str) -> str:
    """Guyana Payroll Employees is now the roster ``populate_from_hr_roster`` links
    filing lines against (decoupled from hr_app — see ``tools/hrm_sync.py``),
    so an hr_app Employee's OWN id never shows up on a filing line; its
    mirrored Guyana Payroll Employees record's id does. The
    ``mirror_employee_from_hrm`` hook creates that mirror synchronously off
    the same ``entry.create`` this test's ``_make_employee`` fires, so it's
    always present by the time this is called."""
    for rec in await ctx.find_entries_in_track_type("Guyana Payroll Employees"):
        if (rec.custom_fields or {}).get("hrm_source_employee_id") == hrm_employee_id:
            return rec.id
    raise AssertionError(
        f"no Guyana Payroll Employees mirror found for hr_app employee {hrm_employee_id!r}"
    )


async def _make_filing(
    ctx: ToolContext,
    filings_track: Track,
    owner_id: str,
    *,
    entry_type_key: str,
    template_key: str,
    title: str,
    custom_fields: dict,
) -> Entry:
    """A filing is created by a real HUMAN via the real API in production
    (``POST /entries``, ``subject.kind="human"``) — never via
    ``ToolContext.create_entry`` (``subject.kind="agent"``), which has no
    default-allow path for ``anchor.create`` governance (agents need an
    explicit Policy grant a fresh workspace doesn't have; humans get the
    can_admin_track fallback). Mirrors the real create-entry flow closely
    enough to matter here: resolve the EntryType, materialize the anchor
    track directly (same call entries.py's real create path makes), then
    fire entry.create hooks — which is what actually runs
    populate_from_hr_roster automatically, the behavior under test.
    """
    from app.models.edges import AUTHORED_BY, CONTAINS, IS_OF_TYPE
    from app.services.content_profile_graph import materialize_anchor_track
    from app.services.hooks.entry_save_runtime import run_entry_save_hooks
    from app.utils.time import utc_now_iso as _now

    entry_types = await EntryType.find({"context.track_id": filings_track.id})
    entry_type = next(
        et
        for et in entry_types
        if (et.form_schema or {}).get("_manifest_entry_type_key") == entry_type_key
    )

    anchor_track = await materialize_anchor_track(
        source_track=filings_track,
        template_key=template_key,
        field_key="employee_lines_track",
        actor_user_id=owner_id,
        actor_kind="human",
        source_entry_title=title,
    )

    now = _now()
    entry = await Entry.create(
        track_id=filings_track.id,
        type_id=entry_type.id,
        title=title,
        author_id=owner_id,
        status="active",
        custom_fields={**custom_fields, "employee_lines_track": anchor_track.id},
        created_at=now,
        updated_at=now,
    )
    await filings_track.connect(entry, edge=CONTAINS, added_at=now)
    await entry.connect(entry_type, edge=IS_OF_TYPE, assigned_at=now)
    user = await User.get(owner_id)
    if user:
        await entry.connect(user, edge=AUTHORED_BY, authored_at=now)

    await run_entry_save_hooks(
        entry=entry,
        workspace_id=ctx.workspace_id,
        actor_id=owner_id,
        hook_point="entry.create",
    )
    return await Entry.get(
        entry.id
    )  # hooks (carry_forward header, populate) may have updated it


async def _make_nis_filing(
    ctx: ToolContext, filings_track: Track, owner_id: str, *, title: str
) -> Entry:
    return await _make_filing(
        ctx,
        filings_track,
        owner_id,
        entry_type_key="nis_schedule",
        template_key="nis-schedule-lines",
        title=title,
        custom_fields={
            "contribution_year": 2026,
            "contribution_month": "June",
            "schedule_type": "Monthly",
        },
    )


async def _make_paye_filing(
    ctx: ToolContext, filings_track: Track, owner_id: str, *, title: str
) -> Entry:
    return await _make_filing(
        ctx,
        filings_track,
        owner_id,
        entry_type_key="paye_filing",
        template_key="paye-filing-lines",
        title=title,
        custom_fields={"year": 2026, "period": 6},
    )


@pytest.mark.asyncio
async def test_creating_a_nis_filing_auto_populates_active_employees_via_hook():
    ws, employees_track, filings_track, member_id, owner_id = (
        await _provision_workspace()
    )
    ctx = ToolContext(user_id=owner_id, workspace_id=ws.id, scope="test")

    active_a = await _make_employee(
        ctx, employees_track.id, member_id, title="Alice", status="active"
    )
    active_b = await _make_employee(
        ctx, employees_track.id, member_id, title="Bob", status="active"
    )
    terminated = await _make_employee(
        ctx, employees_track.id, member_id, title="Cara", status="terminated"
    )

    filing = await _make_nis_filing(
        ctx, filings_track, owner_id, title="2026-06 NIS Schedule"
    )

    lines_track_id = (filing.custom_fields or {}).get("employee_lines_track")
    assert lines_track_id
    lines = await ctx.find_entries({"track_id": lines_track_id})
    linked_employee_ids = {(l.custom_fields or {}).get("employee") for l in lines}
    # hr_app's install seeds its own placeholder roster (all active by
    # default), so a fresh workspace is never actually empty — assert on
    # membership, not exact equality, and confirm active vs. terminated
    # status is genuinely respected rather than "everything got linked".
    # Filing lines link against Guyana Payroll Employees (this app's own roster),
    # not hr_app's Employee ids directly — resolve each hr_app employee's
    # mirrored id first.
    assert await _mirror_id(ctx, active_a.id) in linked_employee_ids
    assert await _mirror_id(ctx, active_b.id) in linked_employee_ids
    assert await _mirror_id(ctx, terminated.id) not in linked_employee_ids


@pytest.mark.asyncio
async def test_creating_a_paye_filing_auto_populates_active_employees_via_hook():
    ws, employees_track, filings_track, member_id, owner_id = (
        await _provision_workspace()
    )
    ctx = ToolContext(user_id=owner_id, workspace_id=ws.id, scope="test")

    active = await _make_employee(
        ctx, employees_track.id, member_id, title="Dana", status="active"
    )

    filing = await _make_paye_filing(
        ctx, filings_track, owner_id, title="2026-06 PAYE Filing"
    )

    lines_track_id = (filing.custom_fields or {}).get("employee_lines_track")
    lines = await ctx.find_entries({"track_id": lines_track_id})
    linked_employee_ids = {(l.custom_fields or {}).get("employee") for l in lines}
    assert await _mirror_id(ctx, active.id) in linked_employee_ids


@pytest.mark.asyncio
async def test_manual_button_catches_up_employee_hired_after_filing_created():
    ws, employees_track, filings_track, member_id, owner_id = (
        await _provision_workspace()
    )
    ctx = ToolContext(user_id=owner_id, workspace_id=ws.id, scope="test")

    await _make_employee(
        ctx, employees_track.id, member_id, title="Eli", status="active"
    )
    filing = await _make_nis_filing(
        ctx, filings_track, owner_id, title="2026-06 NIS Schedule"
    )
    # Snapshot how many lines the hook itself created (hr_app's seeded
    # roster plus Eli) before the late hire shows up, so the assertions
    # below don't hardcode a headcount that depends on hr_app's seed data.
    lines_track_id = (filing.custom_fields or {}).get("employee_lines_track")
    lines_from_hook = await ctx.find_entries({"track_id": lines_track_id})
    count_from_hook = len(lines_from_hook)

    # Hired after the filing (and its auto-populate hook) already ran.
    late_hire = await _make_employee(
        ctx, employees_track.id, member_id, title="Farah", status="active"
    )

    result = await populate_from_hr_roster({"entry_id": filing.id}, ctx)
    assert result["ok"] is True
    assert (
        result["added_count"] == 1
    )  # only the late hire — everyone else already on it from the hook
    assert result["skipped_existing"] == count_from_hook

    lines = await ctx.find_entries({"track_id": lines_track_id})
    linked_employee_ids = {(l.custom_fields or {}).get("employee") for l in lines}
    assert await _mirror_id(ctx, late_hire.id) in linked_employee_ids
    assert len(lines) == count_from_hook + 1


@pytest.mark.asyncio
async def test_rerunning_manually_never_double_adds():
    ws, employees_track, filings_track, member_id, owner_id = (
        await _provision_workspace()
    )
    ctx = ToolContext(user_id=owner_id, workspace_id=ws.id, scope="test")

    await _make_employee(
        ctx, employees_track.id, member_id, title="Gita", status="active"
    )
    filing = await _make_nis_filing(
        ctx, filings_track, owner_id, title="2026-06 NIS Schedule"
    )

    lines_track_id = (filing.custom_fields or {}).get("employee_lines_track")
    lines_from_hook = await ctx.find_entries({"track_id": lines_track_id})
    count_from_hook = len(lines_from_hook)

    result = await populate_from_hr_roster({"entry_id": filing.id}, ctx)
    assert result["ok"] is True
    assert result["added_count"] == 0
    assert result["skipped_existing"] == count_from_hook

    lines = await ctx.find_entries({"track_id": lines_track_id})
    assert len(lines) == count_from_hook


@pytest.mark.asyncio
async def test_no_entry_id_is_clean_failure():
    result = await populate_from_hr_roster(
        {}, ToolContext(user_id="t", workspace_id="w", scope="test")
    )
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_filing_not_found_is_clean_failure():
    ws = await _make_workspace()
    ctx = ToolContext(user_id="u_1", workspace_id=ws.id, scope="test")
    result = await populate_from_hr_roster({"entry_id": "n.Entry.does-not-exist"}, ctx)
    assert result["ok"] is False
