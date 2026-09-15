"""Structural guard: every ``create_wizard`` step's ``track_type`` /
``source_track_type`` reference (entry_checklist's own source track, and
its ``filter.join``/``display_columns[].join`` track references) must
resolve to a track this app actually declares — by exact title match —
or to the shared HR "Employees" track.

This is the class of bug a full end-to-end test wouldn't catch: the join
itself is resolved CLIENT-SIDE (``CreateWizardModal.tsx``), not by any
backend service, so a stale track-title reference (e.g. left over from a
track rename that updated the track's own `name:` but missed a wizard's
`track_type:` reference elsewhere in the same manifest) silently
produces an empty employee list in the real UI with no error anywhere —
confirmed live on Guyana Payroll after its "Guyana "-prefix rename missed
exactly this. A pure YAML/manifest scan, no DB, so it runs fast and
catches this before it ever reaches a browser.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Set

import pytest

from app.services.content_profile_loader import load_library_profiles_with_issues

_PAYROLL_SLUGS = ("payroll-app", "aruba-payroll")


def _declared_track_names(manifest: Dict[str, Any]) -> Set[str]:
    tracks = ((manifest.get("app") or {}).get("tracks")) or []
    return {str(t.get("name") or "") for t in tracks if isinstance(t, dict)}


def _iter_track_type_refs(step: Dict[str, Any]) -> Iterable[str]:
    if step.get("kind") != "entry_checklist":
        return
    if step.get("source_track_type"):
        yield str(step["source_track_type"])
    filter_join = (step.get("filter") or {}).get("join") or {}
    if filter_join.get("track_type"):
        yield str(filter_join["track_type"])
    for col in step.get("display_columns") or []:
        join = (col or {}).get("join") or {}
        if join.get("track_type"):
            yield str(join["track_type"])


def _all_wizard_steps(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for track in (manifest.get("app") or {}).get("tracks") or []:
        for et in (track or {}).get("entry_types") or []:
            wizard = (et or {}).get("create_wizard") or {}
            out.extend(wizard.get("steps") or [])
    return out


@pytest.mark.parametrize("slug", _PAYROLL_SLUGS)
def test_wizard_track_type_references_resolve_to_a_real_track(slug: str):
    specs, _ = load_library_profiles_with_issues(profiles_root=Path("app/profiles"))
    spec = next(s for s in specs if s.slug == slug)
    manifest = spec.manifest
    declared = _declared_track_names(manifest) | {"Employees"}

    stale: List[str] = []
    for step in _all_wizard_steps(manifest):
        for ref in _iter_track_type_refs(step):
            if ref not in declared:
                stale.append(ref)

    assert not stale, (
        f"{slug}: create_wizard track_type reference(s) {sorted(set(stale))} "
        f"don't match any of this app's own declared track names "
        f"{sorted(declared)} — likely a rename that updated the track's own "
        f"`name:` but missed a wizard step's `track_type:`/`source_track_type:` "
        f"reference elsewhere in the manifest."
    )
