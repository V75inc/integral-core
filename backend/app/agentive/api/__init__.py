"""Register all agentive API routes.

Called from main.py at startup. HTTP routes in this
subpackage register themselves with jvspatial's ``Server`` via ``@endpoint``
side-effect imports below — there is no central router to ``include_router``.

The lone exception is ``agent_events.py`` (WebSocket) which still uses
``APIRouter().websocket(...)`` because jvspatial's ``@endpoint`` does not
support WebSocket routes; ``register_routes(app)`` mounts that router
explicitly. See ``app/agentive/api/agent_events.py`` for the ``# deviation:``
annotation per AGENTS.md § Forbidden Patterns → Pragmatism Clause.
"""

from fastapi import FastAPI

# Side-effect imports — every ``@endpoint``-decorated handler registers itself
# with the active jvspatial ``Server`` at decoration time (mirrors the core
# ``app.api.__init__.py:1-30`` pattern).
from app.agentive.api import (  # noqa: F401 — side-effect registration
    agent_skills,
    agent_tools,
    channels,
    chat,
    connectors,
    conversations,
    mcp_connectors,
    proactive,
    prompt_queue,
    questions,
    routines,
    speech,
    staging,
    status,
    uplink,
)
from app.agentive.api.agent_events import router as agent_events_router


def register_routes(app: FastAPI) -> None:
    """Attach agentive WebSocket route to the FastAPI app.

    HTTP routes are auto-registered by the side-effect imports above. Only
    the WebSocket router needs an explicit ``include_router`` because
    jvspatial's ``@endpoint`` does not support WebSocket dispatch.
    """
    app.include_router(agent_events_router)
