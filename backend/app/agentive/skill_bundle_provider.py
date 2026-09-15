"""jvagent host skill provider — surfaces workspace overlay skills to Orchestrator."""

from __future__ import annotations

import logging
from typing import Any, List

logger = logging.getLogger(__name__)

_installed = False


def _overlay_to_skill_doc(doc: Any) -> Any:
    from jvagent.action.orchestrator.skills import SkillDoc

    return SkillDoc(
        name=doc.name,
        description=doc.description,
        body=doc.body,
        requires_tools=tuple(doc.requires_tools or ()),
        requires_actions=tuple(doc.requires_actions or ()),
        source=getattr(doc, "source", "workspace"),
        directory="",
        spec=getattr(doc, "spec", "jv"),
        always_active=bool(getattr(doc, "always_active", False)),
        metadata=dict(getattr(doc, "metadata", None) or {}),
    )


def _integral_host_skill_provider(_agent: Any) -> List[Any]:
    from app.agentive.workspace_agent_profile import get_turn_workspace_profile

    profile = get_turn_workspace_profile()
    if profile is None:
        return []
    return [_overlay_to_skill_doc(d) for d in profile.overlay_skill_docs]


def install_skill_provider_into_jvagent() -> None:
    """Register Integral's workspace overlay provider with jvagent (idempotent)."""
    global _installed
    if _installed:
        return
    try:
        from jvagent.action.orchestrator.skill_providers import (
            register_host_skill_provider,
        )

        register_host_skill_provider(_integral_host_skill_provider)
        _installed = True
        logger.info("skill_bundle_provider: registered jvagent host skill provider")
    except ImportError as exc:
        logger.warning(
            "skill_bundle_provider: jvagent skill_providers unavailable: %s",
            exc,
        )


__all__ = ["install_skill_provider_into_jvagent"]
