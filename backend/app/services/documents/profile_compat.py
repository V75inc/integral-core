"""Resolve content-profile vs operational-model substrate for document services."""

from __future__ import annotations

from typing import Any, Dict, Tuple


def compile_canonical_manifest(manifest: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from app.services.content_profile_compile import (
            compile_canonical_manifest as _compile,
        )
    except ImportError:
        from app.services.operational_model_compile import (
            compile_canonical_manifest as _compile,
        )
    return _compile(manifest=manifest)


def slug_manifest_key(value: str) -> str:
    try:
        from app.services.content_profile_compile import slug_manifest_key as _slug
    except ImportError:
        from app.services.operational_model_compile import slug_manifest_key as _slug
    return _slug(str(value or "").strip())


def track_runtime_module():
    try:
        from app.services import content_profile_runtime as mod
    except ImportError:
        from app.services import operational_model_runtime as mod
    return mod


async def manifest_dict_for_app(app: Any) -> Dict[str, Any]:
    cp_id = getattr(app, "attached_content_profile_id", None) or ""
    if cp_id:
        try:
            from app.models.nodes import ContentProfile
        except ImportError:
            ContentProfile = None  # type: ignore[misc, assignment]
        if ContentProfile is not None:
            cp = await ContentProfile.get(cp_id)
            if cp:
                raw = getattr(cp, "manifest", None) or {}
                return raw if isinstance(raw, dict) else {}
    om_id = getattr(app, "attached_operational_model_id", None) or ""
    if om_id:
        from app.models.nodes import OperationalModel

        om = await OperationalModel.get(om_id)
        if om:
            raw = getattr(om, "manifest", None) or {}
            return raw if isinstance(raw, dict) else {}
    return {}


async def resolve_track_runtime_profile(track: Any) -> Tuple[Any, Dict[str, Any], str]:
    mod = track_runtime_module()
    return await mod.resolve_track_runtime_profile(track)
