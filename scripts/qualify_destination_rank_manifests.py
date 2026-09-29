#!/usr/bin/env python3
"""Exercise the filing destination ranker with pinned App manifest schemas.

This is a deterministic schema-compatibility smoke, not a substitute for the
Q-custodied natural-language exam. It loads tracked manifests from an immutable
Integral Business revision and runs the production ranker with synthetic graph,
permission, entry-history, and skill adapters. No model/provider is called.

Run from ``backend/``::

    uv run python ../scripts/qualify_destination_rank_manifests.py \
      --business-repo /path/to/integral-business \
      --revision <40-character-commit-sha>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from app.services import destination_rank

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=str(repo), text=True, stderr=subprocess.PIPE
    ).strip()


def _paths(repo: Path, revision: str) -> list[str]:
    output = _git(repo, "ls-tree", "-r", "--name-only", revision, "integral-apps")
    return sorted(
        path for path in output.splitlines() if path.endswith("/operational-model.yaml")
    )


def _load(repo: Path, revision: str, path: str) -> dict[str, Any]:
    content = subprocess.check_output(
        ["git", "show", f"{revision}:{path}"], cwd=str(repo), stderr=subprocess.PIPE
    )
    value = yaml.safe_load(content)
    if not isinstance(value, dict):
        raise ValueError(f"{path} at {revision} is not a YAML mapping")
    return value


def _track_schema(track_spec: dict[str, Any]) -> list[dict[str, Any]]:
    types = []
    for type_spec in track_spec.get("entry_types", []):
        fields = [
            {
                "key": str(field["key"]),
                "name": str(field.get("name") or field["key"]),
                "required": bool(field.get("required")),
            }
            for field in type_spec.get("fields", [])
            if isinstance(field, dict) and field.get("key")
        ]
        types.append(
            {
                "key": str(type_spec.get("key") or type_spec.get("name") or "entry"),
                "name": str(type_spec.get("name") or type_spec.get("key") or "Entry"),
                "fields": fields,
            }
        )
    return types


async def _qualify(repo: Path, revision: str, paths: list[str]) -> dict[str, Any]:
    reports = []
    for app_index, path in enumerate(paths):
        manifest = _load(repo, revision, path)
        package = manifest.get("package", {})
        app = SimpleNamespace(
            id=f"manifest-app-{app_index}",
            name=str(package.get("name") or Path(path).parent.name),
        )
        tracks = []
        schemas: dict[str, list[dict[str, Any]]] = {}
        for track_index, spec in enumerate(manifest.get("app", {}).get("tracks", [])):
            track_id = f"{app.id}-track-{track_index}"
            track = SimpleNamespace(
                id=track_id,
                title=str(spec.get("name") or spec.get("key") or "Track"),
            )
            tracks.append(track)
            schemas[track_id] = _track_schema(spec)

        def active_workspace_id() -> str:
            return "manifest-qualification"

        async def accessible_tracks_for_scope(
            _user_id: str, *, workspace_id: str | None = None
        ) -> list[Any]:
            if workspace_id != "manifest-qualification":
                raise AssertionError("ranker requested an unexpected workspace")
            return tracks

        async def generic_entry_read(_track: Any) -> Any:
            return SimpleNamespace(allowed=True)

        async def policy_evaluate(**_kwargs: Any) -> Any:
            return SimpleNamespace(allowed=True)

        async def destination_schema(track: Any) -> list[dict[str, Any]]:
            return schemas[track.id]

        async def history_prior(_track: Any, _user_id: str) -> float:
            return 0.0

        async def parent_app_for_track(_track: Any) -> Any:
            return app

        async def intake_skills(_user_id: str, _workspace_id: str) -> list[Any]:
            return []

        async def likely_entries(
            _tracks: list[Any], _text_tokens: set[str]
        ) -> list[Any]:
            return []

        destination_rank.active_workspace_id = active_workspace_id
        destination_rank.accessible_tracks_for_scope = accessible_tracks_for_scope
        destination_rank.generic_entry_read = generic_entry_read
        destination_rank.policy_evaluate = policy_evaluate
        destination_rank.destination_schema = destination_schema
        destination_rank._history_prior = history_prior
        destination_rank.parent_app_for_track = parent_app_for_track
        destination_rank._intake_skills = intake_skills
        destination_rank._likely_entries = likely_entries

        scenarios = []
        for track in tracks:
            for entry_type in schemas[track.id]:
                fields = entry_type["fields"]
                if not fields:
                    continue
                supplied = {field["key"]: "sample" for field in fields}
                query = f"{track.title} {entry_type['name']}"
                result = await destination_rank.rank_destinations(
                    user_id="manifest-qualification",
                    text=query,
                    facets=[{"text": query, "fields": supplied}],
                )
                if "error" in result:
                    raise AssertionError(f"{path}: {result['error']}")
                candidates = result["facets"][0]["candidates"]
                candidate_ids = [row["track_id"] for row in candidates]
                if track.id not in candidate_ids:
                    raise AssertionError(
                        f"{path}: schema target {track.title!r} missing from candidates"
                    )
                target = next(row for row in candidates if row["track_id"] == track.id)
                if target["mapped_fields"] != supplied:
                    raise AssertionError(
                        f"{path}: field coverage mismatch for {track.title!r}/"
                        f"{entry_type['name']!r}; ranker chose "
                        f"{target['entry_type']['name']!r} and mapped "
                        f"{sorted(target['mapped_fields'])} of {sorted(supplied)}"
                    )
                scenarios.append(
                    {
                        "track_id": track.id,
                        "entry_type": entry_type["key"],
                        "target_rank": candidate_ids.index(track.id) + 1,
                        "candidate_count": len(candidates),
                    }
                )

        reports.append(
            {
                "manifest": Path(path).relative_to("integral-apps").as_posix(),
                "tracks": len(tracks),
                "declared_entry_types": sum(len(types) for types in schemas.values()),
                "rank_scenarios": len(scenarios),
                "target_rank_1": sum(row["target_rank"] == 1 for row in scenarios),
                "rank_distribution": {
                    str(rank): sum(row["target_rank"] == rank for row in scenarios)
                    for rank in range(1, 6)
                },
            }
        )

    all_scenarios = sum(row["rank_scenarios"] for row in reports)
    first_rank = sum(row["target_rank_1"] for row in reports)
    return {
        "snapshot": revision,
        "manifest_count": len(reports),
        "rank_scenarios": all_scenarios,
        "target_rank_1": first_rank,
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
    paths = _paths(repo, args.revision)
    if not paths:
        parser.error(f"no tracked Operational Model manifests found at {args.revision}")
    result = asyncio.run(_qualify(repo, args.revision, paths))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
