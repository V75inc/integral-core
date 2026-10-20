"""Reproducible loaded-workspace navigation probe (synthetic data only)."""

import cProfile
import json
import os
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow


@pytest.mark.asyncio
async def test_loaded_workspace_probe(
    authenticated_client, test_user, monkeypatch, tmp_path
):
    from app.models.edges import CONTAINS, IS_MEMBER_OF, OWNS
    from app.models.nodes import App, Entry, Track, Workspace
    from app.services.app_graph import catalog_app, catalog_track, catalog_workspace
    from app.services.permissions import invalidate_user_accessible_caches

    workspace = await Workspace.create(
        name="Loaded workspace probe", kind="organization"
    )
    await catalog_workspace(workspace)
    await test_user.connect(workspace, edge=IS_MEMBER_OF, role="owner")
    headers = {"X-Integral-Scope": f"ws:{workspace.id}"}
    monkeypatch.setenv("INTEGRAL_PERF_HEADER_ENABLED", "1")
    points = []
    output = Path(
        os.getenv("INTEGRAL_PROBE_OUTPUT", str(tmp_path / "loaded-workspace.json"))
    )
    for index in range(8):
        app = await App.create(name=f"Probe {index}", workspace_id=workspace.id)
        await catalog_app(app)
        await test_user.connect(app, edge=OWNS)
        for ti in range(4):
            track = await Track.create(
                title=f"Probe {index}/{ti}", workspace_id=workspace.id
            )
            await catalog_track(track)
            await app.connect(track, edge=CONTAINS)
            await test_user.connect(track, edge=OWNS)
            for ei in range(10):
                entry = await Entry.create(title=f"Record {ei}", track_id=track.id)
                await track.connect(entry, edge=CONTAINS)
        if index + 1 not in {1, 4, 8}:
            continue
        for path, params in [
            ("/api/apps", {}),
            ("/api/tracks", {"limit": 50}),
            (f"/api/apps/{app.id}/tracks", {}),
            ("/api/entries", {"track_id": track.id, "limit": 50}),
        ]:
            measurements = []
            profile = cProfile.Profile()
            for rep in range(3):
                invalidate_user_accessible_caches(test_user.id)
                started = time.perf_counter()
                profile.enable() if os.getenv("INTEGRAL_PROFILE_PROBE") else None
                response = await authenticated_client.get(
                    path, params=params, headers=headers
                )
                profile.disable() if os.getenv("INTEGRAL_PROFILE_PROBE") else None
                assert response.status_code == 200, response.text
                measurements.append(
                    {
                        "ms": round((time.perf_counter() - started) * 1000, 2),
                        "db_ops": int(response.headers["X-DB-Round-Trip-Count"]),
                    }
                )
            if os.getenv("INTEGRAL_PROFILE_PROBE"):
                profile.dump_stats(
                    str(
                        output.with_name(f"loaded-{index+1}-{path.split('/')[-1]}.prof")
                    )
                )
            points.append(
                {
                    "apps": index + 1,
                    "tracks": (index + 1) * 4,
                    "entries": (index + 1) * 40,
                    "path": path.replace(app.id, "<app>"),
                    "samples": measurements,
                }
            )
    output.write_text(json.dumps(points, indent=2))
    print(json.dumps(points, indent=2))
