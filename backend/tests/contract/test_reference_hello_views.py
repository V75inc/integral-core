"""F2 Phase One — App-owned declarative views without Core FE registry edits.

Completion test 4 (declarative half): an external App ships ``view_types[]``
composites + ``views[]`` that compile to Core palette bases. No new file under
``frontend/src/views/manifests/`` is required for the App.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.operational_model_loader import (
    load_library_operational_models_with_issues,
)
from app.services.operational_model_runtime import compile_canonical_manifest

REPO = Path(__file__).resolve().parents[3]
REF_APP = REPO / "examples" / "reference-hello-app"
CORE_MANIFESTS = REPO / "frontend" / "src" / "views" / "manifests"


@pytest.fixture
def reference_root(monkeypatch):
    assert REF_APP.is_dir(), f"missing reference app at {REF_APP}"
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(REF_APP.parent))
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")
    from app.services.operational_model_library_sync import (
        reset_library_operational_models_cache_for_testing,
    )

    reset_library_operational_models_cache_for_testing()
    yield REF_APP
    reset_library_operational_models_cache_for_testing()


@pytest.mark.contract
def test_reference_hello_app_declares_view_composite(reference_root):
    """External package owns hello_board → composable_board composite."""
    specs, issues = load_library_operational_models_with_issues(
        package_paths=[str(reference_root.parent)],
        core_only=False,
        verify_signatures=False,
    )
    assert not any(
        getattr(i, "slug", None) == "reference-hello-app" for i in (issues or [])
    ), issues
    spec = next(s for s in specs if s.slug == "reference-hello-app")
    canonical = compile_canonical_manifest(manifest=spec.manifest)

    view_types = canonical.get("view_types") or []
    hello = next((vt for vt in view_types if vt.get("key") == "hello_board"), None)
    assert hello is not None, f"expected hello_board in view_types; got {view_types!r}"
    assert hello.get("base") == "composable_board"

    tracks = (canonical.get("app") or {}).get("tracks") or []
    notes = next((t for t in tracks if t.get("key") == "notes"), None)
    assert notes is not None
    views = notes.get("views") or []
    by_key = {v.get("key"): v for v in views}
    assert "notes_feed" in by_key
    board = by_key.get("notes_hello_board")
    assert board is not None, f"missing notes_hello_board; views={list(by_key)}"
    assert board.get("view_type") == "hello_board"
    composite = board.get("composite") or {}
    assert composite.get("base") == "composable_board"
    assert (composite.get("config") or {}).get("group_by") == "title"


@pytest.mark.contract
def test_reference_hello_views_need_no_core_fe_manifest():
    """Guard: App-owned composite must not require a Core *.manifest.ts."""
    # Core already ships composable_board — that is the palette primitive.
    assert (CORE_MANIFESTS / "composable_board.manifest.ts").is_file()
    # No App-specific hello_board / reference-hello widget in Core FE.
    forbidden = (
        "hello_board.manifest.ts",
        "reference_hello.manifest.ts",
        "notes_hello_board.manifest.ts",
    )
    present = {p.name for p in CORE_MANIFESTS.glob("*.manifest.ts")}
    for name in forbidden:
        assert name not in present, f"Core FE must not own App view {name}"
