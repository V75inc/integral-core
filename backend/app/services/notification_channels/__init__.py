"""Notification channel adapter registry (Phase 9 Plan 09-03a -> 09-03b, NOTIF-02).

The ``CHANNEL_REGISTRY`` dict is the router's lookup table:
``CHANNEL_REGISTRY[channel_name]`` returns the singleton adapter that handles
a dispatch for that channel. Adding a new channel (SMS, Slack, Push) is a
single registry-entry addition here plus a new adapter module.

09-03b — the 09-03a placeholder adapter is REMOVED; the WhatsApp slot now
points at the real
:class:`~app.services.notification_channels.whatsapp_channel.WhatsappChannel`
adapter (Meta Cloud API + welcome-template-then-freeform decision). The
registry key is stable across the swap, so no other call site changed.
"""

from app.services.notification_channels.email_channel import EmailChannel
from app.services.notification_channels.in_app_channel import InAppChannel
from app.services.notification_channels.types import ChannelDispatchResult
from app.services.notification_channels.whatsapp_channel import WhatsappChannel

# Singleton instances — channel adapters carry no per-call state, so a single
# instance shared across the process is cheaper than constructing one per
# dispatch. The registry is the single canonical lookup; the router never
# constructs adapters directly.
CHANNEL_REGISTRY = {
    "in_app": InAppChannel(),
    "email": EmailChannel(),
    "whatsapp": WhatsappChannel(),
}

__all__ = [
    "CHANNEL_REGISTRY",
    "ChannelDispatchResult",
    "EmailChannel",
    "InAppChannel",
    "WhatsappChannel",
]
