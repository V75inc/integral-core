"""GET /api/apps/{slug}/skills — generic skill catalogue per installed bundle.

Replaces legacy-era /api/agents//skills. Substrate reads the compiled
manifest of any bundle slug; no domain coupling.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import ResourceNotFoundError


def _summarize_skill(skill: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "key": skill.get("key"),
        "name": skill.get("name") or skill.get("key"),
        "description": skill.get("description") or "",
        "kind": skill.get("kind") or "declarative",
        "private": bool(skill.get("private")),
        "parameters": skill.get("parameters") or {},
        "outputs": skill.get("outputs") or [],
    }


def _summarize_agent(agent: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "key": agent.get("key"),
        "name": agent.get("name") or agent.get("key"),
        "scope": agent.get("scope") or "app",
        "persona_ref": agent.get("persona_ref") or "",
        "skills": list(agent.get("skills") or []),
    }


@endpoint("/apps/{slug}/skills", methods=["GET"], auth=True, tags=["Apps"])
async def list_app_skills(request: Request, slug: str) -> Dict[str, Any]:
    """Return the skill + agent catalogue declared by an installed bundle's manifest."""
    from app.services.operational_model_compile import compile_canonical_manifest
    from app.services.operational_model_loader import (
        load_library_operational_models_with_issues,
    )

    specs, _ = load_library_operational_models_with_issues(
        packages_root=Path("app/packages")
    )
    spec = next((s for s in specs if s.slug == slug), None)
    if spec is None:
        raise ResourceNotFoundError(message=f"App bundle {slug!r} not found")
    canonical = compile_canonical_manifest(manifest=spec.manifest, scope_hint="app")
    app_block = canonical.get("app") or {}
    skills = [_summarize_skill(s) for s in (app_block.get("skills") or [])]
    agents = [_summarize_agent(a) for a in (app_block.get("agents") or [])]
    return {"slug": slug, "agents": agents, "skills": skills, "total": len(skills)}
