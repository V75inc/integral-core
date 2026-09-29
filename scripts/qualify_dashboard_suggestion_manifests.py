#!/usr/bin/env python3
"""Check dashboard suggestions against tracked Operational Model manifests.

This read-only qualification helper loads manifests from a pinned external Git
revision. It does not copy domain schemas into Core or require a database,
populated App graph, provider credentials, or model calls. App/Track lookup,
permissions, entries, and widget previews are synthetic adapters; the
production ``suggest_dashboard_template`` function performs recommendation
selection and the helper checks every emitted custom-field reference.

Run from ``backend/`` so the Integral service package is importable:

    uv run python ../scripts/qualify_dashboard_suggestion_manifests.py \
      --business-repo ../../integral-business \
      --revision <40-character-commit-sha>
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from app.services import dashboard_service

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=str(repo), text=True, stderr=subprocess.PIPE
    ).strip()


def _manifest_paths(repo: Path, revision: str) -> list[str]:
    result = _git(repo, "ls-tree", "-r", "--name-only", revision, "integral-apps")
    return sorted(
        path for path in result.splitlines() if path.endswith("/operational-model.yaml")
    )


def _load_manifest(repo: Path, revision: str, path: str) -> tuple[dict[str, Any], bytes]:
    content = subprocess.check_output(
        ["git", "show", f"{revision}:{path}"], cwd=str(repo), stderr=subprocess.PIPE
    )
    value = yaml.safe_load(content)
    if not isinstance(value, dict):
        raise ValueError(f"{path} at {revision} is not a YAML mapping")
    return value, content


class _ManifestApp:
    def __init__(self, app_id: str, name: str, tracks: list[Any]) -> None:
        self.id = app_id
        self.name = name
        self._tracks = tracks

    async def nodes(self, **_kwargs: Any) -> list[Any]:
        return self._tracks


def _track_schemas(
    manifest: dict[str, Any], app_index: int
) -> tuple[list[Any], dict[str, list[dict[str, Any]]], int]:
    tracks: list[Any] = []
    fields_by_track: dict[str, list[dict[str, Any]]] = {}
    total_fields = 0
    app = manifest.get("app", {})
    for track_index, track_spec in enumerate(app.get("tracks", [])):
        track_id = f"manifest-app-{app_index}-track-{track_index}"
        title = str(track_spec.get("name") or track_spec.get("key") or "Track")
        tracks.append(SimpleNamespace(id=track_id, title=title))
        fields: dict[str, dict[str, Any]] = {}
        for entry_type in track_spec.get("entry_types", []):
            for field in entry_type.get("fields", []):
                key = str(field.get("key") or "").strip()
                field_type = str(field.get("type") or "").strip().lower()
                if key and field_type:
                    fields.setdefault(
                        key,
                        {
                            "key": key,
                            "name": str(field.get("name") or key),
                            "type": field_type,
                            "enum": list(field.get("enum") or []),
                        },
                    )
        fields_by_track[track_id] = list(fields.values())
        total_fields += len(fields)
    return tracks, fields_by_track, total_fields


def _referenced_custom_fields(source: dict[str, Any]) -> list[str]:
    references: list[str] = []
    if source.get("field"):
        references.append(str(source["field"]))
    group_by = str(source.get("group_by") or "")
    if group_by.startswith("custom_fields."):
        references.append(group_by.removeprefix("custom_fields."))
    elif group_by.startswith("date:"):
        references.append(group_by.removeprefix("date:"))
    for predicate in source.get("filters", []):
        field = str(predicate.get("field") or "")
        if field.startswith("custom_fields."):
            references.append(field.removeprefix("custom_fields."))
    return references


async def _qualify(repo: Path, revision: str, paths: list[str]) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []

    for app_index, path in enumerate(paths):
        manifest, content = _load_manifest(repo, revision, path)
        tracks, fields_by_track, field_count = _track_schemas(manifest, app_index)
        package = manifest.get("package", {})
        app_id = f"manifest-app-{app_index}"
        app = _ManifestApp(
            app_id,
            str(package.get("name") or Path(path).parent.name),
            tracks,
        )

        async def can_view_app(*_args: Any, **_kwargs: Any) -> bool:
            return True

        async def get_app(*_args: Any, **_kwargs: Any) -> _ManifestApp:
            return app

        async def activity_digest(*_args: Any, **_kwargs: Any) -> dict[str, int]:
            return {"total_entries": 10}

        async def track_fields(track: Any) -> list[dict[str, Any]]:
            return fields_by_track[track.id]

        async def preview(**_kwargs: Any) -> dict[str, int]:
            return {"value": 10, "total_matched": 10}

        # The helper is a short-lived process, so replacing these adapters is
        # contained to this script and cannot affect the running application.
        dashboard_service.can_view_app = can_view_app
        dashboard_service._get_app_or_none = get_app
        dashboard_service.activity_digest = activity_digest
        dashboard_service._track_dashboard_fields = track_fields
        dashboard_service.resolve_widget_data = preview

        result = await dashboard_service.suggest_dashboard_template(
            user_id="manifest-qualification",
            app_id=app_id,
            workspace_id="manifest-qualification",
        )
        if "error" in result:
            raise AssertionError(f"{path}: suggestion failed: {result['error']}")

        references_checked = 0
        field_widgets = 0
        for widget in result.get("widgets", []):
            if not widget.get("rationale"):
                raise AssertionError(f"{path}: {widget.get('title')} has no rationale")
            if "preview" not in widget:
                raise AssertionError(f"{path}: {widget.get('title')} has no preview")
            source = widget.get("data_source", {})
            track_id = source.get("track_id")
            declared = {field["key"] for field in fields_by_track.get(track_id, [])}
            references = _referenced_custom_fields(source)
            if references:
                field_widgets += 1
            for key in references:
                if key not in declared:
                    raise AssertionError(
                        f"{path}: {widget.get('title')} refers to undeclared field {key!r}"
                    )
                references_checked += 1

        reports.append(
            {
                "manifest": Path(path).relative_to("integral-apps").as_posix(),
                "sha256": hashlib.sha256(content).hexdigest(),
                "tracks": len(tracks),
                "declared_fields": field_count,
                "widgets": len(result.get("widgets", [])),
                "field_widgets": field_widgets,
                "field_references_checked": references_checked,
            }
        )

    return {
        "snapshot": revision,
        "manifest_count": len(reports),
        "apps": reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--business-repo", required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()

    if not _SHA_RE.fullmatch(args.revision):
        parser.error("--revision must be a full 40-character lowercase Git SHA")
    repo = Path(args.business_repo).expanduser().resolve()
    paths = _manifest_paths(repo, args.revision)
    if not paths:
        parser.error(f"no tracked Operational Model manifests found at {args.revision}")
    print(json.dumps(asyncio.run(_qualify(repo, args.revision, paths)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
