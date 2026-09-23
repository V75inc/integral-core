"""Pluggable agent chat connectors (jvagent, future: Claude, etc.)
+ reference SyncConnector subclasses (Phase 5 — github_issues, …).

Side-effect imports at the bottom register concrete SyncConnector subclasses
with the core registry (``app.services.connectors.registry``). These imports
ride the package's AGENTIVE_ENABLED gating (D-08 / I-CON-05): when
AGENTIVE_ENABLED=0, this package is never imported and no slug is registered
— a non-agentive boot has no reference connectors available, by design.
"""

# Native Google Workspace connectors (direct Drive v3 / Sheets v4 tools).
# Side-effect imports fire ``@register_sync_connector("drive_native")`` and
# ``@register_sync_connector("sheets_native")``.
# Phase 19 — Gmail reference SyncConnector (EML-01).
# Side-effect import fires ``@register_sync_connector("gmail")``.
# Phase 18 — QuickBooks Online reference SyncConnector (QB-01).
# Side-effect import fires ``@register_sync_connector("quickbooks")``.
# Phase 5 Plan 05-05 — GitHub Issues reference SyncConnector (CON-04).
# Side-effect import fires ``@register_sync_connector("github_issues")``.
# AGENTIVE_ENABLED-gated by virtue of living under the agentive package
# (D-08 invariant — see I-CON-05 in docs/INVARIANTS.md).
from app.agentive.connectors import drive_native  # noqa: F401,E402
from app.agentive.connectors import github_issues  # noqa: F401,E402
from app.agentive.connectors import gmail  # noqa: F401,E402
from app.agentive.connectors import quickbooks  # noqa: F401,E402
from app.agentive.connectors import sheets_native  # noqa: F401,E402
from app.agentive.connectors.base import (
    AgentChatConnector,
    ChatTurnContext,
    ChatTurnResult,
)
from app.agentive.connectors.registry import get_chat_connector

__all__ = [
    "AgentChatConnector",
    "ChatTurnContext",
    "ChatTurnResult",
    "get_chat_connector",
]
