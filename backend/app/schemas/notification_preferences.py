"""Wire shape for ``User.notification_preferences`` (Phase 9 Plan 09-03a, NOTIF-02).

This module declares the Pydantic boundary for the per-user notification
channel preference matrix that drives the notification router's fan-out
decisions. The shape is stored as ``Dict[str, Any]`` on the
``User`` node (mirroring the Phase 2 ``ChangeEvent.actor_kind: str``
idiom — Pydantic enforces the contract at the boundary, the Node carries
a plain dict to avoid circular imports).

Three Pydantic primitives:

- ``ChannelMatrix`` — per-channel boolean (in_app / email / whatsapp).
- ``WhatsappPrefs`` — WhatsApp-specific opt-in capture (timestamp + e164 phone).
- ``NotificationPreferences`` — the full matrix: a default ``channels``
  matrix, a per-``NotificationKind`` override map, and the WhatsApp slot.

``default_preferences()`` returns the canonical defaults used when
``User.notification_preferences is None`` — in_app on for everything,
email on for the noisy human-facing kinds (mention/share/agent_pending_write/
invitation), whatsapp off by default (explicit opt-in gate per A3).

All models use ``extra="forbid"`` so a stray field in a stored dict (e.g.
from an old schema migration) surfaces as a validation error at the
router boundary rather than silently flowing through.
"""

from typing import Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

NotificationKind = Literal[
    "mention",
    "share",
    "agent_pending_write",
    "invitation",
    "system",
    "whatsapp_welcome",
    "entry_update",
]
ChannelName = Literal["in_app", "email", "whatsapp"]


class ChannelMatrix(BaseModel):
    """Per-channel boolean toggle (in_app / email / whatsapp).

    Used twice in :class:`NotificationPreferences`:

    - As the workspace-wide default under ``channels``.
    - As an entry in the per-kind override map under ``kinds``.

    The matrix is intentionally minimal — adding a channel later (SMS,
    Slack, Push) is a single field addition here plus a registry entry in
    ``app.services.notification_channels``.
    """

    model_config = ConfigDict(extra="forbid")
    in_app: bool = True
    email: bool = True
    whatsapp: bool = False


class WhatsappPrefs(BaseModel):
    """WhatsApp-specific opt-in capture (Phase 9 A3).

    Populated when the user verifies their phone via
    ``/agentive/channels/whatsapp/verify-otp``. ``opted_in_at`` doubles
    as the consent timestamp (audit reference for repudiation defense)
    and the gate the router consults before firing a WhatsApp dispatch —
    if the timestamp is ``None``, the channel is skipped.
    """

    model_config = ConfigDict(extra="forbid")
    opted_in_at: Optional[str] = None  # ISO 8601 timestamp, set on verify-otp success
    phone_e164: Optional[str] = None  # E.164 phone number captured at opt-in


class NotificationPreferences(BaseModel):
    """Full notification preference matrix for one user.

    The router resolves the per-kind matrix by ``kinds.get(kind,
    channels)`` — kinds is a sparse override map, falling back to the
    workspace-wide default when a kind isn't explicitly configured.

    Strict ``extra="forbid"`` validation means stored dicts from prior
    schema versions surface as Pydantic errors at the router boundary,
    not as silent dropped fields.
    """

    model_config = ConfigDict(extra="forbid")
    channels: ChannelMatrix = Field(default_factory=ChannelMatrix)
    kinds: Dict[NotificationKind, ChannelMatrix] = Field(default_factory=dict)
    whatsapp: WhatsappPrefs = Field(default_factory=WhatsappPrefs)


def default_preferences() -> NotificationPreferences:
    """Return the canonical default preference matrix.

    Used when ``User.notification_preferences is None`` (i.e. the user
    has never customized their preferences — most accounts on Phase 9
    rollout). The defaults:

    - **in_app** on for every kind (canonical notification surface).
    - **email** on for noisy human-facing kinds (mention, share,
      agent_pending_write, invitation). Off for the ``system`` kind
      (too noisy by default; opt-in via prefs) and ``whatsapp_welcome``
      (only fires after opt-in, never via email).
    - **whatsapp** off for everything EXCEPT ``whatsapp_welcome`` (which
      fires as the welcome handshake immediately after opt-in capture).

    These defaults are the locked-decision A2/A3 expression — they match
    the truths block of the plan and the spec in
    ``.planning/phases/09-identity-engagement-polish/09-RESEARCH.md``.
    """
    return NotificationPreferences(
        channels=ChannelMatrix(in_app=True, email=True, whatsapp=False),
        kinds={
            "mention": ChannelMatrix(in_app=True, email=True, whatsapp=False),
            "share": ChannelMatrix(in_app=True, email=True, whatsapp=False),
            "agent_pending_write": ChannelMatrix(
                in_app=True, email=True, whatsapp=False
            ),
            "invitation": ChannelMatrix(in_app=True, email=True, whatsapp=False),
            "system": ChannelMatrix(in_app=True, email=False, whatsapp=False),
            "whatsapp_welcome": ChannelMatrix(in_app=False, email=False, whatsapp=True),
            "entry_update": ChannelMatrix(in_app=True, email=True, whatsapp=False),
        },
        whatsapp=WhatsappPrefs(),
    )
