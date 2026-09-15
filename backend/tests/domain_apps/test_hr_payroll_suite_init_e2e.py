"""End-to-end test for the ``hr-payroll-suite`` workspace bundle.

Bundles ``hr_app`` and ``payroll-app`` into one atomically-installable
``scope: workspace`` manifest, mirroring the first such bundle
(``crm-pm-workspace``, see ``test_crm_pm_workspace_init_e2e.py``).

Guyana Payroll merge: the suite used to bundle THREE apps (``hr_app``,
``payroll-app``, ``payroll_filings``) — NIS/PAYE filings are now part of
``payroll-app`` itself (see ``payroll-app/profile.yaml``'s merged
``filings``/``company_profile`` tracks), so the suite is two apps.

Payroll is decoupled from HR (hr_app is a soft requires_apps dep —
Compensation Records link against payroll-app's own Payroll Employees
roster, never hr_app directly; see payroll-app/profile.yaml and
tools/hrm_sync.py). The bundle declares no ``cross_app_relations[]`` for
that reason — there is nothing cross-App left for
``_materialize_cross_app_relations`` to wire. (An earlier version of this
suite declared exactly one such entry, wiring
``payroll-app.compensation_record.employee`` -> ``hr_app.employee``
directly; removed when that relation stopped being cross-App.) This test
still asserts the ``employee`` field resolves to exactly one ``relation``
field on the installed ``compensation_record`` EntryType — now simply
proving payroll-app's own manifest declaration is the sole source, with
no separate materialization step in play at all.
"""

import pytest


@pytest.mark.asyncio
async def test_apply_hr_payroll_suite_creates_two_apps(authenticated_client):
    r = await authenticated_client.post(
        "/api/workspaces",
        json={"name": "Acme HR+Payroll", "profile_slug": "hr-payroll-suite"},
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    ws = body.get("workspace") or {}
    ws_id = ws.get("id")
    assert ws_id, f"workspace id missing in response: {body}"

    r2 = await authenticated_client.get(
        f"/api/workspaces/{ws_id}/apps",
        headers={"X-Integral-Scope": ws_id},
    )
    assert r2.status_code == 200, r2.text
    apps = r2.json().get("apps") or []
    names = sorted(a["name"] for a in apps)
    assert names == [
        "Guyana Payroll",
        "HRM",
    ], f"expected ['Guyana Payroll', 'HRM'], got {names}"


@pytest.mark.asyncio
async def test_hr_payroll_suite_apps_have_tracks(authenticated_client):
    r = await authenticated_client.post(
        "/api/workspaces",
        json={"name": "Acme HR+Payroll Tracks", "profile_slug": "hr-payroll-suite"},
    )
    assert r.status_code in (200, 201), r.text
    ws_id = (r.json().get("workspace") or {}).get("id")
    assert ws_id

    r2 = await authenticated_client.get(
        f"/api/workspaces/{ws_id}/apps",
        headers={"X-Integral-Scope": ws_id},
    )
    assert r2.status_code == 200, r2.text
    apps = r2.json().get("apps") or []
    assert len(apps) == 2, f"expected 2 apps, got {apps}"

    for app_name in ("HRM", "Guyana Payroll"):
        app = next((a for a in apps if a["name"] == app_name), None)
        assert app is not None, f"{app_name} App missing in {apps}"
        r3 = await authenticated_client.get(
            f"/api/apps/{app['id']}/tracks",
            headers={"X-Integral-Scope": ws_id},
        )
        assert r3.status_code == 200, r3.text
        tracks = r3.json().get("tracks") or []
        assert len(tracks) >= 1, f"expected >=1 track under {app_name}, got {tracks}"

    # Guyana Payroll's merged NIS/PAYE tracks come along for free — no
    # separate Payroll Filings app to install afterward.
    payroll_app = next(a for a in apps if a["name"] == "Guyana Payroll")
    r4 = await authenticated_client.get(
        f"/api/apps/{payroll_app['id']}/tracks",
        headers={"X-Integral-Scope": ws_id},
    )
    titles = {t.get("title") for t in (r4.json().get("tracks") or [])}
    assert {"Filings", "Guyana Company Profile"} <= titles, titles


@pytest.mark.asyncio
async def test_hr_payroll_suite_cross_app_relation_is_idempotent(authenticated_client):
    """No cross_app_relations[] entry is declared for this bundle any more
    (Payroll's own manifest is the sole source of the ``employee`` relation
    field) — exactly one ``employee`` relation field should exist on
    compensation_record after install, not two."""
    r = await authenticated_client.post(
        "/api/workspaces",
        json={"name": "Acme HR+Payroll Idempotent", "profile_slug": "hr-payroll-suite"},
    )
    assert r.status_code in (200, 201), r.text
    ws_id = (r.json().get("workspace") or {}).get("id")
    assert ws_id

    r2 = await authenticated_client.get(
        f"/api/workspaces/{ws_id}/apps",
        headers={"X-Integral-Scope": ws_id},
    )
    apps = r2.json().get("apps") or []
    payroll_app = next((a for a in apps if a["name"] == "Guyana Payroll"), None)
    assert payroll_app is not None

    r3 = await authenticated_client.get(
        f"/api/apps/{payroll_app['id']}/tracks",
        headers={"X-Integral-Scope": ws_id},
    )
    tracks = r3.json().get("tracks") or []
    compensation_track = next(
        (
            t
            for t in tracks
            if str(t.get("title") or "").lower().startswith("compensation")
        ),
        None,
    )
    assert compensation_track is not None, f"compensation track missing in {tracks}"

    r4 = await authenticated_client.get(
        "/api/entry-types",
        params={"track_id": compensation_track["id"]},
        headers={"X-Integral-Scope": ws_id},
    )
    assert r4.status_code == 200, r4.text
    entry_types = r4.json().get("entry_types") or []
    comp_et = next(
        (
            et
            for et in entry_types
            if str(et.get("name") or "").lower().replace(" ", "_")
            == "compensation_record"
        ),
        None,
    )
    assert (
        comp_et is not None
    ), f"compensation_record EntryType missing in {entry_types}"

    fields = (comp_et.get("form_schema") or {}).get("fields") or []
    employee_fields = [
        f for f in fields if f.get("key") == "employee" and f.get("type") == "relation"
    ]
    assert len(employee_fields) == 1, (
        "expected exactly 1 'employee' relation field on compensation_record "
        f"(no duplicate from cross_app_relations materialization), got {len(employee_fields)}"
    )
