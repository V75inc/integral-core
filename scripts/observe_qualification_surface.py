#!/usr/bin/env python3
"""Build a qualification surface snapshot from API payloads.

The compiler derives field, dashboard, query, and schema checks from this
snapshot. It does not accept those results as caller booleans. This script
never records prompts, completions, or credentials.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence


def _as_list(value: Any, key: str) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, Mapping) and isinstance(value.get(key), list):
        return value[key]
    return []


def _manifest_of(track: Mapping[str, Any]) -> Mapping[str, Any]:
    model = track.get("operational_model")
    if isinstance(model, Mapping) and isinstance(model.get("manifest"), Mapping):
        return model["manifest"]
    manifest = track.get("manifest")
    if isinstance(manifest, Mapping):
        return manifest
    return {}


def _fields(manifest: Mapping[str, Any]) -> list[Dict[str, str]]:
    fields: list[Dict[str, str]] = []
    for entry_type in manifest.get("entry_types") or []:
        if not isinstance(entry_type, Mapping):
            continue
        for field in entry_type.get("fields") or []:
            if not isinstance(field, Mapping):
                continue
            fields.append(
                {
                    "key": str(field.get("key") or ""),
                    "label": str(field.get("label") or field.get("key") or ""),
                    "type": str(field.get("type") or ""),
                }
            )
    return fields


def _views(manifest: Mapping[str, Any]) -> list[str]:
    views = []
    for view in manifest.get("views") or []:
        if isinstance(view, Mapping):
            views.append(str(view.get("type") or view.get("view_type") or ""))
        elif isinstance(view, str):
            views.append(view)
    return [view for view in views if view]


def surface_from_api_bundle(bundle: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize list/model/dashboard/entry payloads into a compiler snapshot."""
    apps = _as_list(bundle.get("apps"), "apps")
    tracks = []
    for track in _as_list(bundle.get("tracks"), "tracks"):
        if not isinstance(track, Mapping):
            continue
        manifest = _manifest_of(track)
        tracks.append(
            {
                "name": str(track.get("name") or ""),
                "fields": _fields(manifest),
                "views": _views(manifest),
            }
        )
    dashboards = []
    for board in _as_list(bundle.get("dashboards"), "dashboards"):
        if not isinstance(board, Mapping):
            continue
        widgets = board.get("widgets")
        count = board.get("widget_count")
        if not isinstance(count, int):
            count = len(widgets) if isinstance(widgets, list) else 0
        dashboards.append({"widget_count": count})
    entries = []
    for entry in _as_list(bundle.get("entries"), "entries"):
        if not isinstance(entry, Mapping):
            continue
        values = entry.get("values")
        if not isinstance(values, Mapping):
            values = entry.get("data") if isinstance(entry.get("data"), Mapping) else {}
        entries.append({"id": entry.get("id"), "values": dict(values)})
    query = bundle.get("query") if isinstance(bundle.get("query"), Mapping) else {}
    schema = bundle.get("schema") if isinstance(bundle.get("schema"), Mapping) else {}
    return {
        "app_count": len(apps),
        "tracks": tracks,
        "dashboards": dashboards,
        "entries": entries,
        "query": {
            "field": str(query.get("field") or ""),
            "equals": query.get("equals"),
            "rendered_ids": list(query.get("rendered_ids") or []),
        },
        "schema": {
            "revision_before": schema.get("revision_before"),
            "revision_after": schema.get("revision_after"),
            "ids_before": list(schema.get("ids_before") or []),
            "ids_after": list(schema.get("ids_after") or []),
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Write a compiler surface snapshot from a local API bundle."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
    if not isinstance(bundle, dict):
        raise ValueError("bundle must be a JSON object")
    snapshot = surface_from_api_bundle(bundle)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"surface": str(args.out), "apps": snapshot["app_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
