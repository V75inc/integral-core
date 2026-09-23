"""Channel-agnostic renderers for ``StagedChange`` envelopes.

Why this exists
---------------

The staging primitive (``app/agentive/staging.py``) is channel-agnostic
at the data layer: every ``prepare_*`` skill returns a structured
``StagedChange`` envelope and any caller can flip it through the
``bless_token`` / ``revoke_token`` REST endpoints. The assistant-ui
web surface (``frontend/src/features/ai-chat/staging/``) renders that
envelope as an inline approval card with Approve / Reject buttons.

Other channels — SMS, WhatsApp, Slack, voice, email, plain HTTP API
clients — don't have access to the assistant-ui card. They need to:

  1. Present the staged change to the user in their native medium
     (text + ordinal references for SMS, Block Kit buttons for Slack,
     TTS prompts for voice, links for email).
  2. Capture the user's approval / rejection back into a
     ``bless_token`` / ``revoke_token`` call.

This module is the single source of truth for (1). It produces:

  * ``text_prompt`` — channel-formatted prose suitable for any text
    medium. Includes the staged-change summary, an ordinal index
    (for disambiguating when multiple cards are pending), and a
    keyword cue ("Reply YES / NO" for SMS, "say approve / reject"
    for voice, etc.).
  * ``action_links`` — structured list of approve / reject actions,
    each carrying the staging token. Channel adapters convert these
    into native affordances (buttons, links, slash commands).

Channel adapters (per-channel native binding code that lives outside
this repo for connectors we don't ship in-process) call into this
module once per StagedChange event they observe, render the result,
collect the user's reply, then POST to the staging endpoints.

What this module is NOT
-----------------------

* Not a parser. ``staging_renderers`` only renders OUT. See
  ``approval_intent.py`` for parsing the user's text reply IN.
* Not transport-aware. It does not call HTTP, WebSocket, or any
  channel-specific API. It returns plain dicts; the adapter handles
  delivery.
* Not channel-discriminating beyond the named hints. Adapters know
  their own channel; they pass the hint and get back the right
  rendering. Unknown hints fall back to the generic ``text`` channel
  (works for any plain-text medium).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Literal, Optional, TypedDict

from app.agentive.staging import StagedChange

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Channel identifiers
# ---------------------------------------------------------------------------

# Channels we currently know about. Adapters supply one of these to
# ``render_for_channel`` to tune the prompt + action wording.
#
# * ``text``    — generic plain-text fallback. Works for any text
#                 channel without specific tuning.
# * ``sms``     — SMS / WhatsApp / iMessage. Short, single-line
#                 prompts; numeric ordinals; "YES" / "NO" keywords.
# * ``slack``   — Slack messages. Markdown formatting permitted;
#                 action_links assume Block Kit buttons on the
#                 receiving side.
# * ``voice``   — IVR / phone-bot. Spoken-style phrasing; no
#                 markdown; spelled-out instructions ("say
#                 approve").
# * ``email``   — Email body. HTML / multiline OK; action_links
#                 are URLs the recipient clicks.
# * ``mcp``     — MCP server / structured API client. Minimal
#                 prose, full action_links structure for the
#                 client to render its own UI.
ChannelHint = Literal["text", "sms", "slack", "voice", "email", "mcp"]


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


class ActionLink(TypedDict):
    """One approve / reject / undo action on a staged change.

    Adapters consume this to build the channel-native affordance
    (button, link, slash command, hotkey, etc). The shape is stable —
    do not rename fields without bumping a consumer-facing version
    note.
    """

    label: str
    # ``kind`` is the staging primitive verb: bless / revoke. Adapters
    # route this to POST /api/agentive/staging/bless-token or
    # POST /api/agentive/staging/revoke-token respectively.
    kind: Literal["bless", "revoke"]
    # Pass-through autonomy hint for bless calls. ``single`` is the
    # default (one-shot approval); ``session`` adds the kind to the
    # user's autonomy grants for the remainder of the session.
    autonomy: Optional[Literal["single", "session"]]
    # Opaque token. Adapters MUST include this as the bless/revoke
    # body's ``token`` field.
    token: str
    # Suggested keyword the user might say / type to trigger this
    # action via text. Adapters that parse free-text replies (see
    # ``approval_intent.py``) match against this. Lowercase, no
    # punctuation.
    keywords: List[str]


class RenderedStagedChange(TypedDict):
    """One full render of a StagedChange for one channel.

    ``text_prompt`` is the prose the adapter shows the user (already
    formatted for the named channel). ``action_links`` is the list of
    actions the user can take. ``token`` is included at the top level
    too as a convenience for adapters that route by token.
    """

    token: str
    summary: str
    text_prompt: str
    action_links: List[ActionLink]
    # Echo of the input channel hint, so adapters that pass through
    # this structure to logs / observers can identify which rendering
    # they're looking at.
    channel: ChannelHint


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _strip_summary_prefix(summary: str) -> str:
    """Strip the auto-generated ``"File content as ..."`` prefix.

    Skill ``prepare_*`` tools attach this to ``StagedChange.summary``.

    The prefix is useful in the assistant-ui card chrome (where the
    card frame already implies "this is a staged change"), but reads
    awkwardly in plain text ("File content as 'X' in Y" vs simply
    "File X in Y"). The shorter form reads more naturally.

    Falls through unchanged for non-file-content kinds (create_track,
    save_view, modify_operational_model.*, etc.) — their summary is already
    plain enough.
    """
    if not summary:
        return ""
    s = summary.strip()
    # Match patterns like:
    #   File content as "Title" in TrackName (Post)
    #   File content as "Title" in TrackName
    # Drop the leading ``File content as `` and the trailing
    # parenthesised entry-type hint. Keep the title + track.
    prefix = "File content as "
    if s.startswith(prefix):
        s = s[len(prefix) :]
    # Drop trailing entry-type parenthetical for compact text.
    paren = s.rfind(" (")
    if paren > 0 and s.endswith(")"):
        s = s[:paren]
    return s


def _approval_keywords_for(channel: ChannelHint) -> List[str]:
    """Approve-keyword list per channel.

    Conservative whitelist — keep these distinct from common
    conversational openers so approval-intent parsing has clean
    signal.
    """
    base = ["yes", "approve", "ok", "ok approve", "go", "do it"]
    if channel == "voice":
        # Voice keyword lists are matched against ASR output, which
        # is noisier. Include a few extra forms.
        return base + ["confirm", "proceed", "approved"]
    if channel == "sms":
        return base + ["y"]
    return base


def _rejection_keywords_for(channel: ChannelHint) -> List[str]:
    base = ["no", "reject", "cancel", "stop", "don't"]
    if channel == "voice":
        return base + ["denied", "decline"]
    if channel == "sms":
        return base + ["n"]
    return base


def _autonomy_keywords_for(channel: ChannelHint) -> List[str]:
    """Keywords that map to ``bless`` with ``autonomy=session``.

    ``"always"`` / ``"all"`` are common SMS shorthand for "do this
    kind without asking next time". Voice and Slack use the same
    pattern. Email channels typically don't expose autonomy as a
    text-typed action — adapter UIs render an explicit button.
    """
    return ["always", "all", "auto", "auto allow", "always approve"]


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------


def render_for_channel(
    sc: StagedChange,
    *,
    channel: ChannelHint = "text",
    ordinal: Optional[int] = None,
    approve_url_template: Optional[str] = None,
    reject_url_template: Optional[str] = None,
) -> RenderedStagedChange:
    """Render one ``StagedChange`` envelope for the named channel.

    Args:
        sc: The StagedChange to render. Read-only; not mutated.
        channel: Channel hint. See ``ChannelHint``. Defaults to
            ``text`` (works for any plain-text medium).
        ordinal: 1-based position in the user's pending-card queue.
            Included in the text prompt when set ("[1/3]") so users
            with multiple pending cards can disambiguate via
            ordinal references in their text reply ("approve 2").
        approve_url_template / reject_url_template: Optional URL
            templates with ``{token}`` placeholder. When set, the
            corresponding ``ActionLink`` carries the substituted URL
            in its ``label`` for adapters that want clickable links
            (typically email). Leave ``None`` for adapters that
            handle the call themselves (SMS / voice / Slack).

    Returns:
        ``RenderedStagedChange`` — plain dict with ``text_prompt``
        and ``action_links``. Stable shape.
    """
    summary = _strip_summary_prefix(sc.summary)
    ordinal_tag = f"[{ordinal}] " if ordinal else ""
    base_prefix = (
        f"{ordinal_tag}Staged: {summary}."
        if summary
        else f"{ordinal_tag}Staged change."
    )

    if channel == "sms":
        text_prompt = (
            f"{base_prefix} "
            f"Reply YES to approve, NO to reject"
            f"{f', ALWAYS to auto-allow this kind' if sc.kind else ''}."
        )
    elif channel == "voice":
        text_prompt = (
            f"{base_prefix} "
            f"Say 'approve' to file it, 'reject' to discard, "
            f"or 'always' to auto-allow future {sc.kind.replace('_', ' ')} requests."
        )
    elif channel == "slack":
        text_prompt = (
            f"{base_prefix}\n"
            f"Approve to commit, Reject to discard, or "
            f"Approve & auto-allow to skip future prompts for this kind."
        )
    elif channel == "email":
        approve_url = (
            approve_url_template.format(token=sc.token)
            if approve_url_template
            else f"/staging/approve?token={sc.token}"
        )
        reject_url = (
            reject_url_template.format(token=sc.token)
            if reject_url_template
            else f"/staging/reject?token={sc.token}"
        )
        text_prompt = (
            f"{base_prefix}\n\n" f"Approve: {approve_url}\n" f"Reject:  {reject_url}\n"
        )
    elif channel == "mcp":
        # MCP / structured-API clients render their own UI. Keep the
        # text minimal — the action_links carry the actionable data.
        text_prompt = base_prefix
    else:
        # Generic text fallback.
        text_prompt = (
            f"{base_prefix} " f"Reply 'approve' to commit, 'reject' to discard."
        )

    action_links: List[ActionLink] = [
        {
            "label": "Approve",
            "kind": "bless",
            "autonomy": "single",
            "token": sc.token,
            "keywords": _approval_keywords_for(channel),
        },
        {
            "label": "Approve & auto-allow",
            "kind": "bless",
            "autonomy": "session",
            "token": sc.token,
            "keywords": _autonomy_keywords_for(channel),
        },
        {
            "label": "Reject",
            "kind": "revoke",
            "autonomy": None,
            "token": sc.token,
            "keywords": _rejection_keywords_for(channel),
        },
    ]

    return {
        "token": sc.token,
        "summary": summary or sc.summary,
        "text_prompt": text_prompt,
        "action_links": action_links,
        "channel": channel,
    }


def render_pending_list(
    pending: List[StagedChange],
    *,
    channel: ChannelHint = "text",
    max_render: int = 10,
    approve_url_template: Optional[str] = None,
    reject_url_template: Optional[str] = None,
) -> Dict[str, Any]:
    """Render a list of pending staged changes as one combined prompt.

    Used when a connector wakes a session and wants to surface every
    unresolved card in one message ("You have 3 pending: [1] Foo,
    [2] Bar, [3] Baz. Reply '1 approve' / '2 reject' ...").

    Caps at ``max_render`` to keep the message size bounded on
    constrained channels (SMS is the prime concern at ~1600 chars).
    Anything past the cap is summarized as "...and N more".
    """
    if not pending:
        return {
            "text_prompt": "",
            "items": [],
            "channel": channel,
        }

    visible = pending[:max_render]
    overflow = max(0, len(pending) - max_render)

    items: List[RenderedStagedChange] = [
        render_for_channel(
            sc,
            channel=channel,
            ordinal=i + 1,
            approve_url_template=approve_url_template,
            reject_url_template=reject_url_template,
        )
        for i, sc in enumerate(visible)
    ]

    lines: List[str] = [f"You have {len(pending)} staged change(s) awaiting approval:"]
    lines.extend(item["text_prompt"] for item in items)
    if overflow > 0:
        lines.append(f"...and {overflow} more (use the web app to review).")
    lines.append("")
    if channel == "sms":
        lines.append("Reply with the number and YES/NO (e.g. '1 yes', '2 no').")
    elif channel == "voice":
        lines.append("Say the number and 'approve' or 'reject'.")
    else:
        lines.append(
            "Reply with '<n> approve' or '<n> reject' to act on a specific item."
        )

    return {
        "text_prompt": "\n\n".join(lines),
        "items": items,
        "channel": channel,
        "total": len(pending),
        "shown": len(visible),
        "overflow": overflow,
    }
