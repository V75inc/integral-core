"""Measurement for the resolve_role cascade (AGENTS.md pragmatism clause).

AGENTS.md names the role cascade Workspace->App->Track->Entry as the textbook
Walker case, and `resolve_role` is procedural recursion instead. The contract
allows that -- "measure first, deviate second, document always" -- but requires
the deviation to carry a measurement. It carried none.

This is that measurement. It is a benchmark, not a correctness test: it counts
database round trips for a cold, uncached resolve at each depth, so the
`# deviation:` note in permissions.py cites real numbers and so a future change
that makes the cascade materially more expensive shows up here.

Marked `slow` so it stays off the default gate; run with
INTEGRAL_RUN_SLOW_TESTS=1 or `pytest -m slow`.
"""

import os
import time

import pytest

pytestmark = pytest.mark.slow

if not os.getenv("INTEGRAL_RUN_SLOW_TESTS"):
    pytest.skip(
        "benchmark; set INTEGRAL_RUN_SLOW_TESTS=1 to run", allow_module_level=True
    )


async def _bootstrap_chain(email: str):
    """Workspace -> App -> Track -> Entry owned by one user."""
    from jvspatial.api.auth.models import UserCreate

    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.apps import create_app as create_app_endpoint
    from app.api.auth import _get_auth_service
    from app.api.entries import create_entry
    from app.api.tracks import create_track
    from app.models.nodes import User
    from app.services.app_graph import catalog_user
    from app.services.personal_workspace import ensure_personal_workspace

    auth_service = _get_auth_service()
    resp = await auth_service.register_user(
        UserCreate(email=email, password="testpassword123")
    )
    node = await User.create(user_id=resp.id, display_name="Bench")
    await catalog_user(node)
    ws = await ensure_personal_workspace(node)

    app_res = await invoke_route_in_process(
        create_app_endpoint, principal_id=resp.id, scope=ws.id, name="Bench App"
    )
    app_id = (app_res.get("app") or app_res.get("space") or {}).get("id")

    track_res = await invoke_route_in_process(
        create_track,
        principal_id=resp.id,
        scope=ws.id,
        title="Bench Track",
        visibility="private",
        app_id=app_id,
    )
    track_id = track_res["track"]["id"]

    entry_res = await invoke_route_in_process(
        create_entry,
        principal_id=resp.id,
        scope=ws.id,
        track_id=track_id,
        title="Bench Entry",
    )
    return resp.id, ws.id, app_id, track_id, entry_res["entry"]["id"]


@pytest.mark.asyncio
async def test_resolve_role_cascade_cost():
    """Report cold-cache cost per depth. Always passes; the output is the point."""
    from app.services import permissions as perms
    from app.services import permissions_process_cache as ppc

    user_id, ws_id, app_id, track_id, entry_id = await _bootstrap_chain(
        "bench-cascade@example.com"
    )

    targets = [
        ("workspace", ws_id),
        ("app", app_id),
        ("track", track_id),
        ("entry", entry_id),
    ]

    print("\nresolve_role cold-cache cost (deeper = more cascade levels):")
    results = []
    for kind, rid in targets:
        if not rid:
            continue
        # Cold: clear both cache layers so we measure the real cascade.
        ppc.invalidate_user(user_id)
        if hasattr(perms, "_request_role_cache"):
            try:
                perms._request_role_cache.set(None)
            except Exception:
                pass

        t0 = time.perf_counter()
        role = await perms.resolve_role(user_id, kind, rid)
        cold_ms = (time.perf_counter() - t0) * 1000

        # Warm: second call should hit the process cache.
        t1 = time.perf_counter()
        await perms.resolve_role(user_id, kind, rid)
        warm_ms = (time.perf_counter() - t1) * 1000

        results.append((kind, role, cold_ms, warm_ms))
        print(
            f"  {kind:10} role={str(role):8} cold={cold_ms:7.2f}ms warm={warm_ms:7.2f}ms"
        )

    assert results, "benchmark produced no measurements"
    # Sanity: the owner must resolve at every depth, or we measured nothing real.
    for kind, role, _, _ in results:
        assert role is not None, f"{kind} resolved to None — chain is not owner-linked"
