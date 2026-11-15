"""Description contract for newly AI-authored containers, not existing records."""

import re
from typing import Any


def creation_description(value: Any, name: str) -> str:
    """Reject missing metadata and obvious placeholders; preserve useful prose.

    Semantic completeness is guided by the design prompt and human preview. This
    deterministic guard deliberately does not impose English-only word counts.
    """
    description = value.strip() if isinstance(value, str) else ""
    key = " ".join(re.findall(r"\w+", description.casefold()))
    title = " ".join(re.findall(r"\w+", name.casefold()))
    if (
        not key
        or key == title
        or key
        in {
            "tbd",
            "todo",
            "n a",
            "na",
            "none",
            "null",
            "placeholder",
            "description",
            "no description",
            "to be determined",
            "coming soon",
        }
    ):
        raise ValueError(
            f"{name!r} requires a meaningful description explaining its purpose, "
            "what it contains, and how it is used; do not repeat the title or use "
            "a placeholder. Supply the description before creating it."
        )
    return description


def validate_new_package_track_descriptions(
    manifest: dict,
    existing_keys: set[str],
    existing_template_keys: set[str] | None = None,
) -> None:
    """Validate all new prescribed Tracks before any Track is materialized.

    Existing legacy tracks remain operable. Updating a package may backfill their
    blank purpose through the existing ownership-aware description sync; no
    description is invented, and user-authored text is not overwritten.
    """
    app = manifest.get("app") or {}
    tracks = app.get("tracks") or []
    if not (app.get("defaults") or {}).get("provision_prescribed_tracks", True):
        tracks = []
    for spec in tracks:
        if not isinstance(spec, dict) or not spec.get("provision_on_create", True):
            continue
        key = str(spec.get("key") or "")
        if key and key not in existing_keys:
            creation_description(spec.get("description"), str(spec.get("name") or key))

    if existing_template_keys is not None:
        for spec in (manifest.get("app") or {}).get("track_templates") or []:
            if not isinstance(spec, dict):
                continue
            key = str(spec.get("key") or "")
            if key and key not in existing_template_keys:
                creation_description(
                    spec.get("description"), str(spec.get("name") or key)
                )
