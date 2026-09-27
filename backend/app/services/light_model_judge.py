"""One cheap JSON verdict from the workspace's light model.

Intent (approve a design, ask for a new App, promise an effect the build
cannot perform) is the model's call, in whatever language the text uses.
Callers decide what a failure means.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional


async def light_model_json(
    *,
    workspace_id: Optional[str],
    agent_id: Optional[str],
    system: str,
    prompt: str,
    max_tokens: int = 40,
) -> Dict[str, Any]:
    """Ask the light model for one JSON object. Raises if it cannot answer."""
    from jvagent.action.model.context import bind_model_gear
    from jvagent.core.agent import Agent

    from app.services.jvagent_harness import harness_model_override

    agent = await Agent.get(agent_id) if agent_id else None
    orchestrator = (
        await agent.get_action_by_type("OrchestratorInteractAction") if agent else None
    )
    if orchestrator is None:
        raise RuntimeError("no orchestrator for a light-model verdict")
    async with harness_model_override(workspace_id):
        model_action, model_id, *_ = await orchestrator._light_profile()
        if model_action is None:
            raise RuntimeError("no light model")
        with bind_model_gear("light"):
            result = await model_action.query(
                prompt,
                system=system,
                calling_action_name="OrchestratorInteractAction",
                model=model_id,
                temperature=0,
                max_tokens=max_tokens,
            )
    text = str(await result.get_response() or "")
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("light model did not return JSON")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("light model JSON was not an object")
    return parsed
