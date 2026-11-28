"""Public-profile admission for WhatsApp senders who are not linked members.

A verified member stays on the native thread. An unknown sender reaches at
most one tool-free published profile on this line. The phone number is not a
user id, and this module does not open native voice or staged writes.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from app.agentive.services.web_chat_profile_inference import (
    WebChatModelInput,
    bound_public_exchanges,
    clean_public_reply,
    public_text_model_input,
)
from app.api.errors import ResourceConflictError
from app.connectors.channel_packages import (
    ChannelInstallation,
    ChannelProfileConfiguration,
    ChannelProfileOption,
)

logger = logging.getLogger(__name__)

LINK_NOTICE = "Link your number in Integral settings"
UNAVAILABLE_NOTICE = "This number can't start a chat right now."
_HISTORY_LIMIT = 8


class PublicTextUnavailable(RuntimeError):
    """The tool-free model port could not answer. No provider text is included."""


@dataclass(frozen=True)
class PublicLineGrant:
    """One workspace grant whose public profile is still eligible and live."""

    workspace_id: str
    grant_id: str
    revision: int
    profile_key: str
    published_version: int
    persona: str
    principal_id: str


def visitor_session_key(grant_id: str, revision: int, e164: str) -> str:
    """Hash the grant, its revision, and the sender. The phone is not the key."""
    material = f"{grant_id}:{revision}:{e164}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def choose_public_grant(
    grants: list[PublicLineGrant],
) -> PublicLineGrant | None:
    """Return the only live grant, or None when there is not exactly one."""
    if len(grants) != 1:
        return None
    return grants[0]


async def public_profile_is_live(workspace_id: str, profile_key: str) -> bool:
    """True when this key is still a tool-free public profile with a live identity."""
    grant = await _live_grant(workspace_id, "", 0, profile_key)
    return grant is not None


async def live_public_profile_grants(binding) -> list[PublicLineGrant]:
    """Enabled grants on this line whose public profile is currently admissible."""
    from app.agentive.edges import USES_WHATSAPP_LINE
    from app.agentive.services.whatsapp_channel_binding import workspace_line_grant

    if not getattr(binding, "enabled", False):
        return []
    workspaces = await binding.nodes(
        edge=[USES_WHATSAPP_LINE],
        direction="in",
        node=["Workspace"],
        limit=51,
    )
    if len(workspaces) > 50:
        return []
    found: list[PublicLineGrant] = []
    for workspace in workspaces:
        grant = await workspace_line_grant(workspace.id, binding.id)
        if grant is None or not getattr(grant, "enabled", False):
            continue
        key = (getattr(grant, "public_profile_key", "") or "").strip()
        if not key:
            continue
        live = await _live_grant(
            workspace.id,
            grant.id,
            int(getattr(grant, "revision", 0) or 0),
            key,
        )
        if live is not None:
            found.append(live)
    return found


async def describe_line_profile(
    workspace_id: str, current: ChannelInstallation
) -> ChannelProfileConfiguration:
    """Eligible public names for this disabled or enabled grant. No persona."""
    from app.agentive.services import profile_management
    from app.agentive.services.public_web_chat import eligible_publication
    from app.agentive.services.whatsapp_channel_binding import (
        binding_for_workspace,
        workspace_line_epoch,
        workspace_line_grant,
    )

    binding = await binding_for_workspace(workspace_id)
    grant = await workspace_line_grant(workspace_id, binding.id) if binding else None
    if (
        grant is None
        or grant.id != current.provision_id
        or workspace_line_epoch(binding, grant) != current.route_epoch
    ):
        raise ResourceConflictError("WhatsApp grant changed")
    found = await profile_management.record(workspace_id)
    revision = found.revision if found is not None else 0
    profiles = found.profiles if found is not None else {}
    eligible: list[ChannelProfileOption] = []
    selected_version = None
    selected = (getattr(grant, "public_profile_key", "") or "").strip() or None
    for key in profiles:
        published = eligible_publication(profiles, key)
        if published is None:
            continue
        eligible.append(ChannelProfileOption(key, str(published.get("name") or key)))
        if key == selected:
            selected_version = published["version"]
    if selected_version is not None and selected is None:
        selected_version = None
    return ChannelProfileConfiguration(
        revision,
        selected,
        tuple(sorted(eligible, key=lambda item: (item.name.casefold(), item.key))),
        not current.enabled,
        published_version=selected_version,
    )


async def assign_line_profile(
    actor_id: str,
    workspace_id: str,
    current: ChannelInstallation,
    expected_profile_revision: int,
    profile_key: str | None,
) -> None:
    """Delegate the edge write. The caller has already rejected an enabled line."""
    from app.agentive.services.whatsapp_channel_binding import (
        assign_workspace_line_public_profile,
    )

    await assign_workspace_line_public_profile(
        actor_id,
        workspace_id,
        current.provision_id,
        current.route_epoch,
        expected_profile_revision,
        profile_key,
    )


async def unknown_sender_reply(
    binding,
    sender: str,
    text: str,
    *,
    event_id: str,
    complete=None,
) -> str:
    """Reply for a sender with no verified identity. Members never reach this."""
    grants = await live_public_profile_grants(binding)
    if not grants:
        return LINK_NOTICE
    chosen = choose_public_grant(grants)
    if chosen is None:
        return UNAVAILABLE_NOTICE
    try:
        return await _reply_with_profile(
            binding, chosen, sender, text, event_id=event_id, complete=complete
        )
    except Exception:
        logger.warning("whatsapp public profile reply failed")
        return UNAVAILABLE_NOTICE


async def _live_grant(
    workspace_id: str, grant_id: str, revision: int, profile_key: str
) -> PublicLineGrant | None:
    from app.agentive.services import profile_management
    from app.agentive.services.profile_principals import current_principal
    from app.agentive.services.public_web_chat import eligible_publication

    record = await profile_management.record(workspace_id)
    profiles = record.profiles if record is not None else {}
    if not isinstance(profiles, dict):
        return None
    published = eligible_publication(profiles, profile_key)
    if published is None or record is None:
        return None
    version = published["version"]
    identity = await profile_management.live_public_service_identity(
        record, profile_key, version
    )
    if identity is None:
        return None
    principal = await current_principal(record, profile_key, profiles.get(profile_key))
    if principal is None:
        return None
    return PublicLineGrant(
        workspace_id,
        grant_id,
        revision,
        profile_key,
        version,
        str(published.get("persona") or ""),
        principal.id,
    )


def _sender_e164(sender: str) -> str:
    from app.agentive.services.channel_identity import normalize_whatsapp_e164

    raw = (sender or "").strip()
    if raw and not raw.startswith("+"):
        raw = f"+{raw}"
    return normalize_whatsapp_e164(raw)


async def _history(session_key: str) -> tuple[tuple[str, str], ...]:
    from app.models.whatsapp_visitor import WhatsAppVisitorExchange

    rows = await WhatsAppVisitorExchange.find(session_key=session_key)
    rows = sorted(rows, key=lambda row: getattr(row, "created_at", "") or "")
    pairs = []
    for row in rows[-_HISTORY_LIMIT:]:
        visitor = getattr(row, "visitor_text", "") or ""
        assistant = getattr(row, "assistant_text", "") or ""
        if visitor.strip() and assistant.strip():
            pairs.append((visitor, assistant))
    return tuple(pairs)


async def _store_exchange(
    session_key: str, event_id: str, visitor_text: str, assistant_text: str
) -> None:
    from app.models.whatsapp_visitor import WhatsAppVisitorExchange
    from app.utils.time import utc_now_iso

    existing = await WhatsAppVisitorExchange.find(
        session_key=session_key, event_id=event_id
    )
    if existing:
        return
    await WhatsAppVisitorExchange.create(
        session_key=session_key,
        event_id=event_id,
        visitor_text=visitor_text,
        assistant_text=assistant_text,
        created_at=utc_now_iso(),
    )


async def public_call_grant(binding, workspace_id: str) -> PublicLineGrant | None:
    """The only live public profile when this line can still take a call.

    The profile is re-read before the result is returned. A member principal
    is not selected here.
    """
    workspace = (workspace_id or "").strip()
    if not workspace or not getattr(binding, "enabled", False):
        return None
    chosen = choose_public_grant(await live_public_profile_grants(binding))
    if chosen is None or chosen.workspace_id != workspace:
        return None
    if not await _grant_still_only(binding, chosen):
        return None
    from app.agentive.services.whatsapp_channel_binding import workspace_line_grant

    edge = await workspace_line_grant(workspace, getattr(binding, "id", ""))
    if (
        edge is None
        or not getattr(edge, "enabled", False)
        or not getattr(edge, "calling_enabled", False)
    ):
        return None
    return chosen


async def _grant_still_only(binding, grant: PublicLineGrant) -> bool:
    """True when this grant is still the only live public profile on the line."""
    current = await live_public_profile_grants(binding)
    chosen = choose_public_grant(current)
    return (
        chosen is not None
        and chosen.grant_id == grant.grant_id
        and chosen.revision == grant.revision
        and chosen.profile_key == grant.profile_key
        and chosen.published_version == grant.published_version
    )


async def _reply_with_profile(
    binding,
    grant: PublicLineGrant,
    sender: str,
    text: str,
    *,
    event_id: str,
    complete=None,
) -> str:
    e164 = _sender_e164(sender)
    if not e164 or not (event_id or "").strip():
        return UNAVAILABLE_NOTICE
    if not await _grant_still_only(binding, grant):
        return UNAVAILABLE_NOTICE
    session_key = visitor_session_key(grant.grant_id, grant.revision, e164)
    try:
        prompt = public_text_model_input(
            grant.persona,
            bound_public_exchanges(await _history(session_key)),
            text,
        )
    except ValueError:
        return UNAVAILABLE_NOTICE
    if not await _grant_still_only(binding, grant):
        return UNAVAILABLE_NOTICE
    if complete is None:
        reply = await _complete_public_text(grant, session_key, prompt)
    else:
        reply = await complete(prompt)
    reply = clean_public_reply(reply or "")
    if not reply:
        return UNAVAILABLE_NOTICE
    if not await _grant_still_only(binding, grant):
        return UNAVAILABLE_NOTICE
    await _store_exchange(session_key, event_id.strip(), prompt.visitor_text, reply)
    return reply


async def _complete_public_text(
    grant: PublicLineGrant, session_key: str, prompt: WebChatModelInput
) -> str:
    """Run one plain-text completion. The agent is constructed with no tools."""
    import json
    import uuid

    from app.agentive.harness.contracts import HarnessExecutionScope
    from app.agentive.harness.litellm_model import build_litellm_sdk_model
    from app.agentive.harness.model_route import resolve_native_model_route
    from app.agentive.harness.model_routing import resolve_turn_routes
    from app.agentive.harness.pydantic_ai_compat import Agent, UsageLimits

    try:
        plan = await resolve_turn_routes(
            workspace_id=grant.workspace_id,
            resolver=resolve_native_model_route,
        )
        route = plan.light
        scope = HarnessExecutionScope(
            tenant_id=grant.workspace_id,
            principal_id=grant.principal_id,
            workspace_id=grant.workspace_id,
            thread_id=session_key,
            session_id=session_key,
            run_id=uuid.uuid4().hex,
            permission_revision="public-whatsapp",
            capability_version="public-text",
            connection_scope_key=session_key,
            connection_audience="public",
        )

        async def observe(_observation) -> None:
            return None

        model = build_litellm_sdk_model(
            route=route,
            scope=scope,
            observer=observe,
            timeout_seconds=30,
            idle_timeout_seconds=15,
            max_attempts=1,
        )
        instructions = prompt.instructions
        if prompt.persona:
            instructions = f"{instructions}\n\nPublic persona:\n{prompt.persona}"
        payload = {
            "history": [list(pair) for pair in prompt.history],
            "message": prompt.visitor_text,
        }
        try:
            agent = Agent(model, retries=0, instructions=instructions)
            result = await agent.run(
                json.dumps(payload),
                model_settings={"max_tokens": 512},
                usage_limits=UsageLimits(request_limit=1),
            )
        finally:
            await model.client.close()
        output = str(getattr(result, "output", "") or "").strip()
        if not output:
            raise PublicTextUnavailable("empty public reply")
        return output
    except PublicTextUnavailable:
        raise
    except Exception as exc:
        raise PublicTextUnavailable("public text model failed") from exc
